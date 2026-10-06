"""Focused target-runtime boundary oracles for shared mechanisms."""
import copy
from datetime import datetime, timedelta, timezone
from decimal import localcontext
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'stage-1'))
from values import APIError, parse, canonical, validate_tree
from domain import amendable
from temporal import instant, resolve, to_local, microseconds, MINUTE_US
from zoneinfo import ZoneInfo

class MechanismTests(unittest.TestCase):
    def test_precision_and_tag_roundtrip(self):
        for precision in (1,2,28,100):
            with localcontext() as context:
                context.prec=precision
                for a,b in [('10000000000000000000000000000','10000000000000000000000000001'),('1.0000000000000000000000000001','1.0000000000000000000000000002'),('true','1'),('["number",0,"1","0"]','1')]:
                    self.assertNotEqual(canonical(parse('{"x":'+a+'}')),canonical(parse('{"x":'+b+'}')))
                for a,b in [('1','1.0'),('1000','1e3'),('-0','0'),('1e100000','10e99999')]:
                    tree=canonical(parse('{"x":'+a+'}'))
                    self.assertEqual(tree,canonical(parse('{"x":'+b+'}')))
                    imported=json.loads(json.dumps(tree))
                    validate_tree(imported)
                    self.assertEqual(imported,tree)

    def test_range_safe_offset_roundtrip_and_duration(self):
        pairs=[('America/New_York','9999-12-31T19:00','9999-12-31T19:00:00-05:00','9999-12-31T20:30:00-05:00'),
               ('America/New_York','9999-12-30T19:00','9999-12-30T19:00:00-05:00','9999-12-30T20:30:00-05:00'),
               ('Etc/GMT-14','0001-01-01T00:30','0001-01-01T00:30:00+14:00','0001-01-01T02:00:00+14:00'),
               ('Etc/GMT+12','9999-12-31T19:00','9999-12-31T19:00:00-12:00','9999-12-31T20:30:00-12:00'),
               ('UTC','0001-01-01T00:30','0001-01-01T00:30:00+00:00','0001-01-01T02:00:00+00:00'),
               ('Europe/Berlin','2026-10-25T02:30','2026-10-25T02:30:00+02:00','2026-10-25T03:00:00+01:00'),
               ('America/New_York','2026-03-08T01:30','2026-03-08T01:30:00-05:00','2026-03-08T04:00:00-04:00')]
        for name,local,start,end in pairs:
            with self.subTest(name=name,local=local):
                zone=ZoneInfo(name)
                ticks=resolve(datetime.fromisoformat(local),zone)
                self.assertEqual(ticks,instant(start))
                self.assertEqual(to_local(ticks,zone).isoformat(),start)
                self.assertEqual(to_local(ticks+90*MINUTE_US,zone).isoformat(),end)
                self.assertEqual(instant(end)-instant(start),90*MINUTE_US)
                self.assertEqual(instant(to_local(ticks,zone).isoformat()),ticks)
        self.assertEqual(instant('9999-12-31T19:00:00-05:00'),instant('9999-12-31T20:00:00-04:00'))
        self.assertEqual(instant('2030-01-01T00:00:00.000001+00:00')-instant('2030-01-01T00:00:00+00:00'),1)
        for name,date in [('Europe/Berlin','2026-03-29T02:30'),('America/New_York','2026-03-08T02:30')]:
            with self.assertRaises(APIError) as error:
                resolve(datetime.fromisoformat(date),ZoneInfo(name))
            self.assertEqual(error.exception.code,'invalid_local_time')

    def test_cutoff_equality(self):
        clock=datetime(2030,1,1,tzinfo=timezone.utc)
        state={'restaurants':[{'id':'r','cancellation_cutoff_minutes':120}]}
        record={'restaurant_id':'r','status':'confirmed','starts_at':(clock+timedelta(minutes=120)).isoformat()}
        with self.assertRaises(APIError) as error:
            amendable(state,record,clock)
        self.assertEqual(error.exception.code,'cutoff_passed')
        amendable(state,record,clock-timedelta(microseconds=1))
        with self.assertRaises(APIError):
            amendable(state,record,clock+timedelta(microseconds=1))

if __name__=='__main__':unittest.main(verbosity=2)
