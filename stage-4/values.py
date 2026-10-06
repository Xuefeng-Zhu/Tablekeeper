"""Strict request fields and precision-independent JSON receipt representation."""
import json
import re
from decimal import Decimal

class APIError(Exception):
    def __init__(self, status=422, code='validation_failed'):
        self.status, self.code = status, code


def fail(status=422, code='validation_failed'):
    raise APIError(status, code)


def parse(raw):
    try:
        value = json.loads(raw, parse_int=Decimal, parse_float=Decimal,
                           parse_constant=lambda _: fail(400, 'malformed_request'))
    except (ValueError, UnicodeError, RecursionError):
        fail(400, 'malformed_request')
    if not isinstance(value, dict):
        fail(400, 'malformed_request')
    return value


def canonical(value):
    if value is None:
        return ['null']
    if isinstance(value, bool):
        return ['boolean', value]
    if isinstance(value, str):
        return ['string', value]
    if isinstance(value, (Decimal, int)):
        sign, digits, exponent = Decimal(value).as_tuple()
        coefficient = ''.join(map(str, digits))
        if not coefficient.strip('0'):
            return ['number', 0, '0', '0']
        while coefficient.endswith('0'):
            coefficient = coefficient[:-1]
            exponent += 1
        return ['number', sign, coefficient, str(exponent)]
    if isinstance(value, list):
        return ['array', [canonical(v) for v in value]]
    if isinstance(value, dict):
        return ['object', [[k, canonical(value[k])] for k in sorted(value)]]
    fail()


def validate_tree(tree):
    """Reject malformed/noncanonical imported tagged trees, without decoding numbers."""
    if not isinstance(tree, list) or not tree or not isinstance(tree[0], str):
        fail()
    tag = tree[0]
    if tag == 'null' and len(tree) == 1:
        return
    if tag == 'boolean' and len(tree) == 2 and type(tree[1]) is bool:
        return
    if tag == 'string' and len(tree) == 2 and isinstance(tree[1], str):
        return
    if tag == 'number' and len(tree) == 4:
        sign, coeff, exp = tree[1:]
        if isinstance(sign, bool) or sign not in (0, 1) or not isinstance(coeff, str) or not isinstance(exp, str):
            fail()
        if not re.fullmatch(r'0|[1-9][0-9]*', coeff) or not re.fullmatch(r'0|-?[1-9][0-9]*', exp):
            fail()
        if coeff == '0':
            if sign != 0 or exp != '0':
                fail()
        elif coeff.endswith('0'):
            fail()
        return
    if tag in ('array', 'object') and len(tree) == 2 and isinstance(tree[1], list):
        previous = None
        for child in tree[1]:
            if tag == 'object':
                if not isinstance(child, list) or len(child) != 2 or not isinstance(child[0], str):
                    fail()
                if previous is not None and child[0] <= previous:
                    fail()
                previous, child = child
            validate_tree(child)
        return
    fail()


def field(body, name, kind=str):
    if name not in body:
        fail()
    value = body[name]
    if not isinstance(value, kind):
        fail(400, 'malformed_request')
    return value


def identifier(body, name):
    value = field(body, name)
    if not 1 <= len(value) <= 64:
        fail()
    return value


def integer(value, minimum=1, party=False):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        fail(422 if party else 400, 'validation_failed' if party else 'malformed_request')
    # Compare before conversion; huge unknown numbers never allocate huge integers.
    if value < minimum:
        fail()
    if isinstance(value, Decimal) and value != value.to_integral_value():
        fail()
    return int(value)


def plain(value):
    """Snapshot fields contain only bounded integer numbers, never request decimals."""
    if isinstance(value, Decimal):
        return integer(value, minimum=-10**12)
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value
