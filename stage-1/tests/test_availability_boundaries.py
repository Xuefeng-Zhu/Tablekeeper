"""Permanent regressions for QA-S1-DATE-MAX and QA-S1-QUERY-DIGITS."""
import unittest
from decimal import Decimal,localcontext
from tablekeeper.core import Error,query_count,timing
from tablekeeper.service import Service


def restaurant(zone='UTC',opens='18:00',closes='23:00',duration=90):
    return dict(id='r',name='Room',timezone=zone,slot_minutes=30,
                reservation_duration_minutes=duration,cancellation_cutoff_minutes=0,
                opening_hours=[dict(weekday=d,opens=opens,closes=closes)
                               for d in ['mon','tue','wed','thu','fri','sat','sun']],
                tables=[dict(id='a',label='A',capacity=2),dict(id='b',label='B',capacity=4)])


class AvailabilityBoundaries(unittest.TestCase):
    def slots(self,day='2096-09-24',party='2',r=None):
        return Service().availability({'restaurants':[r or restaurant()], 'reservations':[]},
                                      dict(restaurant_id='r',date=day,party_size=party))['slots']

    def test_last_calendar_day_retains_all_fitting_slots(self):
        for day in ['9999-12-31','9999-12-30','0001-01-01','2000-02-29','2400-02-29']:
            with self.subTest(day=day):
                slots=self.slots(day)
                self.assertEqual([x['starts_at_local'][11:] for x in slots],
                                 ['18:00','18:30','19:00','19:30','20:00','20:30','21:00','21:30'])
                self.assertTrue(all(x['available_table_ids']==['a','b'] for x in slots))
        self.assertEqual(timing(restaurant(),'9999-12-31T21:30')[1],'9999-12-31T23:00:00+00:00')
        for local in ['9999-12-31T22:00','9999-12-31T22:30']:
            with self.assertRaises(Error) as raised: timing(restaurant(),local)
            self.assertEqual(raised.exception.code,'outside_opening_hours')

    def test_duration_larger_than_timedelta_range_is_outside_hours(self):
        self.assertEqual(self.slots('9999-12-31',r=restaurant(duration=10**30)),[])
        with self.assertRaises(Error) as raised: timing(restaurant(duration=10**30),'9999-12-31T18:00')
        self.assertEqual(raised.exception.code,'outside_opening_hours')

    def test_ascii_counts_preserve_unbounded_value_and_leading_zeros(self):
        with localcontext() as context:
            context.prec=2
            self.assertEqual(query_count('9'*5000),Decimal('9'*5000))
            self.assertGreater(query_count('9'*5000),query_count('9'*4999))
        for party,available in [('9'*5000,[]),('0'*5000+'2',['a','b']),('0004',['b']),('5',[])]:
            with self.subTest(length=len(party)):
                slots=self.slots(party=party)
                self.assertEqual(len(slots),8)
                self.assertTrue(all(s['available_table_ids']==available for s in slots))
        for invalid in ['0','0'*5000,'','-1','+2','2.0','2e0','٢','２',' 2','2\n']:
            with self.subTest(value=invalid[:10]):
                with self.assertRaises(Error) as raised: self.slots(party=invalid)
                self.assertEqual((raised.exception.status,raised.exception.code),(422,'validation_failed'))

    def test_closing_comparison_uses_elapsed_time_across_dst(self):
        spring=restaurant('America/New_York','01:00','04:00',90)
        slots=self.slots('2026-03-08',r=spring)
        self.assertEqual([s['starts_at_local'][11:] for s in slots],['01:00','01:30'])
        self.assertEqual(timing(spring,'2026-03-08T01:30')[1],'2026-03-08T04:00:00-04:00')
        fall=restaurant('America/New_York','00:00','02:00',90)
        self.assertEqual(timing(fall,'2026-11-01T01:30'),
                         ('2026-11-01T01:30:00-04:00','2026-11-01T02:00:00-05:00'))
