"""Explicit pre-admission failure reconciliation, never an automatic retry.

Only a reviewed operator helper calls commit_reconciliation. Runtime observation
merely validates its retained record; it never restores members or clears errors.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from .membership_gap import _need, _moment

DEADLINE_FAILURE = 'original membership deadline expired'
ROSTER_FAILURE = 'authenticated roster read failed'
ELIGIBLE_FAILURES = (DEADLINE_FAILURE, ROSTER_FAILURE)
TERMINAL = 'operator_restored_failed'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def private_read(path, *, optional=False):
    path = Path(path)
    _need(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)), 'unsafe reconciliation path')
    if optional and not path.exists():
        return None
    info = path.stat()
    _need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and
          not stat.S_IMODE(info.st_mode) & 0o077 and info.st_size <= 16*1024*1024,
          'reconciliation evidence must be private regular files')
    return path.read_bytes()


def create_once(path, raw):
    path = Path(path)
    _need(not any(p.is_symlink() for p in (path, *path.parents)), 'unsafe reconciliation path')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def archived_path(path, digest, kind):
    _need(isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), 'invalid reconciliation hash')
    return path.with_name(path.name + '.' + kind + '-' + digest + '.json')


def validate_terminal(observer, episode, index):
    record = episode.get('operator_reconciliation')
    _need(index == 0 and isinstance(record, dict) and episode.get('outcome') == 'FAILED' and
          episode.get('failed_reason') in ELIGIBLE_FAILURES and episode.get('failure_cause') == 'unknown', 'invalid operator terminal failure')
    archive = private_read(archived_path(observer.path, record.get('archive_sha256'), 'failed'))
    plan_raw = private_read(archived_path(observer.path, record.get('plan_sha256'), 'plan'))
    _need(sha(archive) == record['archive_sha256'] and sha(plan_raw) == record['plan_sha256'], 'operator archive hash changed')
    prior, plan = json.loads(archive), json.loads(plan_raw)
    _need(prior.get('schema_version') == 1 and prior.get('binding') == observer.binding and
          prior.get('blocked_reason') == episode['failed_reason'] and len(prior.get('episodes', [])) == 1,
          'operator prior state is not the exact failed first episode')
    original = prior['episodes'][0]
    claim_raw = private_read(observer.path.with_name(observer.path.name + '.operator-restore.claim.json'))
    claim = json.loads(claim_raw)
    _need(sha(claim_raw) == record.get('restore_claim_sha256') and
          claim.get('plan_sha256') == record['plan_sha256'] and claim.get('agent_id') == original.get('agent_id') and
          claim.get('room_id') == observer.binding['room_id'] and claim.get('helper_sha256') == record.get('helper_sha256') and
          claim.get('status') in ('CLAIMED_ONE_RESTORE_NO_RETRY', 'ALREADY_RESTORED_NO_ADD') and
          _moment(claim.get('claimed_epoch')) and claim['claimed_epoch'] >= original['deadline_epoch'],
          'operator restore claim binding invalid')
    _need(original.get('state') == 'absent' and 'restoration' not in original and
          episode == dict(original, state=TERMINAL, outcome='FAILED', failed_reason=prior['blocked_reason'], failure_cause='unknown',
                          operator_reconciliation=record), 'operator reconciliation altered original failure')
    _need(plan.get('schema_version') == 1 and plan.get('configuration_sha256') == observer.binding['configuration_sha256'] and
          plan.get('room_id') == observer.binding['room_id'] and plan.get('agent_id') == original['agent_id'] and
          plan.get('files', {}).get('membership') == record['archive_sha256'] and
          plan.get('failed_reason') == prior['blocked_reason'] and plan.get('failure_cause') == 'unknown' and
          plan.get('guard_requests') == 4 and plan.get('guard_tokens') == 24765 and
          record.get('pre_admission_files') == plan['files'] and record.get('helper_sha256') == plan.get('helper_sha256'),
          'operator reconciliation plan binding invalid')
    proof = record.get('restoration')
    observer._validate_roster_proof(proof, None)
    _need(proof['owner_uuid'] == original['absence']['owner_uuid'] == plan.get('owner_uuid') and
          len(proof['members']) == 8 and any(m['type'] == 'User' for m in proof['members']) and
          proof['observed_epoch'] >= original['deadline_epoch'] and
          _moment(record.get('reconciled_epoch')) and record['reconciled_epoch'] >= proof['observed_epoch'] and
          record.get('automatic_retry') is False and record.get('messages_sent') == 0 and
          record.get('inference_calls') == 0 and record.get('status') == 'FAILED_EPISODE_OPERATOR_RESTORED',
          'operator restoration is not a complete post-failure barrier')


def evidence_paths(config, room):
    from .budgets import budget_ledger_path
    root = Path(config['paths']['runs']) / 'runtime'
    guard = Path(config['runtime']['featherless_budget_guard']['ledger'])
    _need(guard.parent == root and room == config['band']['rehearsal_room_id'], 'reconciliation must use current rehearsal accounting')
    return {'membership': root/f'membership-{room}.json', 'budget': budget_ledger_path(config),
            'guard': guard, 'workflow': root/f'workflow-{room}.json',
            'task_board': root/f'task-board-{room}.json', 'execution_events': root/'execution-events.jsonl',
            'owner': root/'owner.json'}


def no_admission_snapshot(config, room):
    """Read only. Paid permission turns stay fully counted in unchanged ledgers."""
    from .budgets import persisted_budget_blockers
    from .runtime import is_owned
    paths = evidence_paths(config, room)
    raw = {key: private_read(path, optional=key in ('task_board', 'execution_events')) for key, path in paths.items()}
    data = {key: json.loads(value) for key, value in raw.items() if value is not None and key != 'execution_events'}
    owner = data['owner']
    _need(owner.get('status') == 'stopped' and not is_owned(owner.get('parent', {}), owner.get('token')) and
          not any(is_owned(item) for item in owner.get('children', [])), 'supervisor or child is not confirmed stopped')
    workflow = data['workflow']
    _need(workflow.get('scope', {}).get('room_id') == room and
          all(workflow.get(k) == {} for k in ('turns', 'deliveries', 'events', 'incidents', 'notices')),
          'workflow already admitted work; operator reconciliation prohibited')
    board = data.get('task_board')
    _need(board is None or (board.get('scope', {}).get('room_id') == room and
          board.get('items') == {} and board.get('messages') == {}), 'task board already contains work')
    _need(raw['execution_events'] in (None, b''), 'execution event evidence already contains work')
    guard = data['guard']
    requests = guard.get('requests', {})
    _need(len(requests) == 4 and all(row.get('status') == 'settled' for row in requests.values()) and
          sum(row.get('prompt_tokens', -1) + row.get('completion_tokens', -1) for row in requests.values()) == 24765,
          'request consumption differs from approved pre-admission baseline')
    _need(not persisted_budget_blockers(config, require_existing=True), 'retained consumption budget blocks reconciliation')
    return {key: sha(value) if value is not None else None for key, value in raw.items()}


def commit_reconciliation(observer, plan_raw, proof, *, helper_sha256, clock):
    """Called only under supervisor/guard ownership locks after live REST readback."""
    prior_raw = private_read(observer.path)
    plan = json.loads(plan_raw)
    _need(no_admission_snapshot(observer.config, observer.binding['room_id']) == plan.get('files'),
          'pre-admission evidence changed before reconciliation')
    _need(sha(prior_raw) == plan['files']['membership'] and observer.state == json.loads(prior_raw),
          'failed membership journal changed before reconciliation')
    _need(observer.state.get('blocked_reason') in ELIGIBLE_FAILURES and observer.state['blocked_reason'] == plan.get('failed_reason') and len(observer.state['episodes']) == 1 and
          observer.state['episodes'][0]['state'] == 'absent' and 'restoration' not in observer.state['episodes'][0] and
          observer.binding['max_transitions'] == 2 and helper_sha256 == plan.get('helper_sha256'),
          'only the exact reviewed first pre-admission failure is reconcilable')
    archive_hash, plan_hash = sha(prior_raw), sha(plan_raw)
    for path, content in ((archived_path(observer.path, archive_hash, 'failed'), prior_raw),
                          (archived_path(observer.path, plan_hash, 'plan'), plan_raw)):
        if path.exists():
            _need(private_read(path) == content, 'immutable reconciliation archive changed')
        else:
            create_once(path, content)
    _need(0 <= clock() - proof.get('observed_epoch', -1) <= 30, 'operator restoration observation is stale')
    original = observer.state['episodes'][0]
    record = {'archive_sha256': archive_hash, 'plan_sha256': plan_hash,
              'helper_sha256': helper_sha256, 'pre_admission_files': plan['files'],
              'restore_claim_sha256': sha(private_read(observer.path.with_name(observer.path.name + '.operator-restore.claim.json'))),
              'restoration': proof, 'reconciled_epoch': clock(), 'automatic_retry': False,
              'messages_sent': 0, 'inference_calls': 0, 'status': 'FAILED_EPISODE_OPERATOR_RESTORED'}
    terminal = dict(original, state=TERMINAL, outcome='FAILED', failed_reason=observer.state['blocked_reason'], failure_cause='unknown',
                    operator_reconciliation=record)
    validate_terminal(observer, terminal, 0)
    _need(no_admission_snapshot(observer.config, observer.binding['room_id']) == plan['files'],
          'pre-admission evidence changed before atomic commit')
    observer.state['episodes'][0] = terminal
    observer.state['blocked_reason'] = None
    observer._validate()
    observer._save()
    return {'status': record['status'], 'failed_episodes': 1, 'remaining_episodes': 1,
            'journal_sha256': sha(private_read(observer.path)), 'archive_sha256': archive_hash,
            'plan_sha256': plan_hash, 'messages_sent': 0, 'inference_calls': 0}
