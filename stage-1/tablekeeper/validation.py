import json
import re
from decimal import Decimal

class Problem(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)

def fail(code='validation_failed', status=422):
    raise Problem(status, code)

def string(body, key, required=True, maximum=None):
    if key not in body:
        if required: fail()
        return None
    value = body[key]
    if not isinstance(value, str): fail('malformed_request', 400)
    if not value or (maximum is not None and len(value) > maximum): fail()
    return value

def identifier(body, key):
    return string(body, key, maximum=64)

def party(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 1 or value != int(value): fail()
    return int(value)

def query_party(value):
    if not value or not re.fullmatch(r'[0-9]+', value): fail()
    return party(int(value))

def parse(raw):
    try:
        value = json.loads(raw, parse_constant=lambda _: fail('malformed_request',400))
    except (ValueError, UnicodeError): fail('malformed_request',400)
    if not isinstance(value, dict): fail('malformed_request',400)
    return value

def canonical(value):
    # Typed structural representation: true differs from 1; 1 and 1.0 agree.
    if value is None: return ['null']
    if isinstance(value, bool): return ['bool', value]
    if isinstance(value, (int,float)):
        return ['number', str(Decimal(str(value)).normalize())]
    if isinstance(value, str): return ['string', value]
    if isinstance(value, list): return ['array', [canonical(v) for v in value]]
    return ['object', [[k,canonical(value[k])] for k in sorted(value)]]
