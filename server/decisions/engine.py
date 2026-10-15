"""A small decision-table evaluator: hit policies FIRST, UNIQUE and COLLECT, and a FEEL subset for conditions.

Tables are JSON files in tables/. Each input names a derivation in derive.py, which computes it from the
consented rows. The LLM never computes an input or an outcome; it gathers consent and explains the result.

Conditions: "-" (any), ">= 100000", "> 10000", "< 4", "= 5", "[60000..100000)" (ranges with [ ] ( )),
"yes" / "no" / any text (equality), "a, b" (one of).
"""
import json
import re
from pathlib import Path

from . import derive

TABLES = Path(__file__).resolve().parent / 'tables'
_RANGE = re.compile(r'^([\[\(])\s*(-?[\d.]+)\s*\.\.\s*(-?[\d.]+)\s*([\]\)])$')
_COMPARE = re.compile(r'^(>=|<=|>|<|=)\s*(-?[\d.]+)$')
_cache = {}


class DecisionError(Exception):
    pass


def all_tables():
    if not _cache:
        for path in sorted(TABLES.glob('*.json')):
            data = json.loads(path.read_text(encoding='utf-8'))
            _cache[data['id']] = data
    return _cache


def table(decision_id):
    found = all_tables().get(decision_id)
    if not found:
        raise DecisionError(f'Unknown decision {decision_id!r}. Decisions: {", ".join(all_tables())}.')
    return found


def describe(decision_id):
    t = table(decision_id)
    return {'decision_id': t['id'], 'version': t['version'], 'name': t['name'], 'purpose': t['purpose'],
            'needs': t['needs'], 'inputs': [{'name': i['name'], 'label': i['label']} for i in t['inputs']],
            'outcomes': t['outcomes']}


def inputs_from_rows(decision_id, rows_by_table):
    """Inputs derived by code from the consented rows {table: rows}."""
    t = table(decision_id)
    out = {}
    for spec in t['inputs']:
        fn = getattr(derive, spec['derive'], None)
        if fn is None:
            raise DecisionError(f'No derivation called {spec["derive"]}.')
        out[spec['name']] = fn(rows_by_table)
    return out


def matches(condition, value):
    condition = str(condition).strip()
    if condition in ('', '-'):
        return True
    if value is None:
        return False
    found = _COMPARE.match(condition)
    if found:
        op, limit = found.group(1), float(found.group(2))
        number = float(value)
        return {'>=': number >= limit, '<=': number <= limit, '>': number > limit, '<': number < limit, '=': number == limit}[op]
    found = _RANGE.match(condition)
    if found:
        low, high, number = float(found.group(2)), float(found.group(3)), float(value)
        return (number >= low if found.group(1) == '[' else number > low) and (number <= high if found.group(4) == ']' else number < high)
    options = [o.strip().lower() for o in condition.split(',')]
    return str(value).strip().lower() in options


def evaluate(decision_id, inputs):
    """Run the table. Returns outcome, matched rules, reasons (en, hi) and version."""
    t = table(decision_id)
    missing = [i['name'] for i in t['inputs'] if inputs.get(i['name']) is None]
    hits = [(n, rule) for n, rule in enumerate(t['rules'], 1)
            if all(matches(cond, inputs.get(name)) for name, cond in rule['when'].items())]
    policy = t['hit_policy']
    if policy == 'UNIQUE' and len(hits) > 1:
        raise DecisionError(f'{decision_id}: rules {[n for n, _ in hits]} all match; a UNIQUE table allows one.')
    if policy in ('FIRST', 'UNIQUE'):
        hits = hits[:1]
        outcome = hits[0][1]['outcome'] if hits else None
    else:  # COLLECT: every matching rule is a finding; the aggregate turns them into one outcome
        outcome = t['aggregate']['any'] if hits else t['aggregate']['none']
    shown = {k: _money(k, v) for k, v in inputs.items()}
    return {
        'decision_id': t['id'], 'version': t['version'], 'hit_policy': policy, 'outcome': outcome,
        'outcome_label': t['outcomes'].get(outcome, {}), 'matched_rules': [n for n, _ in hits],
        'findings': [rule['outcome'] for _, rule in hits] if policy == 'COLLECT' else [],
        'reasons': [{lang: rule['reason'][lang].format(**shown) for lang in ('en', 'hi')} for _, rule in hits],
        'basis': {f'rule_{n}': rule.get('basis', 'simulation') for n, rule in hits},
        'inputs': inputs, 'missing_inputs': missing,
    }


def _money(name, value):
    if isinstance(value, (int, float)) and ('income' in name or 'amount' in name):
        return '₹' + indian(value)
    return value


def indian(number):
    """1,24,800-style grouping."""
    whole = str(int(round(number)))
    sign = '-' if whole.startswith('-') else ''
    whole = whole.lstrip('-')
    if len(whole) <= 3:
        return sign + whole
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return sign + ','.join([head, *groups, tail]) if head else sign + ','.join([*groups, tail])
