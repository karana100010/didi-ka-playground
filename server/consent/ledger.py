"""Consent requests and answers.

A request lists exactly which fields of which registers will be read, for one stated purpose, for this
question only. Her answer is read in this order: a button decides outright; otherwise her words are checked
against the consent vocabulary (custom words first); anything mixed or unknown goes to a small LLM. A partial
grant or a low-confidence reading is read back to her before it counts. An answer that stays unclear after
two tries is a no. Nothing is fetched on an unclear answer.
"""
import json
import re
import unicodedata

from connectors import ConnectorError
from core import store
from llm import nvidia

from . import catalog

FINAL = ('granted', 'partial', 'denied', 'withdrawn')
CONFIDENT = 0.8


# --- labels --------------------------------------------------------------------------------

def register(source, table, lang):
    names = (source.get('setup', {}).get('registers') or {}).get(table) or catalog.default_registers({table: []})[table]
    return names.get(lang) or names.get('en') or table


def field_label(source, table, column, lang):
    labels = ((source.get('setup', {}).get('fields') or {}).get(table) or {}).get(column)
    if not labels:
        hi, en, _ = catalog.FIELDS.get(column, (column.replace('_', ' '), column.replace('_', ' '), 'medium'))
        labels = {'hi': hi, 'en': en}
    return labels.get(lang) or labels.get('en') or column


def purpose_text(purpose_id, lang):
    hi, en = catalog.PURPOSES.get(purpose_id, catalog.PURPOSES['other_question'])
    return hi if lang == 'hi' else en


def _join(labels, lang):
    labels = list(dict.fromkeys(labels))
    if len(labels) < 2:
        return ''.join(labels)
    word = {'hi': 'और', 'en': 'and'}.get(lang, 'and')
    return f'{", ".join(labels[:-1])} {word} {labels[-1]}'


# --- building a request --------------------------------------------------------------------

def build(sources, items, purpose_id, language):
    """Items [{source_id, table, fields}] -> the stored items and statement {lang: {...}} for the card and speech."""
    stored = []
    for item in items:
        source = sources[item['source_id']]
        stored.append({
            'source_id': source['id'], 'source_name': source['name'], 'table': item['table'], 'fields': list(item['fields']),
            'register': {lang: register(source, item['table'], lang) for lang in ('hi', 'en')},
            'labels': {lang: [field_label(source, item['table'], f, lang) for f in item['fields']] for lang in ('hi', 'en')},
        })
    statement = {}
    for lang in ('hi', 'en'):
        statement[lang] = _statement(sources, stored, purpose_id, lang)
    if language not in ('hi', 'en'):
        statement[language] = _translated(statement['en'], language)
    return stored, statement


def _statement(sources, items, purpose_id, lang):
    lines = []
    for item in items:
        template = ((sources[item['source_id']].get('setup', {}).get('consent') or {}).get('template') or {}).get(lang) \
            or catalog.DEFAULT_TEMPLATE[lang]
        lines.append(template.replace('{source_name}', item['register'][lang]).replace('{fields}', _join(item['labels'][lang], lang))
                     .replace('{purpose}', purpose_text(purpose_id, lang)))
    intro = catalog.INTRO[lang].format(purpose=purpose_text(purpose_id, lang))
    return {'intro': intro, 'lines': lines, 'retention': catalog.RETENTION[lang], 'ask': catalog.ASK[lang],
            'speech': ' '.join([intro, *lines, catalog.RETENTION[lang], catalog.ASK[lang]])}


def _translated(english, language):
    """Machine translation for languages without written templates. Marked so the operator knows."""
    from bhashini import client as bhashini
    try:
        out = {k: bhashini.translate(v, 'en', language)['text'] if isinstance(v, str) else
               [bhashini.translate(x, 'en', language)['text'] for x in v] for k, v in english.items()}
        out['machine_translated'] = True
        return out
    except Exception:  # noqa: BLE001 - fall back to English rather than fail the question
        return {**english, 'machine_translated': False, 'fallback': 'en'}


