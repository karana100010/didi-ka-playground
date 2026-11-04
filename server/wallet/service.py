"""The didi's wallet: which credentials she holds, issuing them, sharing one with a verifier, and the verifier's inbox.

Issuers (all signing with the playground's one did:web key, named per credential):
- SHG membership: LokOS signs its own users and shg rows for her. The register's owner attesting its own record is
  not a read for an answer, so no consent card; the issuance is logged with the fields it used.
- Lakhpati Didi status: the decision table's outcome, matched rule and version, after the agent ran the table on her
  DAR rows with her consent. No income figure goes into the credential.
- Business plan: the SHA-256 of the built Excel workbook and its headline figures, with her approval (a consent row).
- Training: a mock certificate.
- Consent receipts: one per decided consent in the ledger; a withdrawn consent revokes its receipt and gets a new one.

Sharing: she says yes (a consent row, purpose credential_share) to the claims a verifier needs, and only those
disclosures go into the presentation. The share keeps the presentation alone, so the verifier side never sees the
rest of the credential.
"""
import hashlib
import secrets

import settings
from core import files, store

from . import keys, vc

KINDS = {
    'shg_membership': {'vc': 'ShgMembershipCredential', 'days': 365, 'issuer': 'LokOS (DAY-NRLM), mock',
                       'title': {'hi': 'स्वयं सहायता समूह सदस्यता', 'en': 'SHG membership'}},
    'lakhpati': {'vc': 'LakhpatiDidiCredential', 'days': 365, 'issuer': 'DAY-NRLM Lakhpati Didi decision table, mock',
                 'title': {'hi': 'लखपति दीदी प्रमाणपत्र', 'en': 'Lakhpati Didi status'}},
    'business_plan': {'vc': 'BusinessPlanCredential', 'days': 180, 'issuer': 'GramStack business plan service, mock',
                      'title': {'hi': 'मंज़ूर बिज़नेस प्लान', 'en': 'Approved business plan'}},
    'training': {'vc': 'TrainingCertificateCredential', 'days': 3 * 365, 'issuer': 'RSETI Gaya, mock',
                 'title': {'hi': 'प्रशिक्षण प्रमाणपत्र', 'en': 'Training certificate'}},
    'consent_receipt': {'vc': 'ConsentReceiptCredential', 'days': None, 'issuer': 'GramStack consent ledger',
                        'title': {'hi': 'सहमति रसीद', 'en': 'Consent receipt'}},
}
BY_VC = {spec['vc']: kind for kind, spec in KINDS.items()}

CLAIMS = {  # claim -> labels; also the verifier page's schema
    'name': ('नाम', 'Name'), 'shg_id': ('समूह आईडी', 'SHG id'), 'shg_name': ('समूह का नाम', 'SHG name'),
    'role': ('समूह में पद', 'Role in the SHG'), 'vo_name': ('ग्राम संगठन', 'Village organisation'),
    'clf_name': ('संकुल संघ (CLF)', 'Cluster federation (CLF)'), 'shg_formed_on': ('समूह बनने की तारीख', 'SHG formed on'),
    'shg_grade': ('समूह ग्रेड', 'SHG grade'), 'village': ('गाँव', 'Village'), 'district': ('ज़िला', 'District'),
    'state': ('राज्य', 'State'),
    'lakhpati_status': ('लखपति दीदी की स्थिति', 'Lakhpati Didi status'), 'status_label': ('स्थिति (शब्दों में)', 'Status in words'),
    'decision_table': ('नियम तालिका', 'Decision table'), 'rule_id': ('लागू नियम', 'Rule applied'),
    'table_version': ('नियम का संस्करण', 'Rule version'), 'basis': ('आधार', 'Basis'), 'decided_on': ('तय होने की तारीख', 'Decided on'),
    'enterprise': ('धंधा', 'Enterprise'), 'plan_file': ('प्लान फ़ाइल', 'Plan file'),
    'plan_sha256': ('प्लान फ़ाइल का हैश (SHA-256)', 'Plan file hash (SHA-256)'), 'investment': ('कुल लागत (₹)', 'Investment (₹)'),
    'loan_amount': ('कर्ज़ की रकम (₹)', 'Loan amount (₹)'), 'emi': ('मासिक किस्त (₹)', 'Monthly instalment (₹)'),
    'profit_now': ('अभी का मासिक मुनाफ़ा (₹)', 'Monthly profit now (₹)'), 'profit_after': ('बाद का मासिक मुनाफ़ा (₹)', 'Monthly profit after (₹)'),
    'approved_by_didi': ('दीदी की मंज़ूरी', 'Approved by her'), 'approved_at': ('मंज़ूरी की तारीख', 'Approved on'),
    'approval_consent_id': ('मंज़ूरी की रसीद', 'Approval record'),
    'course': ('प्रशिक्षण', 'Course'), 'provider': ('संस्थान', 'Provider'), 'completed_on': ('पूरा होने की तारीख', 'Completed on'),
    'hours': ('घंटे', 'Hours'), 'result': ('परिणाम', 'Result'),
    'consent_id': ('सहमति संख्या', 'Consent id'), 'purpose': ('उद्देश्य', 'Purpose'), 'decision': ('आपका फ़ैसला', 'Her decision'),
    'registers': ('रजिस्टर', 'Registers'), 'fields': ('जानकारी', 'Fields'), 'recipient': ('किसे', 'Recipient'),
    'decided_at': ('फ़ैसले का समय', 'Decided at'),
}

