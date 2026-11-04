"""Wallet questions in Plan-and-Execute: list her credentials, send one to a named verifier, save her business plan.

Code reads the intent from her words (the planner's "wallet" field covers paraphrases), every step is a recorded
tool call, she is asked aloud before anything is sent or saved, and the answer is worded by code from the results.
"""
from consent import ledger
from core import store
from wallet import keys, service

CREDENTIAL_WORDS = ('प्रमाणपत्र', 'प्रमाण पत्र', 'प्रमाण-पत्र', 'सर्टिफिकेट', 'सर्टीफिकेट', 'सर्टिफिकेट्स', 'कागज', 'वॉलेट',
                    'वालेट', 'बटुआ', 'certificate', 'certificates', 'credential', 'credentials', 'wallet', 'pramanpatra')
SHARE_WORDS = ('भेज', 'दे दो', 'दे दीजिए', 'send', 'share')
HOW_WORDS = ('कैसे', 'क्या होता', 'क्या है', 'how', 'what is')
SAVE_WORDS = ('रखो', 'रख दो', 'रख दीजिए', 'रखिए', 'रखना', 'सहेजो', 'सहेज', 'सेव', 'जोड़ो', 'जोड़ दो', 'डाल दो', 'बना दो',
              'बनाओ', 'save', 'store', 'add')
KIND_WORDS = [  # first match wins
    ('lakhpati', ('लखपति', 'lakhpati')),
    ('business_plan', ('बिज़नेस प्लान', 'बिजनेस प्लान', 'प्लान', 'business plan', 'plan')),
    ('training', ('प्रशिक्षण', 'ट्रेनिंग', 'training')),
    ('consent_receipt', ('सहमति', 'रसीद', 'receipt', 'consent')),
    ('shg_membership', ('सदस्यता', 'समूह', 'shg', 'membership', 'member')),
]
VERIFIER_WORDS = [('bank', ('बैंक', 'bank')), ('block_office', ('ब्लॉक', 'ब्लाक', 'block'))]
ACTIONS = ('list', 'share', 'save_plan')
FOLLOW_UP_MARK = {'hi': 'प्रमाणपत्र', 'en': 'credential'}


def _has(said, words):
    """Hindi stems match inside a word (प्रमाणपत्र in प्रमाणपत्रों); English words match whole."""
    return any((f' {ledger.normalise(w)} ' in f' {said} ') if w.isascii() else ledger.normalise(w) in said for w in words)


def detect(text):
    """{'action', 'kind', 'verifier'} when her words are about the wallet, else None."""
    said = ledger.normalise(text)
    kind = next((k for k, words in KIND_WORDS if _has(said, words)), None)
    verifier = next((v for v, words in VERIFIER_WORDS if _has(said, words)), None)
    credential, sharing, saving = _has(said, CREDENTIAL_WORDS), _has(said, SHARE_WORDS), _has(said, SAVE_WORDS)
    if saving and kind == 'business_plan' and (credential or not sharing):
        if credential or _has(said, ('वॉलेट', 'वालेट', 'wallet', 'प्रमाणपत्र')):
            return {'action': 'save_plan', 'kind': 'business_plan', 'verifier': None}
    if not credential and not (sharing and verifier and kind):
        return None
    if _has(said, HOW_WORDS) and not sharing:
        return None  # "how do I get a Lakhpati certificate?" is a general question for the planner
    if sharing or verifier:
        return {'action': 'share', 'kind': kind, 'verifier': verifier}
    return {'action': 'list', 'kind': kind, 'verifier': None}


def checked(raw):
    """The planner's wallet field, validated; None when it is missing or nonsense."""
    if not isinstance(raw, dict) or raw.get('action') not in ACTIONS:
        return None
    return {'action': raw['action'], 'kind': raw.get('kind') if raw.get('kind') in service.KINDS else None,
            'verifier': raw.get('verifier') if raw.get('verifier') in service.VERIFIERS else None}


def route(ctx):
    """Her wallet intent from this question, or from it and her previous one when she is answering our follow-up."""
    intent = detect(ctx.question)
    if ctx.history:
        last_q, last_a = ctx.history[-1]
        earlier = detect(last_q)
        asked = (last_a or '').rstrip().endswith('?') and any(m in (last_a or '') for m in FOLLOW_UP_MARK.values())
        if earlier and earlier['action'] == 'share' and asked:
            now = detect(f'{last_q} {ctx.question}') or {}
            merged = {k: (intent or {}).get(k) or now.get(k) or earlier.get(k) for k in ('action', 'kind', 'verifier')}
            return {**merged, 'action': 'share'}
    return intent