def ensure(consent_id, ctx, items, purpose_id, statement):
    """Create the pending request once (a replayed run finds it already there)."""
    if store.get_consent(consent_id):
        return
    store.add_consent(dict(
        id=consent_id, workspace=ctx.workspace, session_id=ctx.session['id'], turn_id=ctx.turn['id'], run_id=ctx.run['id'],
        didi_id=str(ctx.didi['didi_id']), purpose={'id': purpose_id, 'hi': purpose_text(purpose_id, 'hi'),
                                                  'en': purpose_text(purpose_id, 'en')},
        items=items, statement=statement, status='pending', granted=[], refused=[], answer={'attempts': 0, 'history': []},
        interpreter=None, created_at=store.now(), decided_at=None, used_at=None, discarded_at=None))
    store.log(ctx.workspace, 'consent_requested', didi_id=str(ctx.didi['didi_id']),
              detail={'consent_id': consent_id, 'purpose': purpose_id, 'tables': [i['table'] for i in items],
                      'fields': sum(len(i['fields']) for i in items)})


def refused_tables(session_id):
    """Registers she said no to earlier in this login. The agent is not to ask for them again."""
    out = set()
    for row in store.session_consents(session_id):
        out.update(row['refused'] or [])
    return out


def covering(turn_id, items):
    """A consent already given in this turn (e.g. to the first agent in the compare view) that covers these items."""
    for row in store.turn_consents(turn_id):
        if row['status'] not in ('granted', 'partial'):
            continue
        granted = {(g['source_id'], g['table']): set(g['fields']) for g in row['granted']}
        if all(set(i['fields']) <= granted.get((i['source_id'], i['table']), set()) for i in items):
            return row
    return None


def result(row):
    """What the agent's request_consent tool returns."""
    return {'consent_id': row['id'], 'decision': row['status'], 'granted': row['granted'],
            'refused_registers': row['refused'], 'summary': (row['interpreter'] or {}).get('summary', {}).get('en', '')}


# --- answers -------------------------------------------------------------------------------

def answer(workspace, consent_id, body, language):
    row = store.get_consent(consent_id, workspace)
    if not row:
        raise ConnectorError('not_found', 'No such consent request.', 404)
    if row['status'] in FINAL:
        return _out(row, language)
    log = dict(row['answer'] or {'attempts': 0, 'history': []})
    log['history'] = [*log.get('history', []), {k: v for k, v in body.items() if v not in (None, '', [])}]
    interp = dict(row['interpreter'] or {})
    proposal = interp.pop('proposal', None)
    button, text = body.get('button'), (body.get('text') or '').strip()

    if text and proposal:  # a spoken reply to the read-back: a plain yes confirms it, a no rejects it
        heard = interpret(text, language, row, vocabulary(workspace, language))
        if heard['decision'] == 'granted' and not heard.get('partial'):
            button = 'confirm'
        elif heard['decision'] == 'denied':
            button = 'reject'
    if button == 'confirm' and proposal:
        return _decide(row, log, {**interp, **proposal, 'confirmed': True}, language)
    if button == 'reject' and proposal:
        return _again(row, log, interp, language)
    if button == 'yes':
        return _decide(row, log, {'by': 'button', 'decision': 'granted', 'granted': _all(row)}, language)
    if button == 'no':
        return _decide(row, log, {'by': 'button', 'decision': 'denied', 'granted': []}, language)
    if button == 'some':
        granted = _within(row, body.get('granted') or [])
        decision = 'denied' if not granted else 'granted' if _covers_all(row, granted) else 'partial'
        return _decide(row, log, {'by': 'button', 'decision': decision, 'granted': granted}, language)
    if not text:
        raise ConnectorError('bad_answer', 'Send a button or her words.', 422)

    reading = interpret(text, language, row, vocabulary(workspace, language))
    if reading['decision'] == 'why':
        return _explain(row, log, reading, language)
    if reading['decision'] == 'unclear':
        return _again(row, log, reading, language)
    if reading['decision'] == 'denied':
        return _decide(row, log, reading, language)
    if reading['decision'] == 'granted' and reading.get('confidence', 1) >= CONFIDENT and not reading.get('partial'):
        return _decide(row, log, reading, language)
    # A partial grant or a shaky reading: read it back and let her confirm.
    return _readback(row, log, reading, text, language)


