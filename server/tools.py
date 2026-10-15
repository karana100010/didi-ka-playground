"""Tools the agents can call, their JSON schemas, and a safe calculator.

The list of her registers is put into the system prompt instead of being a tool, which saves one LLM round
trip per question on a rate-limited key. Only tables the gateway can tie to this didi are listed.
"""
import ast
import json
import math
import operator

import decisions
import gateway
from consent import catalog, ledger

DECISIONS = list(decisions.all_tables())
PLAN_TYPES = ['dairy', 'kirana', 'tailoring']

SPECS = [
    {'type': 'function', 'function': {
        'name': 'request_consent',
        'description': 'Ask the didi for permission to read fields from her registers. Ask once per question, for every '
                       'field you will need and nothing more. Returns consent_id and what she allowed.',
        'parameters': {'type': 'object', 'properties': {
            'purpose': {'type': 'string', 'enum': list(catalog.PURPOSES), 'description': 'Why the fields are needed'},
            'items': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'source': {'type': 'string', 'description': 'Source id, like src_…'},
                'table': {'type': 'string'},
                'fields': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['source', 'table', 'fields']}}},
            'required': ['purpose', 'items']}}},
    {'type': 'function', 'function': {
        'name': 'fetch_records',
        'description': 'Read her rows from one table after she gave permission. Returns only the fields she allowed.',
        'parameters': {'type': 'object', 'properties': {
            'consent_id': {'type': 'string'}, 'source': {'type': 'string'}, 'table': {'type': 'string'},
            'fields': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['consent_id', 'source', 'table', 'fields']}}},
    {'type': 'function', 'function': {
        'name': 'calculate',
        'description': 'Arithmetic on numbers. Use it for every sum, average or difference. Supports + - * / ( ) and '
                       'sum, avg, min, max, round, len, abs over variables that are numbers or lists of numbers.',
        'parameters': {'type': 'object', 'properties': {
            'expression': {'type': 'string', 'description': 'e.g. sum(income) - sum(expense)'},
            'variables': {'type': 'object', 'description': 'name -> number or list of numbers'}}, 'required': ['expression']}}},
    {'type': 'function', 'function': {
        'name': 'describe_decision',
        'description': 'What a decision table (Lakhpati status, bank-linkage readiness) needs: the fields to ask consent for '
                       'and the purpose. Call it before asking consent for a decision.',
        'parameters': {'type': 'object', 'properties': {'decision_id': {'type': 'string', 'enum': DECISIONS}},
                       'required': ['decision_id']}}},
    {'type': 'function', 'function': {
        'name': 'decide',
        'description': 'Run a decision table on her records, using a consent that covers the fields from describe_decision. '
                       'Returns the outcome, the rule that matched and the reasons. Explain it; never change the outcome.',
        'parameters': {'type': 'object', 'properties': {'decision_id': {'type': 'string', 'enum': DECISIONS},
                                                        'consent_id': {'type': 'string'}}, 'required': ['decision_id', 'consent_id']}}},
    {'type': 'function', 'function': {
        'name': 'build_business_plan',
        'description': 'Build an Excel business plan from her registers, using a consent (purpose business_plan) that covers '
                       'the plan fields listed in your instructions. changes sets inputs from her words, e.g. {"cows_to_add": 2}.',
        'parameters': {'type': 'object', 'properties': {
            'enterprise_type': {'type': 'string', 'enum': PLAN_TYPES}, 'consent_id': {'type': 'string'},
            'changes': {'type': 'object', 'description': 'input name -> number'}}, 'required': ['enterprise_type', 'consent_id']}}},
    {'type': 'function', 'function': {
        'name': 'scheme_info',
        'description': 'Short facts about a scheme or concept.',
        'parameters': {'type': 'object', 'properties': {'topic': {'type': 'string', 'enum': list(catalog.SCHEME_INFO)}},
                       'required': ['topic']}}},
]
NAMES = [s['function']['name'] for s in SPECS]


