"""Private startup phase timings; never records runtime inputs or exceptions."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
from uuid import uuid4

from .common import FactoryError


PHASES = frozenset({
    'startup_begin', 'startup_failed', 'runtime_failed', 'runtime_closed',
    'opencode_server_begin', 'opencode_binary_check_begin',
    'opencode_version_verified', 'opencode_binary_verified',
    'opencode_process_started', 'opencode_route_verified',
    'opencode_seat_ready', 'opencode_all_servers_ready',
    'sdk_agent_start_begin', 'sdk_agent_start_complete',
    'startup_ready', 'admission_held', 'admission_released',
})
_CURRENT = ContextVar('factory_startup_telemetry', default=None)
_SEAT = ContextVar('factory_startup_telemetry_seat', default=None)
_SEAT_ID = re.compile(r'[a-z][a-z0-9_-]{0,63}')


def _need(condition):
    if not condition:
        raise FactoryError('Startup timing journal is unsafe or malformed')


class StartupTelemetry:
    """A new owned journal per supervisor scope; no append to old attempts."""

    def __init__(self, config):
        try:
            self.seats = frozenset(row['id'] for row in config['seats'])
            _need(len(self.seats) == len(config['seats']) and bool(self.seats)
                  and all(isinstance(seat, str) and _SEAT_ID.fullmatch(seat) for seat in self.seats))
            configured = Path(config['paths']['runs'])
            _need(configured.is_absolute() and not configured.is_symlink())
            root = configured.resolve()
            _need(root.is_dir() and root.stat().st_uid == os.getuid())
            parent = root / 'runtime'
            parent.mkdir(mode=0o700, exist_ok=True)
            _need(not parent.is_symlink() and parent.is_dir() and parent.stat().st_uid == os.getuid())
            directory = parent / 'startup-telemetry'
            directory.mkdir(mode=0o700, exist_ok=True)
            info = directory.lstat()
            _need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                  and stat.S_IMODE(info.st_mode) == 0o700)
            self.pid = os.getpid()
            self.path = directory / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
                                     + f'-{self.pid}-' + uuid4().hex + '.jsonl')
            self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(self.fd)
                _need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                      and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
                parent_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            except BaseException:
                os.close(self.fd)
                self.fd = None
                raise
            self.started = time.monotonic()
            self.sequence = 0
            self.ready = False
            self.lock = threading.Lock()
        except (OSError, KeyError, TypeError, ValueError):
            raise FactoryError('Startup timing journal could not be initialized') from None

    def record(self, phase, seat=None):
        # No detail/error/text parameter: source inputs cannot leak through a
        # caller accidentally passing an exception or provider response.
        _need(isinstance(phase, str) and phase in PHASES
              and (seat is None or isinstance(seat, str) and seat in self.seats))
        with self.lock:
            _need(self.fd is not None and os.getpid() == self.pid)
            elapsed = time.monotonic() - self.started
            _need(math.isfinite(elapsed) and elapsed >= 0)
            row = {'schema_version': 1, 'sequence': self.sequence, 'pid': self.pid,
                   'phase': phase, 'seat': seat,
                   'observed_at': datetime.now(timezone.utc).isoformat(),
                   'elapsed_seconds': round(elapsed, 6)}
            raw = (json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n').encode()
            try:
                while raw:
                    count = os.write(self.fd, raw)
                    _need(count > 0)
                    raw = raw[count:]
                os.fsync(self.fd)
            except OSError:
                raise FactoryError('Startup timing journal could not be persisted') from None
            self.sequence += 1
            if phase == 'startup_ready':
                self.ready = True

    def close(self):
        with self.lock:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None


def record_startup(phase, seat=None):
    recorder = _CURRENT.get()
    if recorder is not None:
        recorder.record(phase, _SEAT.get() if seat is None else seat)


@contextmanager
def startup_seat(seat):
    recorder = _CURRENT.get()
    if recorder is not None:
        _need(isinstance(seat, str) and seat in recorder.seats)
    token = _SEAT.set(seat)
    try:
        yield
    finally:
        _SEAT.reset(token)


@contextmanager
def startup_telemetry(config):
    """Call around owned server/agent lifetime; nested helpers inherit context."""
    if _CURRENT.get() is not None:
        yield _CURRENT.get()
        return
    recorder = StartupTelemetry(config)
    current_token = _CURRENT.set(recorder)
    seat_token = _SEAT.set(None)
    try:
        recorder.record('startup_begin')
        try:
            yield recorder
        except BaseException:
            # Keep the original failure/cancellation authoritative if storage
            # also fails during unwinding. Never serialize the exception.
            try:
                recorder.record('runtime_failed' if recorder.ready else 'startup_failed')
            except Exception:
                pass
            raise
        finally:
            try:
                recorder.record('runtime_closed')
            except Exception:
                pass
    finally:
        _SEAT.reset(seat_token)
        _CURRENT.reset(current_token)
        recorder.close()