def withdraw(workspace, consent_id):
    row = store.get_consent(consent_id, workspace)
    if not row:
        raise ConnectorError('not_found', 'No such consent request.', 404)
    if row['status'] in ('granted', 'partial', 'pending'):
        store.set_consent(consent_id, status='withdrawn', decided_at=row['decided_at'] or store.now())
        store.log(workspace, 'consent_withdrawn', didi_id=row['didi_id'], detail={'consent_id': consent_id})
    return _out(store.get_consent(consent_id), 'hi')


def _all(row):
    return [{'source_id': i['source_id'], 'table': i['table'], 'fields': i['fields']} for i in row['items']]


def _within(row, granted):
    """Only fields that were asked for can be granted."""
    asked = {(i['source_id'], i['table']): i['fields'] for i in row['items']}
    out = []
    for g in granted:
        fields = [f for f in asked.get((g.get('source_id'), g.get('table')), []) if f in (g.get('fields') or [])]
        if fields:
            out.append({'source_id': g['source_id'], 'table': g['table'], 'fields': fields})
    return out


def _covers_all(row, granted):
    have = {(g['source_id'], g['table']): set(g['fields']) for g in granted}
    return all(set(i['fields']) <= have.get((i['source_id'], i['table']), set()) for i in row['items'])


def _decide(row, log, reading, language):
    decision = reading['decision']
    granted = reading.get('granted', []) if decision != 'denied' else []
    refused = sorted(set(reading.get('refused_topics') or []) |
                     {i['table'] for i in row['items'] if not any(g['table'] == i['table'] and g['source_id'] == i['source_id'] for g in granted)})
    interp = {k: v for k, v in reading.items() if k in ('by', 'model', 'confidence', 'confirmed', 'note')}
    interp['summary'] = _summary(row, decision, granted, refused)
    store.set_consent(row['id'], status=decision, granted=granted, refused=refused, answer=log, interpreter=interp,
                      decided_at=store.now())
    store.log(row['workspace'], 'consent_decided', didi_id=row['didi_id'],
              detail={'consent_id': row['id'], 'decision': decision, 'by': interp.get('by'), 'purpose': row['purpose']['id'],
                      'tables': [i['table'] for i in row['items']], 'refused': refused})
    return _out(store.get_consent(row['id']), language)


def _pending(row, log, interp, message, language):
    store.set_consent(row['id'], answer=log, interpreter=interp)
    out = _out(store.get_consent(row['id']), language)
    out['message'] = message
    return out


def _again(row, log, reading, language):
    log['attempts'] = log.get('attempts', 0) + 1
    if log['attempts'] >= 2:
        return _decide(row, log, {'by': reading.get('by', 'default'), 'decision': 'denied', 'granted': [],
                                  'note': 'still unclear after two tries'}, language)
    message = {'hi': 'माफ़ कीजिए, मैं समझ नहीं पाई। क्या आप अनुमति देती हैं? हाँ या नहीं बोलिए।',
               'en': 'Sorry, I did not understand. Do you give permission? Please say yes or no.'}
    return _pending(row, log, {k: v for k, v in reading.items() if k in ('by', 'model')}, _localise(message, language), language)


def _explain(row, log, reading, language):
    log['attempts'] = log.get('attempts', 0) + 1
    if log['attempts'] > 2:
        return _decide(row, log, {'by': reading.get('by'), 'decision': 'denied', 'granted': [], 'note': 'no clear answer'}, language)
    message = {lang: f'{row["purpose"][lang]}, हमें यह जानकारी चाहिए। {catalog.RETENTION["hi"]} {catalog.ASK["hi"]}' if lang == 'hi'
               else f'{row["purpose"]["en"]}, we need this information. {catalog.RETENTION["en"]} {catalog.ASK["en"]}'
               for lang in ('hi', 'en')}
    return _pending(row, log, {'by': reading.get('by')}, _localise(message, language), language)


