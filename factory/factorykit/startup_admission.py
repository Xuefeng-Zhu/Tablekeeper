"""One owned startup barrier; waiting never acknowledges or admits an SDK event.

BAND may already have claimed a queued message when its adapter waits here.
This barrier controls factory turn reservation and provider execution only.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import tempfile
import time

from .runtime import GateError, fingerprint, state_dir


def _need(condition, reason):
    if not condition:
        raise GateError('Startup admission blocked: ' + reason)


def binding(config, mode, token):
    return {'configuration_sha256': fingerprint(config), 'mode': mode,
            'room_id': config['band'][mode + '_room_id'],
            'owner_sha256': hashlib.sha256(token.encode()).hexdigest()}


def paths(config, bound):
    root = state_dir(config) / 'admission'
    _need(not any(p.is_symlink() for p in (root, *root.parents)), 'symlink path')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.stat()
    _need(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.getuid(), 'private directory required')
    base = root / bound['owner_sha256']
    return base.with_suffix('.json'), base.with_suffix('.claim.json'), base.with_suffix('.request.json')


def read(path):
    _need(not path.is_symlink(), 'symlink evidence')
    info = path.stat()
    _need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
          and stat.S_IMODE(info.st_mode) == 0o600, 'private evidence required')
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        raise GateError('Startup admission blocked: unreadable evidence') from None


def write(path, value, *, exclusive=False):
    _need(not path.is_symlink(), 'symlink evidence')
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, sort_keys=True, indent=2)
            output.write('\n'); output.flush(); os.fsync(output.fileno())
        if exclusive:
            # Link publishes complete durable bytes with exclusive creation.
            # Readers never see a partially written one-use request.
            os.link(name, path)
        else:
            os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def request_release(config, record):
    """Publish one complete request after the caller verified live ownership."""
    _need(record.get('mode') == 'rehearsal' and record.get('config_sha256') == fingerprint(config)
          and record.get('status') == 'ready_held', 'exact held rehearsal owner required')
    bound = binding(config, 'rehearsal', record['token'])
    path, claim, request = paths(config, bound)
    state = read(path)
    _need(state.get('binding') == bound and state.get('state') == 'ready_held'
          and state.get('hold_requested') is True and state.get('warm_ready') is True,
          'current warm-ready evidence required')
    _need(not claim.exists() and not request.exists(), 'release already claimed; inspect its outcome')
    value = {'schema_version': 1, 'binding': bound, 'claimed_epoch': time.time(),
             'parent_pid': record['parent']['pid'],
             'parent_create_time': record['parent']['created']}
    # Claim survives a crash before request publication: no silent second try.
    write(claim, value, exclusive=True)
    write(request, value, exclusive=True)
    return path


class AdmissionController:
    def __init__(self, config, mode, token, stop, check, *, hold=False,
                 budget_deadline=None, clock=time.time, monotonic=time.monotonic):
        _need(not hold or mode == 'rehearsal', 'manual hold is rehearsal only')
        self.binding = binding(config, mode, token)
        self.path, self.claim, self.request = paths(config, self.binding)
        self.stop, self.check = stop, check
        self.clock, self.monotonic = clock, monotonic
        now = clock()
        startup_seconds, operator_seconds = 45 * 7 + 10, 60 if hold else 0
        budget_deadline = now + startup_seconds + operator_seconds if budget_deadline is None else budget_deadline
        _need(type(budget_deadline) in (int, float) and math.isfinite(budget_deadline) and budget_deadline > now,
              'approved startup window expired')
        self.wait_allowance = min(startup_seconds + operator_seconds, budget_deadline - now)
        self.latest_deadline = now + self.wait_allowance
        self.deadline = min(now + startup_seconds, self.latest_deadline)
        self.monotonic_deadline = monotonic() + self.deadline - now
        self.lock, self.opened = asyncio.Lock(), asyncio.Event()
        self.state = {'schema_version': 1, 'binding': self.binding, 'state': 'connecting',
            'hold_requested': hold, 'warm_ready': False, 'waiting_callbacks': 0,
            'factory_admissions': 0, 'sdk_processing_claims_possible': True,
            'release_outcome': None, 'startup_deadline_epoch': self.deadline,
            'wait_deadline_epoch': self.deadline, 'latest_wait_deadline_epoch': self.latest_deadline,
            'operator_hold_seconds': operator_seconds}
        write(self.path, self.state, exclusive=True)

    def _save(self):
        write(self.path, self.state)

    def _retained(self):
        _need(read(self.path) == self.state, 'admission evidence changed')

    def _stopped(self):
        if self.state['state'] != 'open' and (self.clock() >= self.deadline or self.monotonic() >= self.monotonic_deadline):
            self.state['wait_expired'] = True
            self.stop.set()
        if self.stop.is_set() or self.state['state'] == 'stopped':
            raise asyncio.CancelledError('Factory admission stopped before reservation')

    async def ready(self):
        async with self.lock:
            self._retained(); self._stopped()
            _need(self.state['state'] == 'connecting', 'startup already completed')
            proof = await self.check(True)
            self._stopped()
            if self.state['hold_requested']:
                self.deadline = min(self.latest_deadline, self.clock() + 60)
                self.monotonic_deadline = min(self.monotonic_deadline + 60,
                                              self.monotonic() + self.deadline - self.clock())
                self.state['wait_deadline_epoch'] = self.deadline
            self.state.update(warm_ready=True, warm_ready_proof=proof,
                              state='ready_held' if self.state['hold_requested'] else 'open')
            self._save()
            if self.state['state'] == 'open':
                self.opened.set()

    async def poll_release(self, parent):
        self._stopped()
        if not self.request.exists():
            return
        async with self.lock:
            self._retained(); self._stopped()
            if self.state['release_outcome'] is not None:
                return
            try:
                _need(self.state['state'] == 'ready_held' and self.state['warm_ready'], 'release requires warm-ready hold')
                value = read(self.request)
                _need(value == read(self.claim) and value.get('binding') == self.binding
                      and value.get('parent_pid') == parent.get('pid')
                      and value.get('parent_create_time') == parent.get('created'), 'release ownership changed')
                proof = await self.check(False)
                self._stopped()
                self.state.update(state='open', release_outcome='released', release_proof=proof)
                self._save()
                self.opened.set()
            except BaseException:
                self.state.update(state='stopped', release_outcome='blocked')
                self.stop.set(); self._save()
                raise

    async def wait(self):
        """Wait before taking a ledger semaphore slot or creating a turn."""
        self._stopped()
        self.state['waiting_callbacks'] += 1
        self._save()
        opened = asyncio.create_task(self.opened.wait())
        stopped = asyncio.create_task(self.stop.wait())
        try:
            while not opened.done() and not stopped.done():
                remaining = min(self.deadline - self.clock(), self.monotonic_deadline - self.monotonic())
                await asyncio.wait((opened, stopped), timeout=min(1, max(0, remaining)),
                                   return_when=asyncio.FIRST_COMPLETED)
                self._stopped()
            self._stopped()
            _need(self.state['state'] == 'open', 'barrier is not open')
        finally:
            original_error = sys.exc_info()[1]
            for task in (opened, stopped):
                task.cancel()
            await asyncio.gather(opened, stopped, return_exceptions=True)
            self.state['waiting_callbacks'] -= 1
            try:
                self._save()
            except Exception:
                self.stop.set()
                # Storage failure must not turn shutdown cancellation into an
                # ordinary SDK failure/receipt transition.
                if not isinstance(original_error, asyncio.CancelledError):
                    raise

    async def before_reserve(self):
        """Recheck after acquiring a semaphore slot, before its synchronous reserve."""
        async with self.lock:
            self._retained(); self._stopped()
            _need(self.state['state'] == 'open', 'barrier is not open')
            await self.check(None)
            self._stopped()

    def reserved(self):
        self.state['factory_admissions'] += 1
        self._save()

    async def close(self):
        async with self.lock:
            self.stop.set()
            self.state['state'] = 'stopped'
            self._save()

    def summary(self):
        return {key: self.state[key] for key in ('state', 'warm_ready', 'hold_requested',
            'waiting_callbacks', 'factory_admissions', 'sdk_processing_claims_possible', 'release_outcome',
            'startup_deadline_epoch', 'wait_deadline_epoch', 'latest_wait_deadline_epoch', 'operator_hold_seconds')}
