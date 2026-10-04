import unittest
from datetime import datetime,timezone,timedelta
from tablekeeper.core import *
from tablekeeper.service import Service
class Rules(unittest.TestCase):
    def test_json(self):
        self.assertTrue(equal(parse('{"n":2,"a":[3]}'),parse('{"a":[3.0],"n":2.0}')))
        for a,b in [('{"n":true}','{"n":1}'),('{"n":9007199254740993}','{"n":9007199254740992}'),('{"a":[1,2]}','{"a":[2,1]}')]: self.assertFalse(equal(parse(a),parse(b)))
        self.assertTrue(equal(parse('{"n":1e1000}'),parse('{"n":10e999}')))
    def test_dst(self):
        for z,w,expected in [('Europe/Berlin','2026-10-25T02:30','2026-10-25T02:30:00+02:00'),('America/New_York','2026-11-01T01:30','2026-11-01T01:30:00-04:00')]:
            self.assertEqual(stamp(resolve(wall(w),ZoneInfo(z)),ZoneInfo(z)),expected)
        for z,w in [('Europe/Berlin','2026-03-29T02:30'),('America/New_York','2026-03-08T02:30')]:
            with self.assertRaises(Error): resolve(wall(w),ZoneInfo(z))
    def test_historical(self):
        z=ZoneInfo('Europe/Berlin'); u=resolve(wall('1800-01-01T12:00'),z)
        self.assertEqual(instant(stamp(u,z)),u)
        self.assertEqual(stamp(u,z)[-6:],'+00:00')
    def test_cutoff(self):
        s=Service(); b={'starts_at':'2096-01-01T12:00:00+00:00','_terms':{'cancellation_cutoff_minutes':60}}
        boundary=instant(b['starts_at'])-timedelta(minutes=60)
        s.cutoff(b,boundary-timedelta(microseconds=1))
        with self.assertRaises(Error) as e: s.cutoff(b,boundary)
        self.assertEqual(e.exception.code,'cutoff_passed')
    def test_password(self):
        p=password('synthetic password'); self.assertTrue(password('synthetic password',p)); self.assertFalse(password('wrong',p))
    def test_transaction_rollback(self):
        s=Service()
        with self.assertRaises(RuntimeError):
            with s.store.transaction(True) as state: state['users'].append({'id':'x'}); raise RuntimeError()
        with s.store.transaction() as state: self.assertEqual(state['users'],[])
if __name__=='__main__': unittest.main()