def merge(ctx, planned):
    """Code's reading first; the planner's fills what code could not tell."""
    found, planned = route(ctx), checked(planned)
    if not found:
        return planned
    if planned and planned['action'] == found['action']:
        return {k: found[k] or planned[k] for k in found}
    return found


def answer(ctx, intent):
    ctx.worded_by_code = True
    steps = {'list': ['Open her wallet', 'Tell her what it holds'],
             'share': ['Find the credential', 'Ask her before sending', 'Send only the claims the verifier needs'],
             'save_plan': ['Find her latest business plan', 'Ask her to approve it', 'Issue it as a credential']}[intent['action']]
    ctx.emit('plan', steps=steps, needs_records=False, items=[], wallet=intent)
    if keys.problem():
        store.log(ctx.workspace, 'wallet_unavailable', didi_id=str(ctx.didi['didi_id']), detail={'reason': keys.problem()})
        name = _name(ctx)
        return (f'{name} जी, अभी प्रमाणपत्र वाला वॉलेट चालू नहीं है। बाद में फिर से पूछिए।' if ctx.llm_language == 'hi'
                else f'{name}, the credential wallet is not available right now. Please ask again later.')
    return {'list': list_answer, 'share': share_answer, 'save_plan': save_answer}[intent['action']](ctx, intent)


# --- listing -------------------------------------------------------------------------------------

def open_wallet(ctx):
    return ctx.tool('wallet_list', {'didi_id': str(ctx.didi['didi_id'])}, lambda: service.listing(ctx.workspace, ctx.didi),
                    summary=lambda cards: f'{len(cards)} credentials: ' + ', '.join(sorted({c["kind"] for c in cards})))


def list_answer(ctx, intent):
    cards = open_wallet(ctx)
    hi = ctx.llm_language == 'hi'
    shown = [c for c in cards if not intent.get('kind') or c['kind'] == intent['kind']]
    ctx.emit('wallet', action='list', credentials=shown, position=ctx.run['position'])
    name = _name(ctx)
    if not shown:
        return f'{name} जी, आपके वॉलेट में अभी यह प्रमाणपत्र नहीं है।' if hi else f'{name}, your wallet does not hold that credential yet.'
    receipts = [c for c in shown if c['kind'] == 'consent_receipt']
    main = [c for c in shown if c['kind'] != 'consent_receipt']
    titles = [c['title']['hi' if hi else 'en'] + ('' if c['state'] == 'active' else (' (समय-सीमा खत्म)' if hi else ' (expired)'))
              for c in main]
    if receipts:
        titles.append(f'{len(receipts)} सहमति रसीदें' if hi and len(receipts) > 1 else '1 सहमति रसीद' if hi
                      else f'{len(receipts)} consent receipt{"s" if len(receipts) > 1 else ""}')
    lakhpati = any(c['kind'] == 'lakhpati' for c in main)
    if hi:
        text = f'{name} जी, आपके वॉलेट में {len(main) + len(receipts)} प्रमाणपत्र हैं: {_join(titles, "hi")}।'
        text += ' हर प्रमाणपत्र पर डिजिटल हस्ताक्षर है।'
        text += (' किसी को भेजना हो तो बोलिए, जैसे "लखपति दीदी का प्रमाणपत्र बैंक को भेजो"।' if lakhpati else
                 ' लखपति दीदी का प्रमाणपत्र चाहिए तो बोलिए "लखपति दीदी का प्रमाणपत्र बैंक को भेजो"; पहले आपकी आमदनी जाँची जाएगी।')
        return text
    text = f'{name}, your wallet holds {len(main) + len(receipts)} credentials: {_join(titles, "en")}. Each one is digitally signed.'
    return text + ' To send one, say for example "send my Lakhpati Didi credential to the bank".'


# --- sharing -------------------------------------------------------------------------------------

