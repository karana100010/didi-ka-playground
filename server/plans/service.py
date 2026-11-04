"""Building a plan for one didi: derive assumptions, apply the agent's changes (checked against ranges, and
against her own words), compute, write the workbook, store it, and return a summary the agent can explain."""
import re
import unicodedata

from core import files, store

from . import builder, templates

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


NUMBER_WORDS = {'एक': 1, 'दो': 2, 'तीन': 3, 'चार': 4, 'पाँच': 5, 'पांच': 5, 'छह': 6, 'छः': 6, 'छ': 6, 'सात': 7, 'आठ': 8, 'नौ': 9,
                'दस': 10, 'बीस': 20, 'पचास': 50, 'सौ': 100, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
                'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'twenty': 20, 'fifty': 50, 'hundred': 100}
NUMBER_WORDS.update({'डेढ': 1.5, 'ढाई': 2.5})
SCALES = {'हजार': 1000, 'thousand': 1000, 'लाख': 100000, 'lakh': 100000, 'lakhs': 100000}
_TOKEN = re.compile(r'\d+(?:[.,]\d+)*|[^\s\d.,!?।%()"\'-]+')


def said_numbers(text):
    """Every number in her words: digits (Devanagari too), number words, and "50 हज़ार" / "डेढ़ लाख"-style amounts."""
    text = unicodedata.normalize('NFD', text or '').replace('\u093c', '')  # ज़ and ज are one letter here
    tokens = _TOKEN.findall(text.translate(str.maketrans('०१२३४५६७८९', '0123456789')).lower())
    found, last = set(), None
    for token in tokens:
        if token in SCALES and last is not None:
            found.add(last * SCALES[token])
        value = NUMBER_WORDS.get(token) if not token[0].isdigit() else float(token.replace(',', ''))
        if value is not None:
            found.add(float(value))
        last = value
    return found


def apply_changes(enterprise, assumptions, changes, said=None):
    """Changes the agent proposed from her words, e.g. {"cows_to_add": 2}. Out-of-range or unknown keys are refused,
    and so is a value she never said (`said`: her words), so the agent cannot replace her register figures with
    numbers of its own."""
    heard = said_numbers(said) if said is not None else None
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
        if heard is not None and number not in heard:
            refused.append(f'{key}: she did not say {number:g}; her registers or the template give this input')
            continue
        assumptions[key] = {'value': int(number) if number.is_integer() else number, 'source': 'Her words, through the agent'}
    return refused


def build(ctx, enterprise, rows, changes, consent_id, read_at):
    assumptions = templates.derive(enterprise, rows)
    said = ' '.join([q for q, _ in ctx.history] + [ctx.question])
    refused = apply_changes(enterprise, assumptions, changes, said)
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
