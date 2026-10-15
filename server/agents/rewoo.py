"""ReWOO: a planner writes every step with placeholders (#E1, #E2.income), the tools run with no LLM in
between, and a solver answers from the evidence. Two LLM calls, but no adapting mid-way."""
import json
import re

import tools

from . import facts, prompts
from .context import clean_answer

NAME = 'ReWOO'

PLANNER = """Plan every step needed to answer the didi's question. Do not answer it. Reply with JSON only:
{"steps": [{"id": "E1", "tool": "<tool>", "args": {...}, "why": "<short>"}, ...]}

Tools and arguments:
- request_consent {"purpose": "<one of: %s>", "items": [{"source": "<source id>", "table": "<table>", "fields": [...]}]}
  Ask once, for every field you will need.
- fetch_records {"consent_id": "#E1", "source": "<source id>", "table": "<table>", "fields": [...]}
- calculate {"expression": "sum(income) - sum(expense)", "variables": {"income": "#E2.income", "expense": "#E2.expense"}}
  "#E2.income" means the income column of step E2's rows; "#E2.income[-12:]" means its last 12 values.
- scheme_info {"topic": "<one of: %s>"}
- decide {"decision_id": "<lakhpati_status or bank_linkage_readiness>", "consent_id": "#E1"}
  For Lakhpati status or bank-linkage readiness, ask consent for the fields listed under the decision below, then
  decide; the table decides the outcome. Lakhpati needs dar: month, income, expense, cycle_no. Bank-linkage
  readiness needs meetings: date, held, savings_collected, loans_given, repayments_due, repayments_made, books_updated.
Use "#E1" wherever a later step needs the consent id from step E1.
If the question needs none of her records, reply {"steps": []}.

Her registers (source id, table, fields):
%s"""

SOLVER = """
Answer her question using only the evidence below. The figures are exact; use them as they are. Say which
register they came from. If a step failed or she refused, say kindly what you could not check.

Plan and evidence:
%s"""

_REF = re.compile(r'^#(E\d+)(?:\.([A-Za-z_][A-Za-z0-9_]*))?(?:\[(-?\d*):(-?\d*)\])?$')


def run(ctx):
    from consent import catalog
    planner = PLANNER % (', '.join(catalog.PURPOSES), ', '.join(catalog.SCHEME_INFO), tools.schema_text(ctx))
    history = [m for q, a in ctx.history for m in ({'role': 'user', 'content': q}, {'role': 'assistant', 'content': a})]
    text = ctx.llm([{'role': 'system', 'content': planner}, *history, {'role': 'user', 'content': ctx.question}],
                   max_tokens=800, temperature=0)['content']
    steps = _steps(text)
    ctx.emit('plan', steps=[f'{s.get("id")}: {s.get("tool")} — {s.get("why", "")}' for s in steps])
    evidence = {}
    for step in steps[:8]:
        args = _substitute(step.get('args') or {}, evidence)
        result = tools.call(ctx, step.get('tool', ''), args)
        if step.get('tool') == 'fetch_records' and 'rows' in result:
            result = {**result, 'summary': ctx.tool('summarise', {'table': args.get('table')},
                                                    lambda r=result, t=args.get('table'): facts.summarise(t, r['rows']),
                                                    summary=lambda s: 'summarised')}
        evidence[str(step.get('id'))] = result
    shown = [{'step': s.get('id'), 'tool': s.get('tool'), 'why': s.get('why'),
              'result': _brief(evidence.get(str(s.get('id'))))} for s in steps[:8]]
    solver = prompts.system(ctx.didi, ctx.llm_language, SOLVER % json.dumps(shown, ensure_ascii=False, default=str, indent=1))
    return clean_answer(ctx.llm([{'role': 'system', 'content': solver}, *history, {'role': 'user', 'content': ctx.question}])['content'])


def _steps(text):
    try:
        data = json.loads(text[text.index('{'):text.rindex('}') + 1])
        return [s for s in data.get('steps') or [] if isinstance(s, dict)]
    except ValueError:
        return []


def _substitute(value, evidence):
    if isinstance(value, dict):
        return {k: _substitute(v, evidence) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, evidence) for v in value]
    if isinstance(value, str):
        match = _REF.match(value.strip())
        if match:
            result = evidence.get(match.group(1)) or {}
            if not match.group(2):
                return result.get('consent_id', result.get('result', value))
            column = [r.get(match.group(2)) for r in result.get('rows', []) if r.get(match.group(2)) is not None]
            if match.group(3) or match.group(4):
                start = int(match.group(3)) if match.group(3) else None
                stop = int(match.group(4)) if match.group(4) else None
                column = column[start:stop]
            return column
    return value


def _brief(result):
    """Evidence for the solver: summaries instead of every row."""
    if not isinstance(result, dict):
        return result
    if 'rows' in result:
        return {k: v for k, v in result.items() if k != 'rows'}
    return result