def share_answer(ctx, intent):
    hi = ctx.llm_language == 'hi'
    name, kind, verifier = _name(ctx), intent.get('kind'), intent.get('verifier')
    if not kind:
        cards = open_wallet(ctx)
        titles = sorted({c['title']['hi' if hi else 'en'] for c in cards if c['kind'] != 'consent_receipt'})
        return (f'{name} जी, आपके पास {_join(titles, "hi")} हैं; लखपति दीदी का प्रमाणपत्र भी बन सकता है। कौन सा प्रमाणपत्र भेजूँ?'
                if hi else f'{name}, which credential should I send? You hold {_join(titles, "en")}; which credential?')
    if not verifier:
        return (f'{name} जी, यह प्रमाणपत्र किसे भेजना है, बैंक को या ब्लॉक कार्यालय को?' if hi else
                f'{name}, who should get this credential, the bank or the block office?')
    row = find(ctx, kind)
    if isinstance(row, str):
        return row  # she could not or would not get one: the reason, in words
    to = service.VERIFIERS[verifier]['name']
    claims = row['needs'][verifier]
    status = next((x['value'] for x in row['claims'] if x['claim'] == 'status_label'), None)
    # The title and the status go in one sentence: "लखपति दीदी प्रमाणपत्र" alone would read as the verdict to gate C.
    says = {'hi': f'; इसमें आपकी स्थिति "{status}" लिखी है', 'en': f'; it states your status: "{status}"'} if status else {'hi': '', 'en': ''}
    labels = {lang: [service.label(c)[lang] for c in claims] for lang in ('hi', 'en')}
    title = row['title']
    lines = {'hi': f'{to["hi"]} को सिर्फ़ ये बातें दिखेंगी: {_join(labels["hi"], "hi")}।',
             'en': f'The {to["en"]} will see only: {_join(labels["en"], "en")}.'}
    hidden = {'hi': 'बाकी बातें छिपी रहेंगी' + ('; आपकी आमदनी के आँकड़े इसमें हैं ही नहीं।' if kind == 'lakhpati' else '।'),
              'en': 'Everything else stays hidden' + ('; your income figures are not in it at all.' if kind == 'lakhpati' else '.')}
    statement = {lang: _statement(
        {'hi': f'आपका {title["hi"]} {to["hi"]} को भेजा जाएगा{says["hi"]}।',
         'en': f'Your {title["en"]} will be sent to the {to["en"]}{says["en"]}.'}[lang],
        lines[lang], hidden[lang] + {'hi': ' आपकी हाँ सहमति रसीद में दर्ज होगी।', 'en': ' Your answer is kept as a consent receipt.'}[lang],
        {'hi': 'क्या भेज दूँ?', 'en': 'Shall I send it?'}[lang]) for lang in ('hi', 'en')}
    items = [{'source_id': 'wallet', 'source_name': 'Wallet', 'table': kind, 'fields': claims,
              'register': title, 'labels': labels}]
    purpose = {'id': 'credential_share', 'hi': f'आपका प्रमाणपत्र {to["hi"]} को भेजने के लिए',
               'en': f'To send your credential to the {to["en"]}', 'verifier': verifier, 'credential_id': row['id']}
    said = ctx.ask(f'share:{row["id"]}:{verifier}', items, purpose, statement)
    if said['decision'] not in ('granted', 'partial'):
        return (f'ठीक है {name} जी, {title["hi"]} {to["hi"]} को नहीं भेजा गया।' if hi else
                f'All right {name}, your {title["en"]} was not sent to the {to["en"]}.')
    allowed = {f for g in said['granted'] for f in g['fields']}
    chosen = [c for c in claims if c in allowed]
    shared = ctx.tool('share_credential', {'credential_id': row['id'], 'verifier': verifier, 'claims': chosen,
                                           'consent_id': said['consent_id']},
                      lambda: service.share(ctx.workspace, ctx.didi, row['id'], verifier, chosen, said['consent_id']),
                      summary=lambda s: f'{s["share_id"]}: disclosed {", ".join(c["claim"] for c in s["disclosed"])}; '
                                        f'withheld {len(s["withheld"])} claims')
    ctx.emit('wallet', action='shared', share=shared, position=ctx.run['position'])
    seen = _join([d['label']['hi' if hi else 'en'] for d in shared['disclosed']], 'hi' if hi else 'en')
    if hi:
        text = f'{name} जी, आपका {title["hi"]} {to["hi"]} को भेज दिया है{says["hi"]}। {to["hi"]} को सिर्फ़ {seen} दिखेगा'
        text += '; आपकी आमदनी के आँकड़े और बाकी बातें नहीं।' if kind == 'lakhpati' else '; बाकी बातें नहीं।'
        return text + f' {to["hi"]} डिजिटल हस्ताक्षर जाँचकर इसे सत्यापित कर सकता है।'
    text = f'{name}, your {title["en"]} has been sent to the {to["en"]}{says["en"]}. They will see only {seen}'
    return text + ('; not your income figures or anything else.' if kind == 'lakhpati' else '; nothing else.') + \
        ' They can check the digital signature to verify it.'