VERIFIERS = {
    'bank': {'name': {'hi': 'बैंक', 'en': 'Bank (mock)'},
             'needs': {'lakhpati': ['name', 'lakhpati_status', 'status_label', 'rule_id', 'table_version', 'decided_on'],
                       'shg_membership': ['name', 'shg_name', 'role', 'shg_grade', 'clf_name'],
                       'business_plan': ['name', 'enterprise', 'plan_sha256', 'investment', 'loan_amount', 'emi',
                                         'approved_by_didi', 'approved_at'],
                       'training': ['name', 'course', 'provider', 'completed_on'],
                       'consent_receipt': ['consent_id', 'purpose', 'decision', 'decided_at']}},
    'block_office': {'name': {'hi': 'ब्लॉक कार्यालय', 'en': 'Block office (mock)'},
                     'needs': {'lakhpati': ['name', 'lakhpati_status', 'rule_id', 'table_version', 'decided_on'],
                               'shg_membership': ['name', 'shg_name', 'shg_id', 'role', 'village'],
                               'business_plan': ['name', 'enterprise', 'plan_sha256', 'approved_at'],
                               'training': ['name', 'course', 'provider', 'completed_on', 'result'],
                               'consent_receipt': ['consent_id', 'purpose', 'decision', 'decided_at']}},
}

TRAININGS = {  # mock: what each didi has completed
    'D-001': ('पशुपालन एवं डेयरी प्रबंधन', 'Animal husbandry and dairy management', 60),
    'D-002': ('सिलाई एवं परिधान निर्माण', 'Tailoring and garment making', 90),
}
FINANCIAL_LITERACY = ('वित्तीय साक्षरता (FLCRP)', 'Financial literacy (FLCRP)', 24)


def audience(verifier):
    return f'{settings.PUBLIC_URL}/verify/{verifier}'


# --- holding -----------------------------------------------------------------------------------

def state(row):
    if row['revoked_at']:
        return 'revoked'
    if row['expires_at'] and store.aware(row['expires_at']) <= store.now():
        return 'expired'
    return 'active'


def current(workspace, didi_id, kind):
    """Her newest active credential of `kind`, or None."""
    return next((r for r in store.didi_credentials(workspace, didi_id) if r['summary']['kind'] == kind and state(r) == 'active'), None)


def card(row, lang='hi'):
    s = row['summary']
    return {'id': row['id'], 'kind': s['kind'], 'title': s['title'], 'issuer': s['issuer'], 'state': state(row),
            'issued_at': store.aware(row['issued_at']).date().isoformat(),
            'valid_until': store.aware(row['expires_at']).date().isoformat() if row['expires_at'] else None,
            'claims': [{'claim': c, 'label': label(c), 'value': v} for c, v in vc.disclosed_claims(row['sd_jwt']).items()]}


def label(claim):
    hi, en = CLAIMS.get(claim, (claim, claim))
    return {'hi': hi, 'en': en}


def listing(workspace, didi):
    """Her wallet after making sure it holds what LokOS, the trainer and the ledger would have issued."""
    provision(workspace, didi)
    rows = store.didi_credentials(workspace, didi['didi_id'])
    return [card(r) for r in rows if state(r) != 'revoked']


def provision(workspace, didi):
    if not current(workspace, didi['didi_id'], 'shg_membership'):
        issue_shg(workspace, didi)
    if not current(workspace, didi['didi_id'], 'training'):
        issue_training(workspace, didi)
    sync_receipts(workspace, didi)


# --- issuing -----------------------------------------------------------------------------------

