import unittest
from decimal import Decimal,localcontext
from tablekeeper import codec
from tablekeeper.core import integer,Error,parse,equal

class NumericCodec(unittest.TestCase):
    def test_exact_roundtrip_without_int_text_guard(self):
        for digits in ['9'*5000,'1'+'0'*4999,'1234567890'*500]:
            with localcontext() as context:
                context.prec=2
                n=int(Decimal(digits)); value={'n':n,'array':[True,None,'a"\\\n',Decimal(digits+'.0')]}
                encoded=codec.dumps(value)
                self.assertIn('"n":'+digits,encoded)
                self.assertEqual(codec.loads(encoded)['n'],n)
                self.assertTrue(equal(parse(encoded),parse(codec.dumps(codec.loads(encoded)))))
    def test_numeric_types_and_party_override(self):
        for field in ['slot_minutes','reservation_duration_minutes','cancellation_cutoff_minutes','capacity']:
            for value in ['30',True,None,[],{}]:
                with self.assertRaises(Error) as raised: integer({field:value},field)
                self.assertEqual((raised.exception.status,raised.exception.code),(400,'malformed_request'))
            for value in [-1,Decimal('1.5')]:
                with self.assertRaises(Error) as raised: integer({field:value},field)
                self.assertEqual(raised.exception.status,422)
            with self.assertRaises(Error) as raised: integer({},field)
            self.assertEqual(raised.exception.status,422)
            self.assertEqual(integer({field:Decimal('30.0')},field),30)
        for value in ['30',True,None,[],{}]:
            with self.assertRaises(Error) as raised: integer({'party_size':value},'party_size')
            self.assertEqual(raised.exception.status,422)