def find(ctx, kind):
    """Her active credential of `kind` as a card with each verifier's needed claims, issuing it first if it can be
    issued now. A string is the answer when it cannot."""
    hi, name = ctx.llm_language == 'hi', _name(ctx)

    def held():
        row = service.current(ctx.workspace, ctx.didi['didi_id'], kind)
        return {**service.card(row), 'needs': {v: service.needs(v, kind, row) for v in service.VERIFIERS}} if row else None

    if kind in ('shg_membership', 'training', 'consent_receipt'):
        open_wallet(ctx)  # issues what LokOS, the trainer and the ledger would have
    row = ctx.tool('wallet_find', {'kind': kind, 'didi_id': str(ctx.didi['didi_id'])}, held,
                   summary=lambda r: f'{r["id"]} ({r["state"]})' if r else f'no active {kind}')
    if row:
        return row
    if kind == 'business_plan':
        return (f'{name} जी, आपके वॉलेट में अभी मंज़ूर बिज़नेस प्लान नहीं है। पहले प्लान बनवाइए, फिर बोलिए "मेरा बिज़नेस प्लान वॉलेट में रखो"।'
                if hi else f'{name}, your wallet has no approved business plan yet. Build a plan, then say "save my business plan to my wallet".')
    if kind != 'lakhpati':
        return f'{name} जी, यह प्रमाणपत्र अभी नहीं मिल पाया।' if hi else f'{name}, that credential is not available right now.'
    return lakhpati(ctx) or ctx.tool('wallet_find', {'kind': kind, 'didi_id': str(ctx.didi['didi_id']), 'after': 'issue'}, held,
                                     summary=lambda r: f'{r["id"]} ({r["state"]})' if r else 'not issued')


def lakhpati(ctx):
    """Run the Lakhpati table on her DAR rows with her consent and issue the credential. None when issued."""
    import tools
    hi, name = ctx.llm_language == 'hi', _name(ctx)
    described = tools.describe_decision(ctx, 'lakhpati_status')
    if 'error' in described:
        return f'{name} जी, अभी लखपति दीदी की जाँच नहीं हो पाई।' if hi else f'{name}, the Lakhpati check cannot run right now.'
    items, _ = tools._items(ctx, described['consent_items'])
    consent = ctx.consent(items, described['purpose'])
    if consent['decision'] not in ('granted', 'partial'):
        return (f'ठीक है {name} जी। आपकी आमदनी देखे बिना लखपति दीदी का प्रमाणपत्र नहीं बन सकता, इसलिए कुछ नहीं भेजा गया।' if hi else
                f'All right {name}. Without looking at your income the Lakhpati credential cannot be made, so nothing was sent.')
    result = ctx.decide('lakhpati_status', consent['consent_id'])
    if 'error' in result:
        return f'{name} जी, लखपति दीदी की जाँच पूरी नहीं हो पाई।' if hi else f'{name}, the Lakhpati check could not finish.'
    outcome = {k: result[k] for k in ('decision_id', 'version', 'outcome', 'outcome_label', 'matched_rules', 'basis')}
    ctx.tool('issue_credential', {'kind': 'lakhpati', 'decision': outcome},
             lambda: service.card(service.issue_lakhpati(ctx.workspace, ctx.didi, outcome)),
             summary=lambda c: f'{c["id"]}: {outcome["outcome"]} (rule {", ".join(map(str, outcome["matched_rules"]))}, v{outcome["version"]})')
    return None


# --- saving her business plan -------------------------------------------------------------------

