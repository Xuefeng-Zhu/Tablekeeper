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