def _readback(row, log, reading, said, language):
    granted = reading.get('granted') or _all(row)
    decision = 'granted' if _covers_all(row, granted) else 'partial'
    labels = {lang: [lbl for i in row['items'] for g in granted if g['table'] == i['table']
                     for f, lbl in zip(i['fields'], i['labels'][lang]) if f in g['fields']] for lang in ('hi', 'en')}
    what = {'hi': 'यह सारी जानकारी' if decision == 'granted' else f'सिर्फ़ {_join(labels["hi"], "hi")}',
            'en': 'all of this information' if decision == 'granted' else f'only {_join(labels["en"], "en")}'}
    message = {'hi': f'आपने कहा: “{said}”। मैं समझी कि आप {what["hi"]} देखने की अनुमति दे रही हैं। क्या यह सही है?',
               'en': f'You said: “{said}”. I understood you allow {what["en"]} to be looked at. Is that right?'}
    proposal = {'decision': decision, 'granted': granted, 'refused_topics': reading.get('refused_topics', []),
                'by': reading.get('by'), 'model': reading.get('model'), 'confidence': reading.get('confidence')}
    out = _pending(row, log, {'by': reading.get('by'), 'proposal': proposal}, _localise(message, language), language)
    out['readback'] = True
    return out


def _summary(row, decision, granted, refused):
    asked = {i['table'] for i in row['items']}
    extra = [t for t in refused if t not in asked]
    names = {t: catalog.REGISTERS.get(t, (t, t)) for t in extra}
    if decision == 'denied':
        hi, en = 'समझा गया: नहीं · कोई जानकारी नहीं देखी जाएगी', 'Understood: no · nothing will be read'
    elif decision == 'partial':
        hi = 'समझा गया: सिर्फ़ ' + _join([i['register']['hi'] for i in row['items'] if any(g['table'] == i['table'] for g in granted)], 'hi')
        en = 'Understood: only ' + _join([i['register']['en'] for i in row['items'] if any(g['table'] == i['table'] for g in granted)], 'en')
    else:
        hi, en = 'समझा गया: हाँ · ' + ('सारी जानकारी' if len(row['items']) > 1 else 'यह जानकारी'), 'Understood: yes · all items'
    if extra:
        hi += ' · ' + _join([names[t][0] for t in extra], 'hi') + ' नहीं देखा जाएगा'
        en += ' · ' + _join([names[t][1] for t in extra], 'en') + ' will not be read'
    return {'hi': hi, 'en': en}


def _out(row, language):
    summary = (row['interpreter'] or {}).get('summary') or {}
    return {'consent_id': row['id'], 'status': row['status'], 'granted': row['granted'], 'refused': row['refused'],
            'summary': {'text': summary.get(language) or summary.get('en', ''), 'en': summary.get('en', '')},
            'interpreter': {k: v for k, v in (row['interpreter'] or {}).items() if k != 'proposal'}}


def _localise(message, language):
    if language in message:
        return {'text': message[language], 'en': message['en']}
    try:
        from bhashini import client as bhashini
        return {'text': bhashini.translate(message['en'], 'en', language)['text'], 'en': message['en']}
    except Exception:  # noqa: BLE001
        return {'text': message['en'], 'en': message['en']}


# --- reading her words ---------------------------------------------------------------------

def vocabulary(workspace, language):
    custom = (store.get_workspace_settings(workspace).get('vocabulary') or {}).get(language, [])
    base = catalog.VOCABULARY.get(language, catalog.VOCABULARY['en'])
    return {'custom': custom, 'base': base, 'qualifiers': catalog.QUALIFIERS.get(language, []) + catalog.QUALIFIERS['en']}


def normalise(text):
    text = unicodedata.normalize('NFC', text or '').lower()
    text = text.replace('़', '').replace('ँ', 'ं')  # nukta; chandrabindu -> anusvara
    text = re.sub(r'[।॥,.!?;:"“”\'‘’()\-]+', ' ', text)
    return ' '.join(text.split())


def _has(text, phrase):
    return f' {normalise(phrase)} ' in f' {text} '


