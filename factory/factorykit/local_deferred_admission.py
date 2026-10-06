"""SHA-bound local-practice first admission after a denied turn reservation.

The original blocked journal remains immutable. A separately named derived
journal tracks the first real model attempt; completed or interrupted model work
is never eligible. This is transport recovery, not product acceptance.
"""
import copy
import json
from pathlib import Path

from .common import FactoryError, canonical, digest
from .local_terminal_reconciliation import referenced, require


def load_deferred_admission(config, room, workflow):
    manifest = Path(config['paths']['runs']) / 'readiness/local-turn-1000-reconciliation.json'
    if not manifest.exists():
        return config, {}, {}
    require(not manifest.is_symlink(), 'Deferred admission manifest is a symlink')
    record = json.loads(manifest.read_bytes())
    require(config['launch'].get('practice_mode') is True and room == config['band']['rehearsal_room_id'], 'Deferred admission is local-practice only')
    require(record['status'] == 'APPROVED_FIRST_ADMISSION' and record['configuration_sha256'] == digest(canonical(config)), 'Deferred admission configuration differs')
    require(record['authorization'] == {'user_answer': 'can you increate the turn limit', 'old_cap': 769, 'new_cap': 1000}, 'Missing exact turn-cap approval')
    old = referenced(record['original_configuration'])
    expected = copy.deepcopy(old)
    expected['budgets']['max_turns_per_seat'] = 1000
    expected['paths']['factory'] = config['paths']['factory']
    require(old['budgets']['max_turns_per_seat'] == 769 and config == expected, 'Deferred admission changes exceed turn cap and isolated source')
    roster = sorted(s['agent_id'] for s in config['seats'])
    require(record['room_id'] == room and record['participant_ids'] == roster and workflow['scope']['room_id'] == room and workflow['scope']['participant_ids'] == roster, 'Deferred admission room or roster differs')
    owner = referenced(record['stopped_owner'])
    require(owner['status'] == 'stopped' and owner['mode'] == 'rehearsal' and not owner['workflow']['active_turn_ids'] and not owner['workflow']['sdk_execution_busy'] and not owner['workflow']['unknown_notices'], 'Original owner was not stopped and idle')
    ledger = referenced(record['original_ledger'])
    require(ledger['stopped_reason'] == 'handoff model claim did not complete; preserve before retry' and ledger['tokens'] < config['budgets']['max_total_tokens'] and not ledger['room_stopped_reasons'].get(room), 'Deferred admission halt is not the reviewed budget denial')
    require(len(record['entries']) == 1, 'Only one proven never-admitted handoff is supported')
    before = referenced(record['original_workflow'])
    sdk = referenced(record['sdk_evidence'])
    require(sdk['status'] == 'VERIFIED_NEVER_ADMITTED_READ_ONLY' and sdk['room_id'] == room and sdk['participant_ids'] == roster, 'Missing exact SDK deferred-admission proof')
    for ref in sdk['evidence']:
        referenced(ref)
    require(bool(sdk['evidence']), 'SDK evidence inventory is empty')
    result = {}
    for entry in record['entries']:
        seat = next((s for s in config['seats'] if s['id'] == entry['seat']), None)
        require(seat is not None and seat['agent_id'] == sdk['recipient_id'], 'Deferred admission recipient differs')
        trigger = entry['trigger_event_id']
        require(ledger['turns'][seat['id']] == 769 and all(v < 769 for k, v in ledger['turns'].items() if k != seat['id']), 'Archived turn counters do not prove the exact exhausted seat')
        require(not any(v.get('trigger_event_id') == trigger for v in before['turns'].values()), 'A prior model turn exists for this handoff; replay forbidden')
        source = Path(entry['original_journal']['path'])
        journal = referenced(entry['original_journal'])
        require(journal['scope'] == {'room_id': room, 'recipient_id': seat['agent_id'], 'participant_ids': roster}, 'Original deferred journal scope differs')
        batch = journal['batches'][entry['batch_key']]
        require(batch['status'] == 'blocked' and batch['receipt_status'] == 'unsent' and batch['receipt_event_id'] is None and batch['ack_required'] is True and batch['trigger_event_id'] == trigger and digest(canonical(batch)) == entry['batch_sha256'], 'Original blocked, unacknowledged claim differs')
        require(sdk['trigger_event_id'] == trigger and sdk['sender_id'] == batch['binding']['sender_id'] and sdk['trigger_sdk_status'] in ('failed', 'pending') and sdk['codex_thread_id'] == entry['codex_thread_id'], 'SDK trigger or exact retained thread differs')
        parts = [batch['parts'][str(i)][0] for i in range(1, batch['binding']['total'] + 1)]
        require([p['event_id'] for p in sdk['parts']] == parts, 'SDK part identities differ')
        for p in sdk['parts']:
            event = journal['events'][p['event_id']]
            require(p['sender_id'] == batch['binding']['sender_id'] and p['recipient_id'] == seat['agent_id'] and p['content_sha256'] == digest(event['content'].encode()) and p['sdk_status'] in ('processed', 'failed', 'pending', 'processing'), 'Actual SDK part binding differs')
        target = Path(entry['derived_journal'])
        require(target.is_absolute() and target.parent == Path(config['paths']['runs']) / 'runtime' and target.name == f"deferred-handoffs-{room}-{seat['id']}.json" and not target.is_symlink() and target != source, 'Deferred journal path is outside exact run authority')
        initial = referenced(entry['initial_derived_journal'])
        expected_journal = copy.deepcopy(journal)
        expected_journal['batches'][entry['batch_key']]['status'] = 'ready'
        require(initial == expected_journal and target.is_file(), 'Derived first-admission journal differs')
        current = json.loads(target.read_bytes())
        active = current['batches'].get(entry['batch_key'], {})
        require(all(active.get(k) == batch[k] for k in ('binding', 'parts', 'trigger_event_id')), 'Deferred original payload authority changed')
        admitted = [v for v in workflow['turns'].values() if v.get('trigger_event_id') == trigger]
        if active.get('status') == 'ready':
            require(not admitted, 'Deferred ready journal cannot replay an already admitted model event')
        if active.get('status') == 'completed':
            require(len(admitted) == 1 and admitted[0].get('status') == 'completed' and admitted[0].get('agent_id') == seat['agent_id'], 'Deferred completion lacks its exact real completed model turn')
            binding = batch['binding']
            delivery = workflow.get('deliveries', {}).get(binding['delivery'], {})
            ack = delivery.get('acks', {}).get(seat['agent_id'])
            event = workflow.get('events', {}).get(ack, {})
            content = f"HANDOFF-ACK delivery {binding['delivery']}; SHA-256 {binding['digest']}; sender @[[{binding['sender_id']}]]"
            require(active.get('receipt_status') == 'confirmed' and active.get('receipt_event_id') == ack and delivery.get('complete') is True and delivery.get('sender_id') == binding['sender_id'] and delivery.get('recipient_ids') == binding['recipients'] and delivery.get('digest') == binding['digest'] and event.get('sender_id') == seat['agent_id'] and event.get('recipient_ids') == [binding['sender_id']] and event.get('content_sha256') == digest(content.encode()), 'Deferred completion lacks its actual matching receipt evidence')
        # The HandoffJournal constructor verifies all journal states and rejects
        # ambiguous claimed/blocked real attempts. No second reset occurs here.
        result[seat['id']] = target
    relocations = record.get('acceptance_relocations', {})
    for replacement in relocations.values():
        referenced(replacement)
    return old, result, relocations
