"""Validate the one approved second fixture's actual REST absence, not SDK absence."""
from __future__ import annotations
import json
from .membership_gap import _need, _moment
from .membership_reconcile import (TERMINAL, archived_path, create_once, encoded,
                                  no_admission_snapshot, private_read, sha)


def fixture_path(path, kind):
    return path.with_name(path.name + '.second-fixture.' + kind + '.json')


def validate_fixture(observer, episode, index):
    evidence = episode.get('operator_fixture')
    _need(index == 1 and observer.binding['max_transitions'] == 2 and isinstance(evidence, dict) and
          observer.state['episodes'][0]['state'] == TERMINAL and episode.get('absence_source') == 'operator_authenticated_rest',
          'invalid second fixture scope')
    claim_raw = private_read(fixture_path(observer.path, 'claim'))
    receipt_raw = private_read(fixture_path(observer.path, 'absence'))
    _need(sha(claim_raw) == evidence.get('claim_sha256') and sha(receipt_raw) == evidence.get('absence_sha256'),
          'second fixture claim or absence hash changed')
    claim, receipt = json.loads(claim_raw), json.loads(receipt_raw)
    prior_raw = private_read(archived_path(observer.path, evidence.get('prior_journal_sha256'), 'before-second'))
    _need(sha(prior_raw) == evidence['prior_journal_sha256'], 'second fixture prior journal hash changed')
    prior = json.loads(prior_raw)
    _need(prior == {'schema_version': 1, 'binding': observer.binding,
                   'episodes': observer.state['episodes'][:1], 'blocked_reason': None}, 'second fixture altered prior failure')
    plan = claim.get('plan', {})
    _need(claim.get('status') == 'CLAIMED_SECOND_REMOVAL_NO_RETRY' and claim.get('attempt_index') == 2 and
          claim.get('room_id') == observer.binding['room_id'] and claim.get('agent_id') == episode['agent_id'] and
          claim.get('plan_sha256') == sha(encoded(plan)) and plan.get('reconciled_journal_sha256') == evidence['prior_journal_sha256'] and
          plan.get('files', {}).get('membership') == evidence['prior_journal_sha256'] and
          plan.get('configuration_sha256') == observer.binding['configuration_sha256'] and
          plan.get('room_id') == observer.binding['room_id'] and plan.get('agent_id') == episode['agent_id'] and
          receipt.get('claim_sha256') == evidence['claim_sha256'] and receipt.get('status') == 'ACTUAL_REST_ABSENCE_OBSERVED',
          'second fixture approval binding invalid')
    before = claim.get('roster_before')
    observer._validate_roster_proof(before, None)
    first = prior['episodes'][0]['operator_reconciliation']['restoration']
    _need(len(before['members']) == 8 and before['owner_uuid'] == first['owner_uuid'] and
          before['observed_epoch'] > first['observed_epoch'] and _moment(claim.get('claimed_epoch')) and
          before['observed_epoch'] <= claim['claimed_epoch'] <= episode['first_seen_epoch'],
          'second fixture lacks distinct complete restoration barrier')
    _need(episode.get('absence') == receipt.get('absence') and
          episode['first_seen_epoch'] == receipt['absence']['observed_epoch'] and
          receipt['absence']['owner_uuid'] == first['owner_uuid'] and len(receipt['absence']['members']) == 7 and
          episode['deadline_epoch'] == episode['first_seen_epoch'] + observer.binding['ack_timeout_seconds'],
          'second fixture original absence deadline changed')
    observer._validate_proof(episode['absence'], episode, episode['agent_id'])
    observed = episode.get('sdk_absence_observed')
    _need(type(observed) is bool and (not observed and 'first_sdk_absence_epoch' not in episode or
          observed and _moment(episode.get('first_sdk_absence_epoch')) and
          episode['first_seen_epoch'] <= episode['first_sdk_absence_epoch'] < episode['deadline_epoch']),
          'second fixture falsely claims SDK absence')


def seed_absence(observer, plan, absence):
    """Operator helper only, under launch/guard locks after actual REST readback."""
    _need(observer.state.get('blocked_reason') is None and len(observer.state['episodes']) == 1 and
          observer.state['episodes'][0]['state'] == TERMINAL and observer.binding['max_transitions'] == 2,
          'no remaining distinct fixture allowance')
    current = no_admission_snapshot(observer.config, observer.binding['room_id'])
    _need(current == plan.get('files'), 'pre-admission evidence changed before second fixture seed')
    prior_raw = private_read(observer.path)
    _need(sha(prior_raw) == plan.get('reconciled_journal_sha256'), 'reconciled failure barrier changed')
    claim_raw = private_read(fixture_path(observer.path, 'claim'))
    claim = json.loads(claim_raw)
    _need(claim.get('plan') == plan and absence.get('missing_agent_id') == claim.get('agent_id'),
          'second fixture target changed')
    receipt = {'status': 'ACTUAL_REST_ABSENCE_OBSERVED', 'claim_sha256': sha(claim_raw), 'absence': absence}
    receipt_raw = encoded(receipt)
    # Both immutable records precede the journal write. Their existence refuses replay.
    create_once(archived_path(observer.path, sha(prior_raw), 'before-second'), prior_raw)
    create_once(fixture_path(observer.path, 'absence'), receipt_raw)
    episode = {'agent_id': claim['agent_id'], 'first_seen_epoch': absence['observed_epoch'],
               'deadline_epoch': absence['observed_epoch'] + observer.binding['ack_timeout_seconds'],
               'state': 'absent', 'absence': absence, 'absence_source': 'operator_authenticated_rest',
               'sdk_absence_observed': False,
               'operator_fixture': {'claim_sha256': sha(claim_raw), 'absence_sha256': sha(receipt_raw),
                                    'prior_journal_sha256': sha(prior_raw)}}
    observer.state['episodes'].append(episode)
    observer._validate()
    observer._save()
    return episode['deadline_epoch']
