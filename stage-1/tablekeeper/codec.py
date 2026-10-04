"""Exact JSON numbers without process-wide integer conversion setting changes."""
import json as _json
from decimal import Decimal


def loads(value, **options):
    options.setdefault('parse_int', lambda digits: int(Decimal(digits)))
    options.setdefault('parse_float', Decimal)
    return _json.loads(value, **options)


def dumps(value, **options):
    # Application DTO keys are strings. String escaping remains the stdlib's job.
    if value is None: return 'null'
    if value is True: return 'true'
    if value is False: return 'false'
    if isinstance(value, str): return _json.dumps(value, ensure_ascii=options.get('ensure_ascii', True))
    if isinstance(value, int): return str(Decimal(value))
    if isinstance(value, Decimal):
        if not value.is_finite(): raise ValueError('Nonfinite JSON number')
        return str(value)
    if isinstance(value, float): return _json.dumps(value, allow_nan=False)
    if isinstance(value, (list, tuple)): return '['+','.join(dumps(x, **options) for x in value)+']'
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value): raise TypeError('JSON object key must be string')
        return '{'+','.join(dumps(k, **options)+':'+dumps(v, **options) for k,v in value.items())+'}'
    raise TypeError('Unsupported JSON value')
