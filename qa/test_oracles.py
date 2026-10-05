"""Local QA-oracle controls only; these do not exercise product behavior."""
import json
import copy
import datetime as dt
from decimal import Decimal, localcontext
import unittest
from unittest.mock import patch
from stage1_http import Checks, exact_loads, numeric_body, same, fixture
from legacy_upgrade import check_history


class Response:
    status = 200
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return self.body


class OracleControls(unittest.TestCase):
    def test_reported_fractional_witness(self):
        self.assertFalse(same(exact_loads('{"n":1.0000000000000001}'), exact_loads('{"n":1.0}')))

    def test_equivalent_numeric_spellings(self):
        for left,right in [('1','1.0'),('1.0000000000000001','1.00000000000000010'),('10e-1','1'),('-0','0.0')]:
            self.assertTrue(same(exact_loads(left), exact_loads(right)))

    def test_bool_not_number(self):
        for value in ['1','1.0']:
            self.assertFalse(same(exact_loads('true'), exact_loads(value)))
        self.assertFalse(same(exact_loads('false'), exact_loads('0')))

    def test_nested_types_and_arrays(self):
        self.assertTrue(same(exact_loads('{"a":[1,null],"b":true}'), exact_loads('{"b":true,"a":[1.0,null]}')))
        self.assertFalse(same(exact_loads('[1,2]'), exact_loads('[2,1]')))
        self.assertFalse(same(exact_loads('"1"'), exact_loads('1')))

    def test_large_integers(self):
        self.assertFalse(same(exact_loads('9007199254740992'), exact_loads('9007199254740993')))

    def test_tiny_exponents(self):
        self.assertFalse(same(exact_loads('1e-4000'), exact_loads('0')))
        self.assertTrue(same(exact_loads('10e-4001'), exact_loads('1e-4000')))

    def test_precision_context_cannot_round_parser(self):
        with localcontext() as ctx:
            ctx.prec=2
            self.assertFalse(same(exact_loads('1.0000000000000001'), exact_loads('1')))

    def test_non_json_constants_rejected(self):
        for value in ['NaN','Infinity','-Infinity']:
            with self.assertRaises(ValueError): exact_loads(value)

    def test_already_rounded_float_rejected(self):
        with self.assertRaises(TypeError): same(json.loads('1.0000000000000001'), json.loads('1.0'))

    def test_wire_request_and_response_do_not_round(self):
        seen=[]
        def transport(request, timeout):
            seen.append(request.data)
            return Response(b'{"n":1.0000000000000001}')
        with patch('stage1_http.urllib.request.urlopen', transport):
            _, parsed=Checks('http://unused.invalid').request('POST','/reservations',raw_body=numeric_body('1.0000000000000001'))
        self.assertIn(b'1.0000000000000001', seen[0])
        self.assertEqual(parsed['n'], Decimal('1.0000000000000001'))
        self.assertNotEqual(parsed['n'], Decimal('1'))

    def test_export_transport_preserves_raw_bytes(self):
        raw=b'{"track":"tablekeeper","format_version":1,"state":{"n":1.0000000000000001}}'
        seen=[]
        def transport(request, timeout):
            seen.append(request.data)
            return Response(raw)
        with patch('stage1_http.urllib.request.urlopen', transport):
            checks=Checks('http://unused.invalid')
            _, snapshot=checks.request('GET','/_test/export',raw_response=True)
            checks.request('POST','/_test/import',raw_body=snapshot)
        self.assertEqual(seen[1],raw)


class LegacyOracleControls(unittest.TestCase):
    def sample(self):
        created={'starts_at_local':'2030-06-01T19:00','created_at':'2026-10-05T01:00:00+00:00'}
        restaurant=fixture()['restaurants'][0]
        terms={k:restaurant[k] for k in ['slot_minutes','reservation_duration_minutes','cancellation_cutoff_minutes','opening_hours']}
        terms.update(policy_version=0,capacities={'t1':4,'t2':4})
        entries=[{'event':'created','seq':1,'revision':1,'at':created['created_at'],'accepted_terms':terms,
                  'changes':[{'field':'table_id','from':None,'to':'t1'}, {'field':'starts_at_local','from':None,'to':created['starts_at_local']}, {'field':'party_size','from':None,'to':4}]},
                 {'event':'changed','seq':2,'revision':2,'at':'2026-10-05T01:00:10+00:00','accepted_terms':terms,'changes':[{'field':'table_id','from':'t1','to':'t2'}]},
                 {'event':'cancelled','seq':3,'revision':3,'at':'2026-10-05T01:00:20+00:00','accepted_terms':terms,'changes':[]}]
        windows=[(dt.datetime.fromisoformat(e['at']),dt.datetime.fromisoformat(e['at'])+dt.timedelta(milliseconds=100)) for e in entries]
        return entries,created,windows

    def test_actual_history_control(self):
        entries,created,windows=self.sample()
        check_history(entries,created,windows)

    def test_current_table_cannot_replace_creation_fact(self):
        entries,created,windows=self.sample()
        entries=copy.deepcopy(entries);entries[0]['changes'][0]['to']='t2'
        with self.assertRaises(AssertionError):check_history(entries,created,windows)

    def test_creation_time_cannot_become_cancel_time(self):
        entries,created,windows=self.sample()
        entries=copy.deepcopy(entries);entries[2]['at']=entries[0]['at']
        with self.assertRaises(AssertionError):check_history(entries,created,windows)


if __name__=='__main__': unittest.main(verbosity=2)
