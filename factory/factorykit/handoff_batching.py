"""Durable inbound handoff batching at the public BAND preprocessor seam.

A skipped callback is a processed platform receipt, so raw parts are fsynced first.
Only exact complete payloads reach the adapter. A write-ahead model claim is never
replayed after a crash; a human/operator must reconcile ambiguous execution.
This journal records transport/receipt obligations, never product acceptance.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from .common import canonical
from .workflow import parse_header, WorkflowError, _uuid

MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_JOURNAL_BYTES = 64 * 1024 * 1024


class BatchingError(ValueError):
    """Safe error without message content; caller must halt before rethrowing."""


@dataclass(frozen=True)
class BatchDecision:
    kind: str
    content: str | None = None
    event_ids: tuple[str, ...] = ()


def split_fragment(content):
    """Read the canonical header but never trim or normalize payload bytes."""
    try:
        header = parse_header(content)
    except WorkflowError:
        raise BatchingError('Malformed handoff header; preserve inbound evidence.') from None
    if header is None:
        return None
    first, separator, body = content.partition('\n')
    if not separator or '\r' in first:
        raise BatchingError('Handoff requires an exact LF header separator.')
    prefix = first[:first.index('delivery ')]
    if header['index'] == header['total']:
        delimiter = '\nEND OF HANDOFF'
        if not body.endswith(delimiter):
            raise BatchingError('Handoff final delimiter is not exact.')
        body = body[:-len(delimiter)]
    elif header['final_marker']:
        raise BatchingError('Handoff final delimiter appeared before the final part.')
    if len(body.encode('utf-8')) > MAX_PAYLOAD_BYTES:
        raise BatchingError('Handoff fragment exceeds the bounded payload limit.')
    return header, prefix, body


def _sha(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


class HandoffJournal:
    def __init__(self, path, room_id, recipient_id, participant_ids, *, workflow=None, terminal_reconciliations=None):
        self.path = Path(path)
        self.terminal_reconciliations = terminal_reconciliations or {}
        ids = sorted(participant_ids)
        if (not _uuid(room_id) or not _uuid(recipient_id) or recipient_id not in ids
                or len(set(ids)) != len(ids) or any(not _uuid(x) for x in ids)):
            raise BatchingError('Handoff journal requires an exact room and roster.')
        self.scope = dict(room_id=room_id, recipient_id=recipient_id, participant_ids=ids)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._transaction(create=True) as data:
            if workflow is not None:
                self._reconcile_receipts(data, workflow)
            self._validate_terminal_reconciliations(data)
            if any(b['status'] in ('claimed', 'blocked') and not self._terminal(key, b) for key, b in data['batches'].items()):
                raise BatchingError('Retained handoff execution is ambiguous or failed; automatic replay is blocked.')
            if any(b['receipt_status'] == 'claimed' for b in data['batches'].values()):
                raise BatchingError('Retained receipt delivery is ambiguous; automatic resend is blocked.')

    def _terminal(self, key, batch):
        return (key in self.terminal_reconciliations and batch['status'] == 'blocked'
                and batch['receipt_status'] == 'confirmed'
                and hashlib.sha256(canonical(batch)).hexdigest() == self.terminal_reconciliations[key])

    def _validate_terminal_reconciliations(self, data):
        if any(key not in data['batches'] or not self._terminal(key, data['batches'][key])
               for key in self.terminal_reconciliations):
            raise BatchingError('Reviewed terminal claim changed; no replay or reset is allowed.')

    @contextmanager
    def _transaction(self, create=False):
        lock = self.path.with_suffix(self.path.suffix + '.lock')
        existed = lock.exists()
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            self._private(os.fstat(fd))
            fcntl.flock(fd, fcntl.LOCK_EX)
            if self.path.is_symlink():
                raise BatchingError('Handoff journal cannot be a symlink.')
            if self.path.exists():
                self._private(self.path.stat())
                if self.path.stat().st_size > MAX_JOURNAL_BYTES:
                    raise BatchingError('Handoff journal exceeded its storage bound.')
                try:
                    data = json.loads(self.path.read_text())
                    self._validate(data)
                except (ValueError, KeyError, TypeError, AttributeError):
                    raise BatchingError('Malformed retained handoff journal; no reset is permitted.') from None
            elif create and not existed:
                data = dict(version=2, scope=self.scope, events={}, batches={})
            else:
                raise BatchingError('Retained handoff journal is missing; no reset is permitted.')
            before = canonical(data)
            yield data
            after = canonical(data)
            if not self.path.exists() or before != after:
                self._validate(data)
                if len(after) > MAX_JOURNAL_BYTES:
                    raise BatchingError('Handoff journal exceeded its storage bound.')
                self._write(after)
        finally:
            os.close(fd)

    @staticmethod
    def _private(info):
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise BatchingError('Handoff state must be a privately owned regular file.')

    def _write(self, raw):
        fd, name = tempfile.mkstemp(prefix='.' + self.path.name, dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(raw); out.flush(); os.fsync(out.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)

    def _validate(self, d):
        def need(condition):
            if not condition:
                raise BatchingError('Malformed retained handoff authority; preserve state.')
        need(set(d) == {'version', 'scope', 'events', 'batches'} and d['version'] == 2 and d['scope'] == self.scope)
        need(isinstance(d['events'], dict) and isinstance(d['batches'], dict))
        for event_id, event in d['events'].items():
            need(_uuid(event_id) and set(event) == {'id','room_id','sender_id','sender_type','content','batch','index','recipients'})
            need(event['id'] == event_id and event['room_id'] == self.scope['room_id'] and event['sender_type'] == 'Agent')
            need(event['sender_id'] in self.scope['participant_ids'] and event['batch'] in d['batches'])
            need(event['recipients'] == d['batches'][event['batch']]['binding']['recipients'])
            parsed = split_fragment(event['content'])
            need(parsed is not None)
            h, prefix, _ = parsed
            b = d['batches'][event['batch']]
            need(event['index'] == h['index'] and self._binding(h, prefix, event['sender_id']) == b['binding'])
        for key, b in d['batches'].items():
            need(set(b) == {'binding','parts','status','trigger_event_id','ack_required','receipt_status','receipt_event_id'})
            need(key == self._key(b['binding']['sender_id'], b['binding']['delivery']))
            need(b['status'] in ('collecting','ready','claimed','completed','blocked') and type(b['ack_required']) is bool)
            need(b['receipt_status'] in ('unsent', 'claimed', 'confirmed'))
            need(_uuid(b['receipt_event_id']) if b['receipt_status'] == 'confirmed' else b['receipt_event_id'] is None)
            need(b['receipt_status'] == 'unsent' or b['status'] in ('claimed','completed','blocked'))
            need(isinstance(b['parts'], dict) and 0 < len(b['parts']) <= b['binding']['total'])
            for number, event_ids in b['parts'].items():
                need(re.fullmatch(r'[1-9][0-9]*', number) and isinstance(event_ids, list) and event_ids and len(set(event_ids)) == len(event_ids))
                first = None
                for event_id in event_ids:
                    need(event_id in d['events'])
                    e = d['events'][event_id]
                    need(e['batch'] == key and str(e['index']) == number)
                    need(first is None or first == e['content'])
                    first = e['content']
            complete = len(b['parts']) == b['binding']['total']
            need(complete == (b['status'] != 'collecting'))
            if complete:
                need(b['trigger_event_id'] in d['events'] and d['events'][b['trigger_event_id']]['batch'] == key)
                self._payload(d, b)
            else:
                need(b['trigger_event_id'] is None and b['ack_required'])
            need(b['ack_required'] or b['status'] == 'completed')
        # Every stored event must be reachable; do not accept an edited orphan.
        need(set(d['events']) == {eid for b in d['batches'].values() for ids in b['parts'].values() for eid in ids})

    @staticmethod
    def _key(sender, delivery):
        return sender + ':' + delivery

    @staticmethod
    def _binding(h, prefix, sender):
        return dict(sender_id=sender, delivery=h['delivery'], total=h['total'], digest=h['digest'], recipients=h['recipients'], prefix=prefix)

    def _payload(self, d, b):
        body = ''.join(split_fragment(d['events'][b['parts'][str(i)][0]]['content'])[2]
                       for i in range(1, b['binding']['total'] + 1))
        if len(body.encode('utf-8')) > MAX_PAYLOAD_BYTES or _sha(body) != b['binding']['digest']:
            raise BatchingError('Complete handoff payload digest or size is invalid.')
        return body

    def observe(self, payload, *, event_room_id, confirmed=None):
        # The SDK routes WebSocket messages by MessageEvent.room_id. Its nested
        # payload room is optional; never invent or overwrite a payload field.
        payload_room = getattr(payload, 'chat_room_id', None)
        if event_room_id != self.scope['room_id'] or (payload_room is not None and payload_room != event_room_id):
            raise BatchingError('Inbound handoff envelope or explicit payload room conflicts with its journal scope.')
        parsed = split_fragment(payload.content)
        if parsed is None:
            return BatchDecision('ordinary')
        h, prefix, body = parsed
        metadata = getattr(payload, 'metadata', None)
        mentions = getattr(metadata, 'mentions', None)
        recipients = sorted(getattr(m, 'id', None) for m in mentions) if mentions and all(isinstance(getattr(m, 'id', None), str) for m in mentions) else []
        if ((recipients and recipients != h['recipients']) or not _uuid(payload.id)
                or payload.sender_type != 'Agent' or payload.sender_id not in self.scope['participant_ids']
                or self.scope['recipient_id'] not in h['recipients']
                or not set(h['recipients']) <= set(self.scope['participant_ids'])
                or payload.sender_id in h['recipients']):
            raise BatchingError('Inbound handoff room, sender, or recipient is outside its exact roster scope.')
        if (not isinstance(confirmed, dict) or confirmed.get('sender_id') != payload.sender_id
                or confirmed.get('recipient_ids') != h['recipients']
                or confirmed.get('content_sha256') != _sha(payload.content)):
            raise BatchingError('Handoff lacks a matching confirmed outbound event binding.')
        recipients = h['recipients']
        key = self._key(payload.sender_id, h['delivery'])
        binding = self._binding(h, prefix, payload.sender_id)
        event = dict(id=payload.id, room_id=event_room_id, sender_id=payload.sender_id,
                     sender_type=payload.sender_type, content=payload.content, batch=key, index=h['index'], recipients=recipients)
        with self._transaction() as d:
            self._validate_terminal_reconciliations(d)
            if any(b['status'] in ('claimed','blocked') and not self._terminal(k, b) for k, b in d['batches'].items()):
                raise BatchingError('Handoff execution remains ambiguous; no further admission is allowed.')
            old = d['events'].get(payload.id)
            if old is not None and old != event:
                raise BatchingError('Conflicting repeated BAND event identity.')
            if key in self.terminal_reconciliations:
                # No journal mutation, model claim, or receipt resend. A new
                # transport identity for this old delivery is not accepted.
                if old != event:
                    raise BatchingError('Terminal delivery received a new event identity; reconcile explicitly.')
                return BatchDecision('skip')
            b = d['batches'].setdefault(key, dict(binding=binding, parts={}, status='collecting', trigger_event_id=None,
                                                ack_required=True, receipt_status='unsent', receipt_event_id=None))
            if b['binding'] != binding:
                raise BatchingError('Conflicting repeated handoff binding.')
            ids = b['parts'].setdefault(str(h['index']), [])
            if ids and d['events'][ids[0]]['content'] != payload.content:
                raise BatchingError('Conflicting duplicate handoff fragment.')
            if old is None:
                d['events'][payload.id] = event
                ids.append(payload.id)
            if b['status'] == 'completed':
                return BatchDecision('skip')
            if len(b['parts']) < h['total']:
                return BatchDecision('skip')
            assembled = self._payload(d, b)
            if b['status'] == 'collecting':
                b['status'], b['trigger_event_id'] = 'ready', payload.id
            if b['trigger_event_id'] != payload.id:
                return BatchDecision('skip')
            source_ids = tuple(b['parts'][str(i)][0] for i in range(1, h['total'] + 1))
            # This is explicitly a local view, never a fabricated BAND event.
            envelope = (f"Verified complete handoff delivery {h['delivery']}; SHA-256 {h['digest']}; "
                        f"sender @[[{payload.sender_id}]]; recipient " + ', '.join('@[[' + x + ']]' for x in h['recipients']) +
                        f"\nOriginal header prefix: {prefix!r}; complete parts: {h['total']}/{h['total']}\nOriginal BAND fragment IDs (part order): {', '.join(source_ids)}\n"
                        f"Transport trigger ID (actual last arrival): {payload.id}\n"
                        "Acknowledge this complete delivery with factory_handoff_ack using its delivery_id before acting. "
                        "The payload below is the byte-exact reassembly; receipt is not task acceptance.\n\n")
            return BatchDecision('complete', envelope + assembled, source_ids)

    def acknowledgement_binding(self, delivery_id):
        """Read a verified, admitted delivery; never infer receipt from headers."""
        if not isinstance(delivery_id, str):
            raise BatchingError('Acknowledgement requires a delivery ID.')
        with self._transaction() as d:
            matches = [b for b in d['batches'].values() if b['binding']['delivery'] == delivery_id]
            if len(matches) != 1 or matches[0]['status'] not in ('claimed', 'completed'):
                raise BatchingError('Acknowledgement requires one complete admitted delivery; partial or uncertain work cannot be acknowledged.')
            batch = matches[0]
            if batch['receipt_status'] == 'claimed':
                raise BatchingError('Receipt delivery remains uncertain; no resend is permitted.')
            # Recheck byte-exact reassembly before supplying any receipt authority.
            self._payload(d, batch)
            return dict(batch['binding'], room_id=self.scope['room_id'], recipient_id=self.scope['recipient_id'])

    def claim_acknowledgement(self, delivery_id):
        """Durable write-ahead receipt claim, independent of the model claim."""
        with self._transaction() as d:
            matches = [b for b in d['batches'].values() if b['binding']['delivery'] == delivery_id]
            if (len(matches) != 1 or matches[0]['status'] not in ('claimed','completed')
                    or matches[0]['receipt_status'] != 'unsent'):
                raise BatchingError('Receipt already has a claim or confirmed outcome; no resend is permitted.')
            matches[0]['receipt_status'] = 'claimed'

    def confirm_acknowledgement(self, delivery_id, event_id):
        with self._transaction() as d:
            matches = [b for b in d['batches'].values() if b['binding']['delivery'] == delivery_id]
            if len(matches) != 1 or matches[0]['receipt_status'] != 'claimed' or not _uuid(event_id):
                raise BatchingError('Confirmed receipt lacks its exact durable send claim.')
            matches[0].update(receipt_status='confirmed', receipt_event_id=event_id)

    def claim(self, event_id):
        """None=ordinary; False=already completed; True=new durable model claim."""
        with self._transaction() as d:
            e = d['events'].get(event_id)
            if e is None:
                return None
            b = d['batches'][e['batch']]
            self._validate_terminal_reconciliations(d)
            if self._terminal(e['batch'], b):
                return False
            if b['status'] == 'completed':
                return False
            if b['status'] != 'ready' or b['trigger_event_id'] != event_id:
                raise BatchingError('Handoff cannot be claimed from this state or trigger.')
            b['status'] = 'claimed'
            return True

    def finish(self, event_id, *, completed, acknowledged=False):
        with self._transaction() as d:
            b = d['batches'][d['events'][event_id]['batch']]
            if b['status'] != 'claimed' or b['trigger_event_id'] != event_id:
                raise BatchingError('Handoff completion lacks its exact claim.')
            b['status'] = 'completed' if completed else 'blocked'
            if acknowledged and b['receipt_status'] != 'confirmed':
                raise BatchingError('Receipt completion requires confirmed send evidence.')
            b['ack_required'] = not (completed and b['receipt_status'] == 'confirmed')

    def reconcile_acknowledgements(self, workflow):
        """Clear a receipt obligation only from matching confirmed watchdog evidence."""
        with self._transaction() as d:
            self._reconcile_receipts(d, workflow)

    def _reconcile_receipts(self, d, workflow):
        if (not isinstance(workflow, dict) or workflow.get('scope', {}).get('room_id') != self.scope['room_id']
                or workflow.get('scope', {}).get('participant_ids') != self.scope['participant_ids']):
            raise BatchingError('Receipt reconciliation requires the exact retained room and roster.')
        for b in d['batches'].values():
            if b['status'] != 'completed' or not b['ack_required']:
                continue
            binding = b['binding']
            v = workflow.get('deliveries', {}).get(binding['delivery'], {})
            actor = self.scope['recipient_id']
            ack_id = v.get('acks', {}).get(actor)
            event = workflow.get('events', {}).get(ack_id, {})
            content = (f"HANDOFF-ACK delivery {binding['delivery']}; SHA-256 {binding['digest']}; "
                       f"sender @[[{binding['sender_id']}]]")
            if (v.get('sender_id') == binding['sender_id'] and v.get('recipient_ids') == binding['recipients']
                    and v.get('digest') == binding['digest'] and v.get('total') == binding['total']
                    and v.get('complete') is True and _uuid(ack_id) and event.get('sender_id') == actor
                    and event.get('recipient_ids') == [binding['sender_id']]
                    and event.get('content_sha256') == _sha(content)):
                b.update(ack_required=False, receipt_status='confirmed', receipt_event_id=ack_id)

    def summary(self):
        with self._transaction() as d:
            return {key: dict(status=b['status'], ack_required=b['ack_required'], received_parts=len(b['parts']),
                              total=b['binding']['total'], trigger_event_id=b['trigger_event_id'],
                              receipt_status=b['receipt_status'], receipt_event_id=b['receipt_event_id'])
                    for key, b in d['batches'].items()}