def schema_text(ctx):
    lines = []
    for source in ctx.sources.values():
        setup = source.get('setup') or {}
        if not setup.get('confirmed'):
            continue
        paused = (setup.get('policy') or {}).get('paused')
        lines.append(f'Source {source["id"]} ({source["name"]})' + (' — PAUSED by its owner, cannot be read' if paused else ''))
        for table, columns in source['tables'].items():
            if is_profile(source, table):
                continue
            try:
                _, _, group = gateway.scope(source, table, ctx.didi)
            except gateway.Blocked:
                continue
            fields = [f'{c} ({ledger.field_label(source, table, c, "en")})' for c in columns if c not in catalog.KEY_COLUMNS]
            note = ' [SHG group data: only an office bearer can allow it]' if group else ''
            lines.append(f'  - table {table}: {ledger.register(source, table, "en")}{note}. Fields: {", ".join(fields)}')
    text = '\n'.join(lines) or '(no registers connected)'
    text = f'Already known from her login (never ask consent for these): {profile_text(ctx.didi)}\n\n{text}'
    names = ', '.join(f'{d["id"]} ({d["name"]["en"]})' for d in decisions.all_tables().values())
    from plans import templates
    fields = '; '.join(f'{n["table"]} ({", ".join(n["fields"])})' for n in templates.COMMON_NEEDS)
    inputs = '; '.join(f'{k}: ' + ', '.join(a[0] for a in templates.assumption_specs(k)) for k in templates.TEMPLATES)
    return (f'{text}\n\nDecision tables you can run: {names}.\n'
            f'Business plans ({", ".join(templates.TEMPLATES)}): ask consent with purpose business_plan for {fields}, then '
            f'call build_business_plan. Inputs you may set from her words: {inputs}.')


def call(ctx, name, args):
    """Run one tool call for an agent. Always returns a JSON-able dict; problems come back as {'error': ...}."""
    if not isinstance(args, dict):
        return {'error': 'Arguments must be a JSON object.'}
    if name == 'request_consent':
        items, problem = _items(ctx, args.get('items'))
        if problem:
            return {'error': problem}
        purpose = args.get('purpose') if args.get('purpose') in catalog.PURPOSES else 'other_question'
        return ctx.consent(items, purpose)
    if name == 'fetch_records':
        fields = [f for f in args.get('fields') or [] if isinstance(f, str)]
        return ctx.fetch(str(args.get('source', '')), str(args.get('table', '')), fields, str(args.get('consent_id', '')))
    if name == 'calculate':
        return ctx.tool('calculate', args, lambda: calculate(args.get('expression', ''), args.get('variables') or {}),
                        summary=lambda r: f'{args.get("expression")} = {r.get("result", r.get("error"))}')
    if name == 'describe_decision':
        return ctx.tool('describe_decision', args, lambda: describe_decision(ctx, args.get('decision_id')),
                        summary=lambda r: r.get('error') or f'needs {", ".join(i["table"] for i in r["consent_items"])}')
    if name == 'decide':
        return ctx.decide(str(args.get('decision_id', '')), str(args.get('consent_id', '')))
    if name == 'build_business_plan':
        return ctx.build_plan(str(args.get('enterprise_type', '')), str(args.get('consent_id', '')), args.get('changes') or {})
    if name == 'scheme_info':
        topic = args.get('topic')
        return ctx.tool('scheme_info', args, lambda: {'text': catalog.SCHEME_INFO.get(topic, 'No notes on that topic.')},
                        summary=lambda r: topic)
    return {'error': f'Unknown tool {name}. Tools: {", ".join(NAMES)}.'}


