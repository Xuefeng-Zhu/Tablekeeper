"""Architecture-only host experiments; not product implementation or release tests."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from itertools import product
import json, platform, sqlite3, time
from pathlib import Path

BASE = Path(__file__).parent
started = datetime.now(timezone.utc).isoformat()
results = []

def candidates(text, zone):
    naive = datetime.fromisoformat(text)
    tz = ZoneInfo(zone)
    options = []
    for fold in (0, 1):
        instant = naive.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc)
        if instant.astimezone(tz).replace(tzinfo=None) == naive:
            options.append(instant)
    return sorted(set(options))

for zone, gap, repeated in [
    ('Europe/Berlin', '2026-03-29T02:30', '2026-10-25T02:30'),
    ('America/New_York', '2026-03-08T02:30', '2026-11-01T01:30'),
]:
    assert not candidates(gap, zone)
    opts = candidates(repeated, zone)
    assert len(opts) == 2 and opts[1] - opts[0] == timedelta(hours=1)
    first = opts[0]
    end = (first + timedelta(minutes=90)).astimezone(ZoneInfo(zone))
    expected = '2026-10-25T03:00:00+01:00' if zone == 'Europe/Berlin' else '2026-11-01T02:00:00-05:00'
    assert end.isoformat() == expected
    results.append({'probe': 'DST', 'zone': zone, 'gap_candidates': 0,
                    'first': first.astimezone(ZoneInfo(zone)).isoformat(), 'end': end.isoformat(), 'outcome':'PASS'})

def canonical(value):
    if value is None: return ['null']
    if isinstance(value, bool): return ['bool', value]
    if isinstance(value, Decimal):
        # Decimal equality is exact, independent of lexical numeric representation.
        t = value.as_tuple()
        digits = list(t.digits); exponent = t.exponent
        if not any(digits): return ['number', '0']
        while digits[-1] == 0:
            digits.pop(); exponent += 1
        return ['number', ('-' if t.sign else '') + ''.join(map(str,digits)) + 'e' + str(exponent)]
    if isinstance(value, str): return ['string', value]
    if isinstance(value, list): return ['array', [canonical(v) for v in value]]
    return ['object', [[k, canonical(value[k])] for k in sorted(value)]]

def parse(text):
    return json.loads(text, parse_int=Decimal, parse_float=Decimal,
                      parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
assert canonical(parse('{"a":1,"b":[true,null]}')) == canonical(parse('{"b":[true,null],"a":1.0}'))
assert canonical(parse('true')) != canonical(parse('1'))
assert canonical(parse('1000')) == canonical(parse('1e3'))
assert canonical(parse('9007199254740993')) != canonical(parse('9007199254740992'))
results.append({'probe':'typed JSON equality','outcome':'PASS', 'note':'Numeric lexical equivalence is a recommended interpretation pending independent challenge.'})

db=sqlite3.connect(':memory:')
db.execute('create table state (id integer primary key, value text)')
db.execute("insert into state values (1,'before')"); db.commit()
try:
    with db:
        db.execute("update state set value='partial'")
        raise ValueError('synthetic failed write')
except ValueError: pass
assert db.execute('select value from state').fetchone()[0] == 'before'
results.append({'probe':'SQLite transaction rollback','outcome':'PASS','layer':'host mechanism only'})

t=time.perf_counter(); leaves=0; best=None
for ranks in product(range(10), repeat=6):
    # Full 10^6 objective enumeration, not an occupancy solver or container benchmark.
    objective=(sum(r != i for i,r in enumerate(ranks)), sum(ranks), ranks)
    if best is None or objective < best: best=objective
    leaves+=1
elapsed=time.perf_counter()-t
assert leaves == 1000000
results.append({'probe':'planner leaf bound','leaves':leaves,'elapsed_seconds':elapsed,
                'outcome':'PASS','layer':'host objective enumeration only; container solver budget NOT_TESTED'})
report={'work_item':'TK-ARCH-PLAN','revision':'681a3c2ea6bf317a75a20950f880bb6f939f9c6d',
        'responsible':'@frankzhu94/factory-architect','start_utc':started,
        'end_utc':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),
        'sqlite':sqlite3.sqlite_version,'results':results,'usage':'UNAVAILABLE'}
(BASE/'probe-results.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
