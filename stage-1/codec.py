"""Lossless JSON transport and typed structural receipt equality."""
import json
from decimal import Decimal


def loads(text):
    def reject(value):
        raise ValueError('non JSON number')
    return json.loads(text, parse_int=Decimal, parse_float=Decimal, parse_constant=reject)


def dumps(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, Decimal)):
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError('nonfinite number')
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, list):
        return '[' + ','.join(dumps(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k)+':'+dumps(v) for k,v in value.items()) + '}'
    raise TypeError('unsupported JSON value')


def equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, Decimal)) and isinstance(b, (int, Decimal)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x,y) for x,y in zip(a,b))
    return type(a) is type(b) and a == b
