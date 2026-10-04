"""JSON values with exact base-10 numbers, including portable receipt bodies.

The standard decoder preserves integer digits but rounds decimal/exponent tokens
through binary floats. Decimal construction and tuple inspection are exact and
independent of the ambient arithmetic context. Serialization emits JSON numbers,
never quoted strings or tagged objects.
"""
import json
import math
from decimal import Decimal


def reject_constant(value):
    raise ValueError('Non-finite values are not JSON numbers')


def loads(raw):
    return json.loads(raw,parse_float=Decimal,parse_constant=reject_constant)


def dumps(value):
    if value is None: return 'null'
    if value is True: return 'true'
    if value is False: return 'false'
    if isinstance(value,str): return json.dumps(value,ensure_ascii=False)
    if isinstance(value,int): return str(value)
    if isinstance(value,Decimal):
        if not value.is_finite(): raise ValueError('Non-finite JSON number')
        return str(value)
    if isinstance(value,float):
        if not math.isfinite(value): raise ValueError('Non-finite JSON number')
        return json.dumps(value,allow_nan=False)
    if isinstance(value,list): return '['+','.join(dumps(item) for item in value)+']'
    if isinstance(value,dict):
        if any(not isinstance(key,str) for key in value): raise TypeError('JSON keys must be strings')
        return '{'+','.join(dumps(key)+':'+dumps(item) for key,item in value.items())+'}'
    raise TypeError('Unsupported JSON value')


def number_identity(value):
    # Decimal(int) and Decimal(str(float)) do not apply context precision.
    number=value if isinstance(value,Decimal) else Decimal(str(value))
    if not number.is_finite(): raise ValueError('Non-finite JSON number')
    sign,digits,exponent=number.as_tuple()
    if not any(digits): return [0,[0],0]
    digits=list(digits)
    while digits[-1]==0:
        digits.pop()
        exponent+=1
    return [sign,digits,exponent]
