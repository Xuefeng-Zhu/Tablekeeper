"""Explicit local-practice terminal claims; never replay or rewrite old failures.

Only a separately reviewed operator manifest admits unrelated future work while
retaining one acknowledged, interrupted handoff as permanently non-replayable.
The existing watchdog must resolve its incident using a new real SDK callback.
"""
from pathlib import Path
import json
import copy

from .common import canonical, digest, FactoryError


def require(value, message):
    if not value:
        raise FactoryError(message)


def referenced(ref):
    path = Path(ref['path'])
    require(path.is_absolute() and not path.is_symlink() and path.is_file(), 'Missing reconciliation evidence')
    require(digest(path) == ref['sha256'], 'Reconciliation evidence changed')
    return json.loads(path.read_bytes())


def load_terminal_reconciliations(config, room, workflow):
    path = Path(config['paths']['runs']) / 'readiness/local-8h-reconciliation.json'
    if not path.exists():
        return {}
    require(not path.is_symlink(), 'Reconciliation manifest is a symlink')
    record = json.loads(path.read_bytes())
    require(config['launch'].get('practice_mode') is True and room == config['band']['rehearsal_room_id'], 'Terminal reconciliation is local-practice only')
    require(record['status'] == 'APPROVED_TERMINAL_NO_REPLAY' and record['configuration_sha256'] == digest(canonical(config)), 'Terminal reconciliation configuration differs')
    require(record['authorization']['user_answer'] == 'Extend 8 hours and resume' and record['authorization']['additional_seconds'] == 28800, 'Missing exact local continuation approval')
    old = referenced(record['original_configuration'])
    expected = copy.deepcopy(old)
    expected['paths']['factory'] = config['paths']['factory']
    for field in ('overall_timeout_seconds', 'stage_timeout_seconds'):
        expected['budgets'][field] += 28800
    require(config == expected, 'Continuation changes exceed the approved clocks and isolated source')
    roster = sorted(s['agent_id'] for s in config['seats'])
    require(record['room_id'] == room and record['participant_ids'] == roster and workflow['scope']['room_id'] == room and workflow['scope']['participant_ids'] == roster, 'Terminal reconciliation room or roster differs')
    original = referenced(record['original_workflow'])
    sdk = referenced(record['sdk_evidence'])
    require(sdk['status'] == 'VERIFIED_READ_ONLY' and sdk['room_id'] == room, 'Missing actual SDK read-only reconciliation proof')
    require(sdk['trigger_sdk_status'] == 'failed' and sdk['receipt_sdk_status'] in ('processing', 'processed'), 'SDK interrupted failure/receipt evidence remains ambiguous')
    require(bool(sdk['evidence']), 'SDK evidence inventory is empty')
    for ref in sdk['evidence']:
        p = Path(ref['path'])
        require(p.is_absolute() and p.is_file() and not p.is_symlink() and digest(p) == ref['sha256'], 'SDK evidence changed')
    require(len(record['entries']) == 1, 'Only the reviewed single interrupted callback is supported')
    result = {}
    for entry in record['entries']:
        seat = next((s for s in config['seats'] if s['id'] == entry['seat']), None)
        require(seat is not None and seat['agent_id'] == sdk['recipient_id'], 'Reconciliation seat identity differs')
        key = entry['turn_id']
        old_turn = original['turns'][key]
        require(workflow['turns'].get(key) == old_turn and old_turn['status'] == 'interrupted' and old_turn['reason_code'] == 'interrupted' and old_turn['agent_id'] == seat['agent_id'], 'Original interrupted callback changed')
        require(old_turn['trigger_event_id'] == entry['trigger_event_id'] == sdk['trigger_event_id'] and entry['receipt_event_id'] == sdk['receipt_event_id'], 'Reconciliation trigger or receipt differs')
        journal = referenced(entry['original_journal'])
        batch = journal['batches'][entry['batch_key']]
        require(journal['scope']['room_id'] == room and journal['scope']['participant_ids'] == roster and journal['scope']['recipient_id'] == seat['agent_id'], 'Original journal scope differs')
        require(batch['status'] == 'blocked' and batch['receipt_status'] == 'confirmed' and batch['trigger_event_id'] == entry['trigger_event_id'] and batch['receipt_event_id'] == entry['receipt_event_id'] and digest(canonical(batch)) == entry['batch_sha256'], 'Original blocked/confirmed claim differs')
        binding = batch['binding']
        ack_content = (f"HANDOFF-ACK delivery {binding['delivery']}; SHA-256 {binding['digest']}; "
                       f"sender @[[{binding['sender_id']}]]")
        import hashlib
        require(sdk['receipt_sender_id'] == seat['agent_id'] and sdk['receipt_recipient_id'] == binding['sender_id'] and sdk['receipt_content_sha256'] == hashlib.sha256(ack_content.encode()).hexdigest(), 'Actual SDK ACK content or sender/recipient differs')
        incident = original['incidents']['turn:' + key]
        require(incident['failed'] is True and incident['closed'] is False and incident['blocked'] is None and not incident['deliveries'], 'Original incident is not a known interrupted callback')
        acceptance = referenced(entry['acceptance_record'])
        require(acceptance['decision'] == 'ACCEPTED' and acceptance['handoff_event'] == entry['trigger_event_id'] and acceptance['receipt_event'] == entry['receipt_event_id'], 'Accepted verdict evidence differs from interrupted callback')
        result[entry['seat']] = {entry['batch_key']: entry['batch_sha256']}
    return result