def _issue(workspace, didi, kind, subject, ref=None, read=None, valid_days='default', when=None):
    spec = KINDS[kind]
    days = spec['days'] if valid_days == 'default' else valid_days
    credential_id, sd_jwt, env = vc.issue(workspace, didi['didi_id'], spec['vc'], subject, days, spec['issuer'], when)
    row = dict(id=credential_id, workspace=workspace, didi_id=str(didi['didi_id']), type=spec['vc'], sd_jwt=sd_jwt,
               summary={'kind': kind, 'title': spec['title'], 'issuer': spec['issuer']}, ref=ref,
               status_index=env['status_index'], issued_at=env['issued_at'], expires_at=env['expires_at'], revoked_at=None)
    store.add_credential(row)
    store.log(workspace, 'credential_issued', didi_id=str(didi['didi_id']),
              detail={'credential_id': credential_id, 'type': spec['vc'], 'ref': ref, 'claims': sorted(subject), 'read': read or []})
    return row


def revoke(workspace, credential_id, reason):
    row = store.get_credential(workspace, credential_id)
    if row and not row['revoked_at']:
        store.set_credential(credential_id, revoked_at=store.now())
        store.log(workspace, 'credential_revoked', didi_id=row['didi_id'], detail={'credential_id': credential_id, 'reason': reason})
    return store.get_credential(workspace, credential_id)


def _supersede(workspace, didi_id, kind, keep=None):
    for row in store.didi_credentials(workspace, didi_id):
        if row['summary']['kind'] == kind and row['id'] != keep and not row['revoked_at']:
            revoke(workspace, row['id'], 'superseded')


def issue_shg(workspace, didi):
    import sources
    shg_id = (didi.get('keys') or {}).get('shg_id') or didi.get('shg_id')
    group, read = {}, []
    source = sources.primary(workspace)
    if source and shg_id and 'shg' in source['tables']:
        fields = [f for f in ('shg_name', 'vo_name', 'clf_name', 'formed_on', 'grade') if f in source['tables']['shg']]
        group = next(iter(sources.connector(source).rows('shg', fields, where={'shg_id': shg_id})), {})
        read = [f'shg.{f}' for f in fields]
    subject = {'name': didi.get('name'), 'shg_id': shg_id, 'shg_name': group.get('shg_name') or didi.get('shg_name'),
               'role': didi.get('role'), 'vo_name': group.get('vo_name'), 'clf_name': group.get('clf_name'),
               'shg_formed_on': _date(group.get('formed_on')), 'shg_grade': group.get('grade'),
               'village': didi.get('village'), 'district': didi.get('district'), 'state': didi.get('state')}
    return _issue(workspace, didi, 'shg_membership', subject, ref=shg_id,
                  read=['users.name', 'users.role', 'users.shg_id', 'users.village', 'users.district', 'users.state', *read])


def issue_training(workspace, didi):
    hi, en, hours = TRAININGS.get(str(didi['didi_id']), FINANCIAL_LITERACY)
    subject = {'name': didi.get('name'), 'course': hi, 'course_en': en, 'provider': 'RSETI गया (mock)', 'hours': hours,
               'completed_on': '2025-02-14', 'result': 'उत्तीर्ण (Pass)'}
    return _issue(workspace, didi, 'training', subject, ref='mock-training')


def issue_lakhpati(workspace, didi, result):
    """From the decision table's result: outcome, rule and version only. Her income stays out."""
    subject = {'name': didi.get('name'), 'lakhpati_status': result['outcome'],
               'status_label': (result.get('outcome_label') or {}).get('hi') or result['outcome'],
               'decision_table': result['decision_id'], 'rule_id': ', '.join(map(str, result['matched_rules'])) or 'none',
               'table_version': result['version'], 'basis': ', '.join(sorted(set((result.get('basis') or {}).values()))) or None,
               'decided_on': store.now().date().isoformat()}
    row = _issue(workspace, didi, 'lakhpati', subject, ref=f'{result["decision_id"]}@{result["version"]}')
    _supersede(workspace, didi['didi_id'], 'lakhpati', keep=row['id'])
    return row


def latest_plan(workspace, didi_id):
    return next(iter(store.didi_files(workspace, didi_id, 'business_plan')), None)


def plan_digest(file_row):
    return hashlib.sha256(files.read(file_row['ref'])).hexdigest()


def plan_subject(didi, file_row, digest):
    from plans import templates
    s = file_row['summary']
    r = s.get('results') or {}
    return {'name': didi.get('name'), 'enterprise': templates.TEMPLATES[s['enterprise']]['name']['hi'],
            'plan_file': file_row['name'], 'plan_sha256': digest, 'investment': r.get('investment'),
            'loan_amount': r.get('loan_amount'), 'emi': r.get('emi'), 'profit_now': r.get('profit_now'),
            'profit_after': r.get('profit_after')}


def issue_plan(workspace, didi, file_row, consent_id):
    digest = plan_digest(file_row)
    subject = {**plan_subject(didi, file_row, digest), 'approved_by_didi': True,
               'approved_at': store.now().date().isoformat(), 'approval_consent_id': consent_id}
    row = _issue(workspace, didi, 'business_plan', subject, ref=file_row['id'])
    _supersede(workspace, didi['didi_id'], 'business_plan', keep=row['id'])
    return row


