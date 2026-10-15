"""Building a plan for one didi: derive assumptions, apply the agent's changes (checked against ranges),
compute, write the workbook, store it, and return a summary the agent can explain."""
from core import files, store

from . import builder, templates

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def apply_changes(enterprise, assumptions, changes):
    """Changes the agent proposed from her words, e.g. {"cows_to_add": 2}. Out-of-range or unknown keys are refused."""
    specs = {key: (lo, hi, en) for key, _, en, _, lo, hi in templates.assumption_specs(enterprise)}
    refused = []
    for key, value in (changes or {}).items():
        if key not in specs:
            refused.append(f'{key}: not an input of the {enterprise} plan')
            continue
        lo, hi, en = specs[key]
        try:
            number = float(value)
        except (TypeError, ValueError):
            refused.append(f'{key}: {value!r} is not a number')
            continue
        if not lo <= number <= hi:
            refused.append(f'{key}: {number:g} is outside {lo:g}–{hi:g}')
            continue
        assumptions[key] = {'value': int(number) if number.is_integer() else number, 'source': 'Her words, through the agent'}
    return refused


def build(ctx, enterprise, rows, changes, consent_id, read_at):
    assumptions = templates.derive(enterprise, rows)
    refused = apply_changes(enterprise, assumptions, changes)
    values = {k: v['value'] for k, v in assumptions.items()}
    results = builder.compute(enterprise, values)
    provenance = {k: {'register': v['source'].split(',')[0] if 'Register' in v['source'] else '',
                      'consent_id': consent_id if 'Register' in v['source'] else '', 'read_at': read_at}
                  for k, v in assumptions.items()}
    data = builder.build(enterprise, assumptions, results, ctx.didi, provenance)
    file_id = store.new_id('file')
    name = f'business-plan-{enterprise}-{ctx.didi.get("didi_id")}.xlsx'
    ref = files.put(f'plans/{ctx.workspace}/{file_id}/{name}', data, XLSX)
    rounded = {k: (round(v) if isinstance(v, float) else v) for k, v in results.items()}
    spec = templates.TEMPLATES[enterprise]
    store.add_file(dict(id=file_id, workspace=ctx.workspace, session_id=ctx.session['id'], turn_id=ctx.turn['id'],
                        kind='business_plan', name=name, ref=ref, summary={'enterprise': enterprise, 'results': rounded},
                        created_at=store.now()))
    return {
        'file_id': file_id, 'name': name, 'enterprise': spec['name'],
        'assumptions': {k: {'value': v['value'], 'source': v['source']} for k, v in assumptions.items()},
        'results': rounded, 'refused_changes': refused,
        'notes': [builder.MOCK_NOTE['en'],
                  *([f'Costs follow Bihar (JEEViKA) norms; {ctx.didi.get("state")} norms may differ.']
                    if (ctx.didi.get('state') or 'Bihar') != 'Bihar' else [])],
        'how_to_use': 'Tell her the monthly profit now and after, the instalment, the extra cash after the instalment and '
                      'the payback months, and that the Excel file can be downloaded. Say that the costs are mock figures.',
    }
