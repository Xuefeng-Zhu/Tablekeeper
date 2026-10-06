"""Read-only, bounded observation of one genuine missing BAND room member.

This observer never removes/adds participants, sends messages, admits turns, or
writes consumption ledgers. Only the existing PM tooling may restore a member.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import tempfile
import time
from uuid import UUID

from .runtime import GateError, fingerprint
from .workflow_runtime import sdk_execution_activity, send_window


def _need(value, reason):
    if not value:
        raise GateError('Membership observation blocked: ' + reason)


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def _moment(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def retained_startup_check(config, mode):
    """Read only: reject retained failure before starting auxiliary processes."""
    from types import SimpleNamespace
    room = config['band'][mode + '_room_id']
    path = Path(config['paths']['runs']) / 'runtime' / f'membership-{room}.json'
    if not path.exists() and not path.is_symlink():
        _need(not path.with_name(path.name + '.second-fixture.claim.json').exists(),
              'second fixture claim has no bound journal')
        return
    observer = MembershipGapObserver(path, config, room, SimpleNamespace(room=room))
    _need(not observer.state['blocked_reason'] and observer._active() is None,
          'warm startup cannot bypass retained or active membership failure')


class MembershipGapObserver:
    def __init__(self, path, config, room_id, ledger, *, clock=time.time):
        self.path, self.ledger, self.clock = Path(path), ledger, clock
        self.config = config
        self.roster = {seat['agent_id']: seat for seat in config['seats']}
        self.pm = next(seat['agent_id'] for seat in config['seats'] if seat['id'] == 'pm')
        _need(len(self.roster) == len(config['seats']) == 7 and all(_uuid(key) for key in self.roster), 'exact seven identities required')
        _need(_uuid(room_id) and ledger.room == room_id, 'room binding mismatch')
        timeout, cap = config['budgets']['ack_timeout_seconds'], min(2, config['budgets']['max_repairs'])
        _need(type(timeout) is int and timeout > 0 and type(cap) is int and cap >= 0, 'invalid finite limits')
        self.binding = {'configuration_sha256': fingerprint(config), 'room_id': room_id,
            'pm_id': self.pm, 'participant_ids': sorted(self.roster),
            'ack_timeout_seconds': timeout, 'max_transitions': cap}
        self.lock = asyncio.Lock()
        _need(self.path.is_absolute(), 'absolute state path required')
        _need(not any(p.is_symlink() for p in (self.path, *self.path.parents)), 'symlink state refused')
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text())
            except (OSError, ValueError):
                raise GateError('Membership observation blocked: unreadable retained state') from None
        else:
            self.state = {'schema_version': 1, 'binding': self.binding, 'episodes': [], 'blocked_reason': None}
            self._save()
        self._validate()

    def _validate(self):
        d = self.state
        _need(isinstance(d, dict) and d.get('schema_version') == 1 and d.get('binding') == self.binding,
              'retained binding changed')
        episodes = d.get('episodes')
        _need(isinstance(episodes, list) and len(episodes) <= self.binding['max_transitions'], 'retained transition cap invalid')
        _need(d.get('blocked_reason') is None or isinstance(d['blocked_reason'], str), 'retained blocker invalid')
        fixture_claim = self.path.with_name(self.path.name + '.second-fixture.claim.json')
        if fixture_claim.exists() or fixture_claim.is_symlink():
            _need(len(episodes) == 2 and 'operator_fixture' in episodes[1],
                  'second fixture claim has no complete bound absence; no implicit restart')
        for index, episode in enumerate(episodes):
            _need(isinstance(episode, dict) and episode.get('agent_id') in self.roster and episode['agent_id'] != self.pm,
                  'retained missing identity invalid')
            _need(_moment(episode.get('first_seen_epoch')) and _moment(episode.get('deadline_epoch')) and
                  episode['deadline_epoch'] == episode['first_seen_epoch'] + self.binding['ack_timeout_seconds'],
                  'retained deadline invalid')
            _need(episode.get('state') in ('verifying_absence', 'absent', 'awaiting_context', 'restored', 'operator_restored_failed'), 'retained episode state invalid')
            _need(index == len(episodes) - 1 or episode['state'] in ('restored', 'operator_restored_failed'), 'multiple retained gaps')
            if episode['state'] != 'verifying_absence':
                self._validate_proof(episode.get('absence'), episode, episode['agent_id'])
            if 'restoration' in episode:
                self._validate_proof(episode['restoration'], episode, None)
            if 'operator_fixture' in episode:
                from .membership_fixture import validate_fixture
                validate_fixture(self, episode, index)
            if episode['state'] == 'operator_restored_failed':
                from .membership_reconcile import validate_terminal
                validate_terminal(self, episode, index)
            if episode['state'] == 'restored':
                _need(_moment(episode.get('restored_epoch')) and episode['restored_epoch'] < episode['deadline_epoch'] and
                      isinstance(episode.get('restoration'), dict), 'retained restoration proof invalid')

    def _validate_proof(self, proof, episode, missing):
        _need(isinstance(proof, dict) and proof.get('room_id') == self.binding['room_id'] and
              proof.get('authenticated_pm_id') == self.pm and _uuid(proof.get('owner_uuid')) and
              proof.get('missing_agent_id') == missing and _moment(proof.get('observed_epoch')) and
              episode['first_seen_epoch'] <= proof['observed_epoch'] < episode['deadline_epoch'],
              'retained roster proof binding invalid')
        self._validate_roster_proof(proof, missing)

    def _validate_roster_proof(self, proof, missing):
        _need(isinstance(proof, dict) and proof.get('room_id') == self.binding['room_id'] and
              proof.get('authenticated_pm_id') == self.pm and _uuid(proof.get('owner_uuid')) and
              proof.get('missing_agent_id') == missing and _moment(proof.get('observed_epoch')),
              'retained roster proof binding invalid')
        members = proof.get('members')
        _need(isinstance(members, list) and all(isinstance(row, dict) for row in members), 'retained roster proof invalid')
        ids = [row.get('id') for row in members]
        _need(all(_uuid(identity) for identity in ids) and len(ids) == len(set(ids)), 'retained roster identities invalid')
        agents = {row['id'] for row in members if row.get('type') == 'Agent'}
        _need(agents == set(self.roster) - ({missing} if missing else set()), 'retained roster coverage invalid')
        _need(all(row.get('status') in ('active', 'inactive') and row.get('role') in ('owner', 'admin', 'member') and
              (row.get('type') == 'Agent' or (row.get('type') == 'User' and row['id'] == proof['owner_uuid'] and row['status'] == 'active'))
              for row in members), 'retained roster member invalid')

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _need(not any(p.is_symlink() for p in (self.path, *self.path.parents)), 'symlink state refused')
        fd, name = tempfile.mkstemp(prefix='.' + self.path.name, dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as output:
                json.dump(self.state, output, indent=2, sort_keys=True)
                output.write('\n'); output.flush(); os.fsync(output.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)

    def _block(self, reason):
        self.state['blocked_reason'] = self.state['blocked_reason'] or reason
        self._save()
        raise GateError('Membership observation blocked: ' + self.state['blocked_reason'])

    def _active(self):
        episodes = self.state['episodes']
        return episodes[-1] if episodes and episodes[-1]['state'] not in ('restored', 'operator_restored_failed') else None

    def _local(self, agents):
        from .membership_liveness import require_live_agents
        from band.runtime.execution import ExecutionContext
        bound = require_live_agents(agents, list(self.roster))
        for agent in bound.values():
            sessions = agent.runtime.runtime.active_sessions
            if self.binding['room_id'] in sessions:
                context = sessions[self.binding['room_id']]
                _need(type(context) is ExecutionContext and context.link is agent.runtime.link,
                      'custom or incorrectly bound SDK context')
        missing = [identity for identity, agent in bound.items()
                   if self.binding['room_id'] not in agent.runtime.runtime.active_sessions]
        _need(len(missing) <= 1 and self.pm not in missing, 'missing PM or multiple contexts')
        present = [agent for identity, agent in bound.items() if identity not in missing]
        # Any existing custom/dead/wrong-room context remains a strict failure.
        activity = sdk_execution_activity(present, self.binding['room_id'], [key for key in self.roster if key not in missing])
        return bound, missing[0] if missing else None, activity

    async def _roster(self, pm_agent, episode):
        room = self.binding['room_id']
        remaining = min(5.0, episode['deadline_epoch'] - self.clock(), send_window(self.ledger))
        _need(remaining > 0 and not self.ledger.stop.is_set() and not self.ledger.reason(), 'approved window expired')
        options = {'max_retries': 0, 'timeout_in_seconds': remaining}
        rest = pm_agent.runtime.link.rest
        try:
            async with asyncio.timeout(remaining):
                me = (await rest.agent_api_identity.get_agent_me(request_options=options)).data
                expected = self.roster[self.pm]
                _need(me.id == self.pm and me.name == expected['display_name'] and
                      me.handle.removeprefix('@') == expected['handle'].removeprefix('@') and _uuid(me.owner_uuid),
                      'authenticated PM identity mismatch')
                header = (await rest.agent_api_chats.get_agent_chat(id=room, request_options=options)).data
                _need(header.id == room, 'authenticated room identity mismatch')
                response = await rest.agent_api_participants.list_agent_chat_participants(chat_id=room, request_options=options)
        except GateError:
            raise
        except Exception:
            raise GateError('Membership observation blocked: authenticated roster read failed') from None
        rows = response.data
        _need(isinstance(rows, list), 'roster response is not a complete list')
        metadata = getattr(response, 'metadata', None)
        if metadata is not None:
            data = metadata if isinstance(metadata, dict) else metadata.model_dump()
            _need(data.get('has_more', False) is False and data.get('total_pages', 1) in (0, 1) and
                  data.get('total_count', len(rows)) == len(rows), 'incomplete roster pagination')
        observed, ids = [], set()
        for row in rows:
            _need(_uuid(row.id) and row.id not in ids, 'duplicate or malformed roster identity')
            ids.add(row.id)
            _need(row.status in ('active', 'inactive') and row.role in ('owner', 'admin', 'member'), 'blocked or unknown member state')
            if row.type == 'Agent':
                _need(row.id in self.roster, 'unconfigured room agent')
                expected = self.roster[row.id]
                _need(row.name == expected['display_name'] and
                      row.handle.removeprefix('@') == expected['handle'].removeprefix('@'), 'roster identity mismatch')
            else:
                _need(row.type == 'User' and row.id == me.owner_uuid and row.status == 'active', 'unexpected human member')
            observed.append({'id': row.id, 'type': row.type, 'role': row.role, 'status': row.status})
        agent_ids = {row['id'] for row in observed if row['type'] == 'Agent'}
        _need(self.pm in agent_ids, 'PM absent from authenticated roster')
        absent = set(self.roster) - agent_ids
        _need(absent in (set(), {episode['agent_id']}), 'different or multiple missing members')
        return {'observed_epoch': self.clock(), 'room_id': room, 'authenticated_pm_id': self.pm,
                'owner_uuid': me.owner_uuid, 'missing_agent_id': episode['agent_id'] if absent else None,
                'members': sorted(observed, key=lambda row: row['id'])}

    async def full_ready(self, agents):
        """Fresh startup proof, without inventing a missing-member episode."""
        async with self.lock:
            self._validate()
            _need(not self.state['blocked_reason'], 'retained failure requires review')
            _need(self._active() is None, 'warm startup requires no active membership gap')
            bound, missing, activity = self._local(agents)
            _need(missing is None, 'warm startup requires all seven contexts')
            # This read window is not a repair allowance and is never persisted
            # as an episode. A missing REST member is rejected at warm startup.
            window = {'agent_id': next(key for key in self.roster if key != self.pm),
                      'deadline_epoch': self.clock() + 5.0}
            proof = await self._roster(bound[self.pm], window)
            _, missing, activity = self._local(agents)
            _need(missing is None and proof['missing_agent_id'] is None
                  and len(proof['members']) == 8, 'warm startup requires full eight-member roster')
            self._validate_roster_proof(proof, None)
            _need(not self.ledger.stop.is_set() and not self.ledger.reason(), 'approved window expired')
            return dict(activity, membership_gap=None, authenticated_roster=proof)

    async def snapshot(self, agents):
        async with self.lock:
            self._validate()
            if self.state['blocked_reason']:
                raise GateError('Membership observation blocked: retained failure requires review')
            try:
                return await self._snapshot(agents)
            except GateError as error:
                # GateError text consists solely of deliberate fixed local codes.
                self._block(str(error).removeprefix('Membership observation blocked: '))
            except Exception:
                self._block('unsupported SDK or evidence state')

    async def _snapshot(self, agents):
        bound, missing, activity = self._local(agents)
        episode = self._active()
        if episode is None and missing is None:
            return dict(activity, membership_gap=None)
        if episode is None:
            _need(len(self.state['episodes']) < self.binding['max_transitions'], 'membership transition cap exhausted')
            start = self.clock()
            if self.state['episodes'] and self.state['episodes'][-1]['state'] == 'operator_restored_failed':
                _need(start > self.state['episodes'][-1]['operator_reconciliation']['restoration']['observed_epoch'],
                      'new absence must follow verified operator restoration')
            episode = {'agent_id': missing, 'first_seen_epoch': start,
                       'deadline_epoch': start + self.binding['ack_timeout_seconds'], 'state': 'verifying_absence'}
            self.state['episodes'].append(episode)
            self._save()  # The first deadline survives REST delay, failure, and restart.
        _need(self.clock() < episode['deadline_epoch'], 'original membership deadline expired')
        _need(self.clock() >= episode['first_seen_epoch'], 'membership clock moved backwards')
        _need(missing in (None, episode['agent_id']), 'missing identity changed during episode')
        proof = await self._roster(bound[self.pm], episode)
        # REST awaits can span SDK removal/recreation. Validate every local context again.
        bound, current_missing, activity = self._local(agents)
        _need(current_missing in (None, episode['agent_id']), 'SDK roster changed during REST observation')
        _need(self.clock() < episode['deadline_epoch'], 'original membership deadline expired')
        _need(self.clock() >= episode['first_seen_epoch'], 'membership clock moved backwards')
        if episode['state'] == 'verifying_absence':
            _need(proof['missing_agent_id'] == episode['agent_id'] and current_missing == episode['agent_id'],
                  'missing context has no authenticated absence proof')
            episode.update(state='absent', absence=proof)
        elif proof['missing_agent_id'] is not None:
            _need(episode['state'] == 'absent' and current_missing == episode['agent_id'],
                  'membership regressed or local context disagrees with absence')
            if 'operator_fixture' in episode:
                episode['sdk_absence_observed'] = True
                episode.setdefault('first_sdk_absence_epoch', self.clock())
        else:
            _need(proof['owner_uuid'] == episode['absence']['owner_uuid'], 'authenticated owner changed during episode')
            episode.setdefault('restoration', proof)
            if current_missing is None:
                episode.update(state='restored', restored_epoch=self.clock(),
                               elapsed_seconds=self.clock() - episode['first_seen_epoch'])
                self._save()
                return dict(activity, membership_gap=None, membership_restored={
                    'agent_id': episode['agent_id'], 'elapsed_seconds': episode['elapsed_seconds'],
                    'transitions_observed': len(self.state['episodes']),
                    'absence_source': episode.get('absence_source', 'sdk_and_authenticated_rest'),
                    'sdk_absence_observed': episode.get('sdk_absence_observed', True)})
            episode['state'] = 'awaiting_context'
        episode['last_observed_epoch'] = self.clock()
        self._save()
        return dict(activity, busy=True, membership_gap={'agent_id': episode['agent_id'],
            'state': episode['state'], 'first_seen_epoch': episode['first_seen_epoch'],
            'deadline_epoch': episode['deadline_epoch'], 'elapsed_seconds': self.clock() - episode['first_seen_epoch'],
            'transitions_observed': len(self.state['episodes']), 'notices_deferred': True})