def interpret(text, language, row, vocab):
    said = normalise(text)
    for entry in vocab['custom']:  # the operator's words win
        if normalise(entry['phrase']) == said and entry['meaning'] in ('yes', 'no', 'why'):
            return _reading(entry['meaning'], row, 'vocabulary')
    yes = any(_has(said, p) for p in vocab['base']['yes']) or any(_has(said, e['phrase']) for e in vocab['custom'] if e['meaning'] == 'yes')
    no = any(_has(said, p) for p in vocab['base']['no']) or any(_has(said, e['phrase']) for e in vocab['custom'] if e['meaning'] == 'no')
    why = any(_has(said, p) for p in vocab['base']['why'])
    qualified = any(_has(said, q) for q in vocab['qualifiers'])
    if why and not yes and not no:
        return _reading('why', row, 'vocabulary')
    if not qualified and yes != no and len(said.split()) <= 6:
        return _reading('yes' if yes else 'no', row, 'vocabulary')
    return _llm_reading(text, language, row, fallback='yes' if yes and not no else None)


def _reading(meaning, row, by):
    if meaning == 'yes':
        return {'by': by, 'decision': 'granted', 'granted': _all(row), 'confidence': 1.0}
    if meaning == 'no':
        return {'by': by, 'decision': 'denied', 'granted': [], 'confidence': 1.0}
    return {'by': by, 'decision': 'why'}


INTERPRETER = """You read a rural woman's spoken reply to a request to look at her records. She may answer in Hindi,
a dialect, or a mix with English. Decide what she allowed. Unclear means unclear: never guess yes.

Items she was asked about:
{items}

Other registers that exist (she may mention them): {others}

Reply with JSON only, no other text:
{{"decision": "grant" | "partial" | "deny" | "why" | "unclear",
  "granted_items": [item numbers she allowed],
  "refused_registers": [register keys she said not to look at, from either list],
  "confidence": 0.0 to 1.0}}
"grant" = all items. "partial" = only some items. "why" = she is asking why it is needed."""


def _llm_reading(text, language, row, fallback=None):
    items = '\n'.join(f'{n}. {i["register"]["en"]} (key {i["table"]}): {", ".join(i["labels"]["en"])}' for n, i in enumerate(row['items'], 1))
    others = ', '.join(f'{t} ({en})' for t, (_, en) in catalog.REGISTERS.items())
    messages = [{'role': 'system', 'content': INTERPRETER.format(items=items, others=others)},
                {'role': 'user', 'content': text}]
    for model in (nvidia.FAST_MODEL, nvidia.DEFAULT_MODEL):
        try:
            reply = nvidia.chat(model, messages, max_tokens=200, temperature=0)['message']['content']
            data = json.loads(re.search(r'\{.*\}', reply, re.S).group(0))
            break
        except (nvidia.LlmError, AttributeError, ValueError, TypeError):
            data = None
    if data is None:  # no LLM: a hedged yes is read back to her; anything else counts as unclear
        if fallback == 'yes':
            return {**_reading('yes', row, 'vocabulary'), 'confidence': 0.5}
        return {'by': 'vocabulary', 'decision': 'unclear'}
    decision = {'grant': 'granted', 'partial': 'granted', 'deny': 'denied', 'why': 'why'}.get(data.get('decision'), 'unclear')
    numbers = {int(n) for n in data.get('granted_items') or [] if str(n).isdigit()}
    chosen = [i for n, i in enumerate(row['items'], 1) if n in numbers] if data.get('decision') == 'partial' else row['items']
    granted = [{'source_id': i['source_id'], 'table': i['table'], 'fields': i['fields']} for i in chosen] if decision == 'granted' else []
    if decision == 'granted' and not granted:
        decision = 'unclear'
    return {'by': 'llm', 'model': model, 'decision': decision, 'granted': granted,
            'partial': data.get('decision') == 'partial' and len(granted) < len(row['items']),
            'refused_topics': [t for t in data.get('refused_registers') or [] if t in catalog.REGISTERS],
            'confidence': float(data.get('confidence') or 0)}
