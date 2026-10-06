"""Normal-run membership diagnostics, without a repair deadline or stop policy.

Startup still authenticates all seven agents and the full room roster. Later
local presence uncertainty defers automatic notices, never admits another room
or claims a remote membership outcome. Historical strict journals are untouched.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import time

from .membership_gap import MembershipGapObserver, _need, _uuid
from .runtime import GateError, fingerprint, save_json
from .workflow_runtime import sdk_execution_activity


def strict_membership_recovery(config):
    return config.get('runtime', {}).get('strict_membership_recovery') is True


class AdvisoryMembershipObserver(MembershipGapObserver):
    """Reuse authenticated roster checks, not the strict incident state machine."""
    def __init__(self, path, config, room_id, ledger, *, clock=time.time):
        self.path, self.config, self.ledger, self.clock = Path(path), config, ledger, clock
        self.roster = {seat['agent_id']: seat for seat in config['seats']}
        self.pm = next(seat['agent_id'] for seat in config['seats'] if seat['id'] == 'pm')
        _need(len(self.roster) == len(config['seats']) == 7 and all(_uuid(i) for i in self.roster),
              'exact seven identities required')
        _need(_uuid(room_id) and ledger.room == room_id, 'room binding mismatch')
        self.binding = {'configuration_sha256': fingerprint(config), 'room_id': room_id,
                        'pm_id': self.pm, 'participant_ids': sorted(self.roster)}
        self.lock = asyncio.Lock()
        self._previous = None

    def _scope(self, agents):
        from band import Agent
        from band.platform.link import BandLink
        from band.runtime.platform_runtime import PlatformRuntime
        from band.runtime.runtime import AgentRuntime
        from band.runtime.execution import ExecutionContext
        bound = {}
        for agent in agents:
            _need(type(agent) is Agent and type(agent.runtime) is PlatformRuntime,
                  'unsupported agent scope')
            platform = agent.runtime
            identity, link, runtime = platform.agent_id, platform.link, platform.runtime
            _need(identity in self.roster and identity not in bound and type(link) is BandLink
                  and type(runtime) is AgentRuntime and link.agent_id == runtime.agent_id == identity
                  and runtime.link is link, 'agent identity or link scope changed')
            context = runtime.active_sessions.get(self.binding['room_id'])
            if context is not None:
                _need(type(context) is ExecutionContext and context.link is link
                      and context.room_id == self.binding['room_id'] and context.agent_id == identity,
                      'execution context scope changed')
            bound[identity] = agent
        _need(set(bound) == set(self.roster), 'configured agent coverage changed')
        return bound

    async def full_ready(self, agents):
        async with self.lock:
            self._scope(agents)
            bound, missing, activity = self._local(agents)
            _need(missing is None, 'warm startup requires all seven contexts')
            # A transport read timeout only; it creates no recovery allowance.
            window = {'agent_id': next(i for i in self.roster if i != self.pm),
                      'deadline_epoch': self.clock() + 5.0}
            proof = await self._roster(bound[self.pm], window)
            self._scope(agents)
            _, missing, activity = self._local(agents)
            _need(missing is None and proof['missing_agent_id'] is None and len(proof['members']) == 8,
                  'warm startup requires full eight-member roster')
            self._validate_roster_proof(proof, None)
            _need(not self.ledger.stop.is_set() and not self.ledger.reason(), 'accounting stop is active')
            return dict(activity, membership_gap=None, authenticated_roster=proof)

    async def snapshot(self, agents):
        async with self.lock:
            bound = self._scope(agents)
            try:
                _, missing, activity = self._local(agents)
                if missing is None:
                    status = {'status': 'local_contexts_available', 'unavailable_agent_ids': []}
                else:
                    status = {'status': 'local_presence_unknown', 'unavailable_agent_ids': [missing]}
                    activity['busy'] = True
            except GateError:
                contexts, unavailable = [], []
                for identity, agent in bound.items():
                    try:
                        contexts.extend(sdk_execution_activity([agent], self.binding['room_id'], [identity])['contexts'])
                    except GateError:
                        unavailable.append(identity)
                activity = {'busy': True, 'contexts': contexts}
                status = {'status': 'local_presence_unknown', 'unavailable_agent_ids': sorted(unavailable)}
            if status != self._previous:
                _need(not any(p.is_symlink() for p in (self.path, *self.path.parents)), 'symlink diagnostics refused')
                save_json(self.path, {'schema_version': 1, 'binding': self.binding,
                    'observed_epoch': self.clock(), 'advisory': True, **status,
                    'remote_membership_verified': False, 'automatic_stop': False})
                self._previous = status
            return dict(activity, membership_gap=None,
                        membership_advisory=status if status['status'] != 'local_contexts_available' else None)