def sync_receipts(workspace, didi):
    """One receipt per decided consent, matching its current status."""
    have = {}
    for row in store.didi_credentials(workspace, didi['didi_id']):
        if row['summary']['kind'] == 'consent_receipt' and not row['revoked_at']:
            have[row['ref']] = row
    for consent in store.didi_consents(workspace, didi['didi_id']):
        if consent['status'] not in ('granted', 'partial', 'denied', 'withdrawn'):
            continue
        held = have.get(consent['id'])
        if held and held['summary'].get('status') == consent['status']:
            continue
        if held:
            revoke(workspace, held['id'], f'consent {consent["status"]}')
        row = _issue(workspace, didi, 'consent_receipt', receipt_subject(didi, consent), ref=consent['id'])
        store.set_credential(row['id'], summary={**row['summary'], 'status': consent['status']})


def receipt_subject(didi, consent):
    purpose = consent['purpose'] or {}
    granted = consent['granted'] or []
    return {'name': didi.get('name'), 'consent_id': consent['id'], 'purpose': purpose.get('hi') or purpose.get('id'),
            'purpose_id': purpose.get('id'), 'decision': consent['status'],
            'registers': [i['register']['hi'] for i in consent['items']],
            'fields': sorted({f for g in granted for f in g['fields']}),
            'recipient': (VERIFIERS.get(purpose.get('verifier')) or {}).get('name', {}).get('hi'),
            'decided_at': store.aware(consent['decided_at']).isoformat(timespec='seconds') if consent['decided_at'] else None}


# --- sharing -----------------------------------------------------------------------------------

def needs(verifier, kind, row):
    """The claims this verifier asks for that her credential holds, in the credential's order."""
    have = vc.disclosed_claims(row['sd_jwt'])
    wanted = VERIFIERS[verifier]['needs'].get(kind, [])
    return [c for c in wanted if c in have]


def share(workspace, didi, credential_id, verifier, claims, consent_id):
    row = store.get_credential(workspace, credential_id)
    if not row or row['didi_id'] != str(didi['didi_id']):
        raise ValueError('not her credential')
    nonce = secrets.token_urlsafe(16)  # the verifier's request nonce (OpenID4VP would send it; the mock makes it here)
    presentation = vc.present(row['sd_jwt'], claims, keys.holder_key(workspace, didi['didi_id']), audience(verifier), nonce)
    share_id = store.new_id('shr')
    store.add_share(dict(id=share_id, workspace=workspace, didi_id=str(didi['didi_id']), credential_id=credential_id,
                         verifier=verifier, presentation=presentation, nonce=nonce, consent_id=consent_id, created_at=store.now()))
    withheld = [c for c in vc.disclosed_claims(row['sd_jwt']) if c not in claims]
    store.log(workspace, 'credential_shared', didi_id=str(didi['didi_id']),
              detail={'share_id': share_id, 'credential_id': credential_id, 'type': row['type'], 'verifier': verifier,
                      'disclosed': list(claims), 'withheld': withheld, 'consent_id': consent_id})
    return {'share_id': share_id, 'verifier': verifier, 'verifier_name': VERIFIERS[verifier]['name'],
            'credential_id': credential_id, 'kind': row['summary']['kind'], 'title': row['summary']['title'],
            'disclosed': [{'claim': c, 'label': label(c)} for c in claims],
            'withheld': [{'claim': c, 'label': label(c)} for c in withheld], 'consent_id': consent_id,
            'verify_url': f'/verify?as={verifier}#{share_id}'}


# --- the verifier's side -------------------------------------------------------------------------

def inbox(workspace, verifier, limit=20):
    """Presentations sent to this verifier, each checked from the presentation alone."""
    out = []
    for share_row in store.verifier_shares(workspace, verifier, limit):
        result = vc.verify(share_row['presentation'], workspace, audience(verifier), share_row['nonce'])
        out.append({'share_id': share_row['id'], 'received_at': store.aware(share_row['created_at']).isoformat(timespec='seconds'),
                    **present_result(result), 'presentation': share_row['presentation']})
    return out


def present_result(result):
    kind = BY_VC.get(result['type'])
    order = list(CLAIMS)
    result = {**result, 'disclosed': dict(sorted(result['disclosed'].items(),
                                                 key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order)))}
    return {**result, 'kind': kind, 'title': KINDS[kind]['title'] if kind else None,
            'labels': {c: label(c) for c in result['disclosed']}}


def _date(value):
    return value.date().isoformat() if hasattr(value, 'date') else (str(value)[:10] if value else None)