def describe_decision(ctx, decision_id):
    try:
        spec = decisions.describe(decision_id)
    except decisions.DecisionError as error:
        return {'error': str(error)}
    items = []
    for need in spec['needs']:
        source_id = ctx.table_source(need['table'])
        if not source_id:
            return {'error': f'No connected source has the {need["table"]} table for her, so this decision cannot run.'}
        items.append({'source': source_id, 'table': need['table'], 'fields': need['fields']})
    return {'decision_id': spec['decision_id'], 'version': spec['version'], 'name': spec['name']['en'],
            'purpose': spec['purpose'], 'consent_items': items,
            'inputs': [i['label']['en'] for i in spec['inputs']], 'outcomes': {k: v['en'] for k, v in spec['outcomes'].items()}}


def is_profile(source, table):
    """Her member row and SHG profile: already known from her login, so never worth a consent prompt."""
    setup = source.get('setup') or {}
    return (setup.get('role') == 'primary' and table == setup.get('users_table')) or table == 'shg'


def profile_text(didi):
    keep = ('name', 'name_local', 'role', 'shg_name', 'village', 'block', 'district', 'state', 'language')
    return ', '.join(f'{k}: {didi[k]}' for k in keep if didi.get(k))


def _items(ctx, raw):
    if not isinstance(raw, list) or not raw:
        return None, 'items must list at least one {source, table, fields}.'
    items = []
    for item in raw:
        source = ctx.sources.get(str((item or {}).get('source', '')))
        if not source:
            return None, f'Unknown source {item.get("source")!r}. Use a source id from the list in your instructions.'
        table = item.get('table')
        if table in source['tables'] and is_profile(source, table):
            continue  # already known from her login; dropped rather than asked
        if table not in source['tables']:
            return None, f'{source["id"]} has no table {table!r}. Tables: {", ".join(source["tables"])}.'
        fields = [f for f in item.get('fields') or [] if f in source['tables'][table] and f not in catalog.KEY_COLUMNS]
        unknown = [f for f in item.get('fields') or [] if f not in source['tables'][table]]
        if unknown:
            return None, f'Table {table} has no field {", ".join(map(repr, unknown))}.'
        if fields:
            items.append({'source_id': source['id'], 'table': table, 'fields': list(dict.fromkeys(fields))})
    if not items:
        return None, 'No readable fields were listed (key columns like didi_id are applied automatically).'
    return items, None


# --- calculator ----------------------------------------------------------------------------

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.USub: operator.neg, ast.UAdd: operator.pos}


def _avg(values):
    values = list(values)
    return sum(values) / len(values) if values else 0


_FUNCS = {'sum': sum, 'avg': _avg, 'mean': _avg, 'min': min, 'max': max, 'round': round, 'len': len, 'abs': abs}


def calculate(expression, variables):
    try:
        tree = ast.parse(str(expression), mode='eval')
        value = _eval(tree.body, {k: _number(v) for k, v in variables.items()})
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, KeyError) as error:
        return {'error': f'Could not calculate: {error}'}
    if isinstance(value, float):
        value = round(value, 2)
        if value.is_integer():
            value = int(value)
    return {'result': value}


def _number(value):
    if isinstance(value, list):
        return [_number(v) for v in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    try:
        return float(str(value).replace(',', '').replace('₹', ''))
    except ValueError as error:
        raise ValueError(f'{value!r} is not a number') from error


def _eval(node, names):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise KeyError(f'unknown variable {node.id}')
        return names[node.id]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, names) for e in node.elts]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _UN[type(node.op)](_eval(node.operand, names))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left, names), _eval(node.right, names)
        if isinstance(node.op, ast.Pow) and abs(right) > 12:
            raise ValueError('exponent too large')
        return _BIN[type(node.op)](left, right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        args = [_eval(a, names) for a in node.args]
        result = _FUNCS[node.func.id](*args) if node.func.id != 'round' else round(*args)
        if isinstance(result, float) and not math.isfinite(result):
            raise ValueError('result is not a finite number')
        return result
    raise ValueError(f'"{ast.unparse(node)}" is not allowed')


def tool_message(call_id, result):
    return {'role': 'tool', 'tool_call_id': call_id, 'content': json.dumps(result, ensure_ascii=False, default=str)}