def save_answer(ctx, intent):
    hi, name = ctx.llm_language == 'hi', _name(ctx)

    def latest():
        row = service.latest_plan(ctx.workspace, ctx.didi['didi_id'])
        return {'file_id': row['id'], 'name': row['name'],
                'subject': service.plan_subject(ctx.didi, row, service.plan_digest(row))} if row else None

    plan = ctx.tool('find_business_plan', {'didi_id': str(ctx.didi['didi_id'])}, latest,
                    summary=lambda p: f'{p["name"]} sha256 {p["subject"]["plan_sha256"][:12]}…' if p else 'no plan built yet')
    if not plan:
        return (f'{name} जी, अभी आपका कोई बिज़नेस प्लान नहीं बना है। पहले बोलिए, जैसे "मेरी डेयरी का बिज़नेस प्लान बनाओ", फिर उसे वॉलेट में रख देंगे।'
                if hi else f'{name}, no business plan has been built for you yet. Ask for one first, then I can save it to your wallet.')
    subject = plan['subject']
    claims = [c for c in subject if subject[c] not in (None, '')]
    labels = {lang: [service.label(c)[lang] for c in claims] for lang in ('hi', 'en')}
    title = service.KINDS['business_plan']['title']
    statement = {lang: _statement(
        {'hi': f'आपका {subject["enterprise"]} का बिज़नेस प्लान ({plan["name"]}) आपकी मंज़ूरी के साथ प्रमाणपत्र बनाकर वॉलेट में रखा जाएगा। उसमें ये बातें होंगी:',
         'en': f'Your business plan ({plan["name"]}) will be signed as a credential with your approval and kept in your wallet. It will hold:'}[lang],
        _join(labels[lang], lang) + '।' if lang == 'hi' else _join(labels[lang], lang) + '.',
        {'hi': 'फ़ाइल का हैश रखा जाएगा, ताकि फ़ाइल बदले तो पता चल जाए। किसी को भेजने से पहले आपसे फिर पूछा जाएगा।',
         'en': 'The file\'s hash is kept, so any change to the file shows. You will be asked again before it is sent to anyone.'}[lang],
        {'hi': 'क्या आप इस प्लान को मंज़ूर करती हैं?', 'en': 'Do you approve this plan?'}[lang]) for lang in ('hi', 'en')}
    items = [{'source_id': 'wallet', 'source_name': 'Wallet', 'table': 'business_plan', 'fields': claims, 'register': title,
              'labels': labels}]
    purpose = {'id': 'plan_approval', 'hi': 'आपके बिज़नेस प्लान को मंज़ूर करके वॉलेट में रखने के लिए',
               'en': 'To approve your business plan and keep it in your wallet', 'file_id': plan['file_id']}
    said = ctx.ask(f'approve:{plan["file_id"]}', items, purpose, statement)
    if said['decision'] != 'granted':
        return (f'ठीक है {name} जी, प्लान मंज़ूर नहीं हुआ, इसलिए वॉलेट में नहीं रखा। पूरा प्लान मंज़ूर हो तभी प्रमाणपत्र बनता है।' if hi
                else f'All right {name}, the plan was not approved, so it was not saved. A plan credential needs the whole plan approved.')
    card = ctx.tool('issue_credential', {'kind': 'business_plan', 'file_id': plan['file_id'], 'consent_id': said['consent_id']},
                    lambda: service.card(service.issue_plan(
                        ctx.workspace, ctx.didi, store.get_file(ctx.workspace, plan['file_id']), said['consent_id'])),
                    summary=lambda c: f'{c["id"]}: business plan, sha256 {subject["plan_sha256"][:12]}…')
    ctx.emit('wallet', action='issued', credentials=[card], position=ctx.run['position'])
    if hi:
        return (f'{name} जी, आपका {subject["enterprise"]} का बिज़नेस प्लान आपकी मंज़ूरी के साथ वॉलेट में रख दिया है। यह 6 महीने तक मान्य है। '
                'बैंक को भेजना हो तो बोलिए "मेरा बिज़नेस प्लान बैंक को भेजो"।')
    return f'{name}, your business plan is saved in your wallet with your approval, valid for 6 months. Say "send my business plan to the bank" to share it.'


# --- wording -------------------------------------------------------------------------------------

def _statement(intro, line, retention, ask):
    return {'intro': intro, 'lines': [line], 'retention': retention, 'ask': ask, 'speech': ' '.join([intro, line, retention, ask])}


def _name(ctx):
    return str(ctx.didi.get('name_local' if ctx.llm_language == 'hi' else 'name') or '').split(' ')[0]


def _join(items, lang):
    items = list(items)
    if len(items) < 2:
        return ''.join(items)
    return f'{", ".join(items[:-1])} {"और" if lang == "hi" else "and"} {items[-1]}'
