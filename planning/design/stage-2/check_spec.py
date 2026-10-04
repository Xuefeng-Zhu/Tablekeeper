"""Static planning checks only; does not prove UI, browser or service behavior."""
import hashlib, json, pathlib, re
from datetime import datetime, timezone
root = pathlib.Path(__file__).resolve().parent
spec = (root / 'interaction-specification.md').read_text()
css = (root / 'tokens.css').read_text()
colours = dict(re.findall(r'--([a-z-]+):\s*(#[0-9a-f]{6});', css))
def lum(h):
    v = [int(h[i:i+2], 16)/255 for i in (1,3,5)]
    v = [x/12.92 if x <= .04045 else ((x+.055)/1.055)**2.4 for x in v]
    return sum(a*b for a,b in zip(v, (.2126,.7152,.0722)))
def contrast(a,b):
    x,y = sorted((lum(colours[a]),lum(colours[b])))
    return (y+.05)/(x+.05)
pairs = [('text','paper',4.5),('ink','paper',4.5),('muted','paper',4.5),
 ('muted','unavailable-bg',4.5),('surface','accent',4.5),('surface','accent-hover',4.5),
 ('ink','selected-bg',4.5),('success-text','success-bg',4.5),('error-text','error-bg',4.5),
 ('uncertain-text','uncertain-bg',4.5),('control-line','surface',3),('control-line','paper',3),
 ('focus','surface',3),('focus','paper',3),('focus','selected-bg',3)]
results = [dict(foreground=a, background=b, ratio=round(contrast(a,b),3), minimum=m,
                result='PASS' if contrast(a,b)>=m else 'FAIL') for a,b,m in pairs]
ids = ['signup-email','signup-password','signup-display-name','signup-submit','login-email',
 'login-password','login-submit','auth-error','current-user','logout-button','restaurant-select',
 'date-input','party-size-input','search-button','availability-grid','slot-{table_id}-{HH:MM}',
 'no-slots','booking-form','booking-summary','booking-party-size','booking-submit','booking-error',
 'booking-uncertain','confirmation','confirmation-reference','confirmation-details',
 'lookup-reference-input','lookup-submit','reservation-detail','reservation-status',
 'reservation-cancel-button','reservation-error','slot-{t_a}+{t_b}-{HH:MM}',
 'confirmation-tables','reservation-tables']
missing = [v for v in ids if '`'+v+'`' not in spec]
sources = {'stage-1.md':'9460189eac83802ce158f16ee90989af728a489b32a6147e2dc8e320f383055f',
 'stage-2.md':'b1aa1b4affad456ff26f208a378eb2f6884153fc6ef667ab37b888fcda96c5dc'}
hashes = {k:hashlib.sha256((pathlib.Path('/Users/frank/mygit/Tablekeeper/challenge/tablekeeper/spec')/k).read_bytes()).hexdigest() for k in sources}
ok = not missing and all(r['result']=='PASS' for r in results) and hashes==sources
print(json.dumps(dict(work_item='TK-S2-DESIGN',timestamp_utc=datetime.now(timezone.utc).isoformat(),
 layer='Static planning contract and token calculation only',result='PASS' if ok else 'FAIL',
 contrast=results,required_testids=len(ids),missing_testids=missing,source_sha256=hashes,
 browser='NOT_TESTED',service='NOT_TESTED',build='NOT_TESTED'),indent=2))
raise SystemExit(0 if ok else 1)
