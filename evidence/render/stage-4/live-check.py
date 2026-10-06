#!/usr/bin/env python3
"""Authorized synthetic-only Stage4 live smoke; never saves credentials or exports.
Usage: python3 SCRIPT expectedDeploymentCommit receiptPath packagePath [--base URL]
Only invoke after the operator confirms the exact Render deployment is live.
"""
import argparse, datetime, hashlib, json, pathlib, secrets, urllib.error, urllib.request

CANDIDATE = 'ac1eb4945a2adaeaaf8a62d0817bdbc6a021e566'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('expectedDeploymentCommit'); parser.add_argument('receiptPath')
    parser.add_argument('packagePath'); parser.add_argument('--base', default='https://tablekeeper-stage2.onrender.com')
    args = parser.parse_args(); base = args.base.rstrip('/'); package = pathlib.Path(args.packagePath)
    receipt = {'startedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'expectedDeploymentCommit': args.expectedDeploymentCommit, 'applicationCandidate': CANDIDATE,
               'scope': 'Finite actual HTTP smoke of ephemeral demo using synthetic accounts/reservations; no reset/import/export, credentials, private snapshots or exhaustive acceptance.',
               'groups': {}, 'requestCount': 0, 'cleanup': {}}
    token = None; refs = set(); errors = []; completed = False
    def request(method, path, body=None, auth=None, key=None):
        headers = {'Accept': 'application/json', 'Connection': 'close'}
        if auth: headers['Authorization'] = 'Bearer ' + auth
        if key: headers['Idempotency-Key'] = key
        data = json.dumps(body, separators=(',', ':')).encode() if body is not None else None
        if data is not None: headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(base+path, data=data, headers=headers, method=method)
        receipt['requestCount'] += 1
        try: response = urllib.request.urlopen(req, timeout=45)
        except urllib.error.HTTPError as exc: response = exc
        with response:
            raw = response.read()
            return response.status, raw
    def api(method, path, body=None, auth=None, key=None):
        status, raw = request(method, path, body, auth, key)
        return status, json.loads(raw) if raw else None
    def expect(test, label):
        if not test: raise AssertionError(label)
    def signup():
        suffix = secrets.token_hex(10)
        status, data = api('POST', '/auth/signup', {'email': 'render-check-'+suffix+'@example.com',
            'password': secrets.token_urlsafe(24), 'display_name': 'Synthetic deployment check'})
        expect(status == 201 and isinstance(data.get('token'), str), 'synthetic_signup')
        return data['token']
    try:
        status, deployment = api('GET', '/deployment')
        expect(status == 200 and deployment.get('stage') == 4 and
               deployment.get('applicationCandidate') == CANDIDATE and
               deployment.get('deploymentCommit') == args.expectedDeploymentCommit, 'deployment_binding')
        receipt['groups']['deployment'] = {'status': status, 'stage': 4, 'commitMatched': True, 'candidateMatched': True}
        status, health = api('GET', '/health'); expect(status == 200 and health.get('status') == 'ok', 'health')
        assets = []
        for path in sorted((package/'static').iterdir()):
            if not path.is_file(): continue
            status, raw = request('GET', '/static/'+path.name)
            actual = hashlib.sha256(raw).hexdigest(); expected = hashlib.sha256(path.read_bytes()).hexdigest()
            expect(status == 200 and actual == expected, 'static_asset_binding')
            assets.append({'asset': path.name, 'status': status, 'sha256': actual})
        expect(bool(assets), 'nonempty_assets')
        status, raw = request('GET', '/'); expect(status == 200 and raw == (package/'static/index.html').read_bytes(), 'root_binding')
        receipt['groups']['health_root_assets'] = {'health': 200, 'root': 200, 'assets': assets}
        # Only route-closure probes: no operation is permitted to reach test handlers.
        methods = ['GET','POST','PATCH','PUT','DELETE','HEAD','OPTIONS']; closure_count = 0
        paths = ['/_test/'+name for name in ('reset','import','export')]
        paths += ['/%5Ftest/'+name for name in ('reset','import','export')]
        paths += ['/%255Ftest/'+name for name in ('reset','import','export')]
        paths += ['/_test/'+name+'?probe=synthetic' for name in ('reset','import','export')]
        paths += ['/_test','/%5Ftest','/%255Ftest']
        for path in paths:
            for method in methods:
                status, raw = request(method, path, {} if method in ('POST','PATCH','PUT','DELETE') else None)
                expect(status == 404, 'test_route_closed')
                closure_count += 1
        receipt['groups']['test_route_closure'] = {'requests': closure_count, 'allStatuses': 404,
            'methods': methods, 'spellings': ['direct','percent-decoded','double-percent-decoded','query','prefix-only']}
        token = signup(); other = signup()
        status, listing = api('GET', '/restaurants'); expect(status == 200, 'restaurant_list')
        expect(any(r['id']=='cedar' for r in listing['restaurants']), 'sample_cedar_present')
        # Two future weekly occurrences; choose two distinct common available times.
        dates = ['2030-01-07','2030-01-14']; available = []
        for date in dates:
            status, data = api('GET', '/availability?restaurant_id=cedar&date='+date+'&party_size=2')
            expect(status == 200, 'public_availability')
            available.append({row['starts_at_local'][-5:] for row in data['slots'] if 'window' in row.get('available_table_ids', [])})
        times = sorted(available[0] & available[1]); expect(len(times)>=2, 'common_future_slots')
        first = '19:00' if '19:00' in times else times[0]
        second = '20:00' if '20:00' in times and first!='20:00' else next(t for t in times if t!=first)
        booking_key=secrets.token_hex(16)
        booking_body={'restaurant_id':'cedar','table_id':'window','party_size':2,'starts_at_local':dates[0]+'T'+first}
        status, booking = api('POST','/reservations',booking_body,token,booking_key)
        expect(status == 201, 'synthetic_booking'); refs.add(booking['reference'])
        status, lookup = api('GET','/reservations/'+booking['reference'],auth=token)
        expect(status == 200 and lookup == booking, 'private_lookup')
        status, _ = api('GET','/reservations/'+booking['reference'],auth=other); expect(status==404,'other_account_lookup_boundary')
        status, _ = api('GET','/reservations/'+booking['reference']+'/history'); expect(status==404,'anonymous_private_history_boundary')
        status, series = api('POST','/series',{'anchor_reference':booking['reference'],'count':2,'interval_weeks':1},token,secrets.token_hex(16))
        expect(status==201 and len(series['occurrences'])==2, 'recurring_adoption')
        for occurrence in series['occurrences']: refs.add(occurrence['reference'])
        original_times=[o['reservation']['starts_at_local'] for o in series['occurrences']]
        expect(original_times==[date+'T'+first for date in dates], 'original_weekly_schedule')
        sid=series['series_id']; original_revision=series['revision']; key=secrets.token_hex(16)
        body={'expected_revision':original_revision,'from_index':0,'local_time':second}
        status, amended=api('POST','/series/'+sid+'/amend',body,token,key)
        expect(status==201 and amended['revision']>original_revision, 'stage4_amend_revision')
        expect([o['reservation']['starts_at_local'] for o in amended['occurrences']]==[date+'T'+second for date in dates], 'stage4_original_dates_preserved')
        status, current=api('GET','/series/'+sid,auth=token)
        expect(status==200 and current==amended, 'current_series_matches_amendment')
        status, original_booking=api('POST','/reservations',booking_body,token,booking_key)
        expect(status==200 and original_booking==booking, 'immutable_original_booking_receipt')
        status, replay=api('POST','/series/'+sid+'/amend',body,token,key)
        expect(status==200 and replay==amended, 'immutable_same_key_amend_replay')
        status, stale=api('POST','/series/'+sid+'/amend',body,token,secrets.token_hex(16))
        expect(status==409 and stale['error']['code']=='stale_revision', 'fresh_key_stale_revision')
        status, _=api('GET','/series/'+sid);expect(status==404,'anonymous_private_series_boundary')
        status, _=api('POST','/restaurants/cedar/replans',{'table_id':'window','from':dates[0]+'T19:00:00-08:00','to':dates[0]+'T21:30:00-08:00'},token,secrets.token_hex(16));expect(status==403,'nonmanager_replan')
        status, _=api('POST','/restaurants/cedar/policies',{},token,secrets.token_hex(16));expect(status==403,'nonmanager_policy')
        receipt['groups']['synthetic_transactions']={'signupAccounts':2,'booking':201,'lookup':200,'adoption':201,
            'occurrences':2,'collectiveAmend':201,'sameKeyReplay':200,'replayBodyIdentical':True,
            'originalScheduleDatesPreserved':True,'originalBookingReceiptPreserved':True,'currentSeriesMatchesAmendment':True,'newRevisionAdvanced':True,'staleFreshKey':409,
            'otherAccountLookup':404,'anonymousHistoryAndSeries':404,'nonmanagerReplanAndPolicy':403}
        completed=True
    except Exception as exc:
        errors.append({'type':type(exc).__name__,'check':str(exc) if isinstance(exc,AssertionError) else 'request_or_local_input_failure'})
    finally:
        statuses=[]
        if token:
            for ref in sorted(refs):
                try:
                    status, result=api('POST','/reservations/'+ref+'/cancel',{},token)
                    statuses.append(status)
                    if status!=200 or result.get('status')!='cancelled':errors.append({'type':'CleanupFailure','check':'synthetic_reservation_cancel'})
                except Exception: errors.append({'type':'CleanupFailure','check':'synthetic_reservation_cancel_request'})
        receipt['cleanup']={'reservationsDiscovered':len(refs),'cancelStatuses':statuses,
            'allKnownReservationsCancelled':len(statuses)==len(refs) and all(s==200 for s in statuses),
            'accounts':'Synthetic accounts remain in ephemeral demo state; no account-deletion API used.'}
        receipt['finishedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        receipt['status']='PASS' if completed and not errors else 'FAIL';receipt['errors']=errors
        target=pathlib.Path(args.receiptPath);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(receipt,indent=2)+'\n')
        print(json.dumps({'status':receipt['status'],'groups':len(receipt['groups']),'requests':receipt['requestCount'],
                          'cleanupCancelled':len(statuses),'receipt':str(target)}))
    return 0 if receipt['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
