"""M16: the didi's wallet. W3C VC 2.0 as SD-JWT, issued inside the server (sd-jwt-python, did:web key outside git),
held per didi, shared by voice with a consent row, and checked by the verifier from the presentation alone."""
import base64
import hashlib
import json
from datetime import timedelta

import pytest

from conftest import WS, login
from test_m4 import run_turn

LIST = 'मेरे प्रमाणपत्र दिखाओ'
SHARE = 'लखपति दीदी का प्रमाणपत्र बैंक को भेजो'


def ws_name():
    return WS['X-Workspace']


def wallet(client, session):
    return client.get(f'/v1/sessions/{session["id"]}/wallet', headers=WS).json()['credentials']


def inbox(client, verifier='bank'):
    return client.get(f'/v1/verifiers/{verifier}/inbox', headers=WS).json()['received']


def disclosures(presentation):
    """The claim names a presentation carries, decoded the way anyone holding it could."""
    out = []
    for part in presentation.split('~')[1:-1]:
        _, key, _ = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
        out.append(key)
    return out


def answer_with(client, button_for):
    """run_turn's consent answering, but per card: button_for(card) -> request body."""
    def run(session, text):
        from conftest import stream
        turn = client.post(f'/v1/sessions/{session["id"]}/turns', headers=WS, json={'text': text}).json()['id']
        events, end = stream(client, turn)
        while end['status'] == 'waiting':
            for e in events:
                if e['type'] == 'consent_request':
                    client.post(f'/v1/consents/{e["data"]["consent_id"]}/answer', headers=WS, json=button_for(e['data']))
            more, end = stream(client, turn, after=events[-1]['id'])
            events += more
        return events, end
    return run


# --- issuance and the issuer's identity ----------------------------------------------------------

def test_issuer_key_is_did_web_and_kept_outside_git(client):
    import settings
    from wallet import keys
    doc = client.get('/.well-known/did.json').json()
    assert doc['id'] == settings.WALLET_DID == 'did:web:localhost%3A8800'
    key = doc['verificationMethod'][0]
    assert key['id'] == f'{doc["id"]}#key-1' and key['publicKeyJwk']['crv'] == 'P-256' and 'd' not in key['publicKeyJwk']
    path = settings.WALLET_KEY_DIR / 'issuer.jwk.json'
    assert path.exists() and settings.ROOT not in path.parents  # the test's temp dir; locally ~/.didi_playground
    assert json.loads(path.read_text())['x'] == key['publicKeyJwk']['x'] and keys.issuer_key().has_private



def test_hosted_wallet_refuses_a_throwaway_issuer_key(client, monkeypatch):
    """On Vercel the disk is wiped between cold starts: a generated key would change and break every credential."""
    import settings
    from jwcrypto.jwk import JWK
    from wallet import keys
    keys.reset()
    monkeypatch.setattr(settings, 'HOSTED', True)
    with pytest.raises(keys.NotConfigured, match='WALLET_ISSUER_JWK is not set'):
        keys.issuer_key()
    assert not (settings.WALLET_KEY_DIR / 'issuer.jwk.json').exists()
    health = client.get('/healthz').json()['wallet']
    assert health['ok'] is False and 'WALLET_ISSUER_JWK' in health['reason']
    assert client.get('/.well-known/did.json').json()['error']['code'] == 'not_configured'

    fixed = JWK.generate(kty='EC', crv='P-256')
    monkeypatch.setattr(settings, 'WALLET_ISSUER_JWK', fixed.export_private())
    with pytest.raises(keys.NotConfigured, match='PUBLIC_URL'):  # the DID still names localhost
        keys.issuer_key()
    monkeypatch.setattr(settings, 'WALLET_DID', 'did:web:didi.example.org')
    assert keys.issuer_key().thumbprint() == fixed.thumbprint()
    health = client.get('/healthz').json()['wallet']
    assert health == {'ok': True, 'did': 'did:web:didi.example.org', 'kid': 'did:web:didi.example.org#key-1',
                      'thumbprint': fixed.thumbprint(), 'source': 'env'}

    keys.reset()
    monkeypatch.setattr(settings, 'WALLET_ISSUER_JWK', fixed.export_public())  # no private half: cannot sign
    assert keys.problem() == 'WALLET_ISSUER_JWK must be a private EC P-256 key.'
    keys.reset()


def test_wallet_turn_says_so_when_the_issuer_key_is_missing(client, fake_llm, monkeypatch):
    import settings
    from wallet import keys
    keys.reset()
    monkeypatch.setattr(settings, 'WALLET_ISSUER_JWK', 'not a key')
    session = login(client, 'D-001', agent='plan_execute')
    events, end = run_turn(client, session, LIST)
    assert end['runs'][0]['result']['answer'] == 'सुनीता जी, अभी प्रमाणपत्र वाला वॉलेट चालू नहीं है। बाद में फिर से पूछिए।'
    assert not [e for e in events if e['type'] == 'wallet'] and fake_llm.calls == []
    logged = [e for e in client.get('/v1/audit', headers=WS).json()['events'] if e['kind'] == 'wallet_unavailable']
    assert logged and logged[0]['detail']['reason'] == 'WALLET_ISSUER_JWK is not a valid JWK.'
    keys.reset()

def test_listing_issues_shg_membership_and_training_from_lokos(client):
    session = login(client, 'D-001')
    creds = {c['kind']: c for c in wallet(client, session)}
    assert set(creds) == {'shg_membership', 'training'}
    shg = {x['claim']: x['value'] for x in creds['shg_membership']['claims']}
    assert shg['shg_name'] == 'Maa Durga Jeevika SHG' and shg['role'] == 'treasurer' and shg['shg_id'] == 'SHG-101'
    assert shg['clf_name'] == 'Barachatti Jeevika CLF' and shg['shg_formed_on'] == '2016-03-12'
    assert creds['shg_membership']['state'] == 'active' and creds['shg_membership']['valid_until']
    assert len(wallet(client, session)) == 2  # listing again issues nothing new
    issued = [e for e in client.get('/v1/audit', headers=WS).json()['events'] if e['kind'] == 'credential_issued']
    assert len(issued) == 2 and 'shg.shg_name' in next(e for e in issued if e['detail']['type'] == 'ShgMembershipCredential')['detail']['read']


def test_issued_credential_is_a_w3c_vc_sd_jwt_with_every_subject_claim_hidden(client):
    from core import store
    session = login(client, 'D-001')
    wallet(client, session)
    row = next(r for r in store.didi_credentials(ws_name(), 'D-001') if r['type'] == 'ShgMembershipCredential')
    jwt = row['sd_jwt'].split('~')[0]
    header, payload = (json.loads(base64.urlsafe_b64decode(p + '=' * (-len(p) % 4))) for p in jwt.split('.')[:2])
    assert header['typ'] == 'vc+sd-jwt' and header['alg'] == 'ES256' and header['kid'].startswith('did:web:')
    assert payload['@context'][0] == 'https://www.w3.org/ns/credentials/v2'
    assert payload['type'] == ['VerifiableCredential', 'ShgMembershipCredential'] and payload['id'].startswith('urn:uuid:')
    assert payload['credentialStatus']['type'] == 'BitstringStatusListEntry' and 'jwk' in payload['cnf']
    assert set(payload['credentialSubject']) == {'_sd'}  # nothing about her is readable without a disclosure
    assert 'Maa Durga' not in json.dumps(payload) and 'Kharanti' not in json.dumps(payload)


# --- signature verification ----------------------------------------------------------------------

def presentation_for(client, session, kind='shg_membership', claims=('name', 'shg_name'), verifier='bank'):
    from core import store
    from wallet import keys, service, vc
    wallet(client, session)
    row = next(r for r in store.didi_credentials(ws_name(), 'D-001') if r['summary']['kind'] == kind)
    holder = keys.holder_key(ws_name(), 'D-001')
    return row, vc.present(row['sd_jwt'], list(claims), holder, service.audience(verifier), 'nonce-1')


def test_verifier_accepts_a_genuine_presentation_and_rejects_tampering(client):
    from jwcrypto.jwk import JWK
    from sd_jwt.common import SDObj
    from sd_jwt.issuer import SDJWTIssuer
    from wallet import service, vc
    session = login(client, 'D-001')
    _, good = presentation_for(client, session)
    result = vc.verify(good, ws_name(), service.audience('bank'), 'nonce-1')
    assert result['valid'] and result['disclosed'] == {'name': 'Sunita Devi', 'shg_name': 'Maa Durga Jeevika SHG'}

    jwt, *rest = good.split('~')
    head, body, sig = jwt.split('.')
    claims = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    claims['validUntil'] = '2099-01-01T00:00:00Z'
    forged_body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    forged = '~'.join([f'{head}.{forged_body}.{sig}', *rest])
    assert not vc.verify(forged, ws_name(), service.audience('bank'), 'nonce-1')['valid']

    # A disclosure the issuer never signed (a made-up SHG name) does not match any digest, so it is not accepted.
    fake = base64.urlsafe_b64encode(json.dumps(['salt', 'shg_name', 'Fake SHG']).encode()).decode().rstrip('=')
    swapped = vc.verify('~'.join([jwt, fake, *rest[1:]]), ws_name(), service.audience('bank'), 'nonce-1')
    assert 'Fake SHG' not in json.dumps(swapped['disclosed'])

    # Signed by a key that is not the did:web issuer's: the signature check fails.
    other = JWK.generate(kty='EC', crv='P-256')
    from wallet import keys
    rogue = SDJWTIssuer({'iss': keys.did(), 'credentialSubject': {SDObj('name'): 'Sunita Devi'}}, other,
                        extra_header_parameters={'typ': 'vc+sd-jwt', 'kid': keys.kid()}).sd_jwt_issuance + '~'
    checks = {c['check']: c['ok'] for c in vc.verify(rogue, ws_name(), service.audience('bank'))['checks']}
    assert checks['issuer'] and not checks['signature']

    # Bound to the bank: the block office cannot replay it, and a wrong nonce fails.
    replay = {c['check']: c['ok'] for c in vc.verify(good, ws_name(), service.audience('block_office'), 'nonce-1')['checks']}
    assert not replay['holder_binding']
    assert not vc.verify(good, ws_name(), service.audience('bank'), 'other-nonce')['valid']


def test_verify_endpoint_checks_a_pasted_presentation(client):
    session = login(client, 'D-001')
    _, good = presentation_for(client, session)
    out = client.post('/v1/verify', headers=WS, json={'presentation': good, 'verifier': 'bank'}).json()
    assert out['valid'] and out['kind'] == 'shg_membership' and out['labels']['shg_name']['hi'] == 'समूह का नाम'
    bad = client.post('/v1/verify', headers=WS, json={'presentation': good[:-5] + 'xxxxx', 'verifier': 'bank'}).json()
    assert not bad['valid']


# --- revocation and expiry -----------------------------------------------------------------------

def test_revoked_credential_fails_verification_through_the_status_list(client):
    from wallet import service, vc
    session = login(client, 'D-001')
    row, good = presentation_for(client, session)
    assert vc.verify(good, ws_name(), service.audience('bank'), 'nonce-1')['valid']
    assert client.post(f'/v1/wallet/credentials/{row["id"]}/revoke', headers=WS, json={'reason': 'left the SHG'}).json()['state'] == 'revoked'
    after = vc.verify(good, ws_name(), service.audience('bank'), 'nonce-1')
    assert not after['valid'] and {c['check']: c['ok'] for c in after['checks']}['not_revoked'] is False
    status = client.get('/v1/wallet/status', headers=WS).json()
    assert status['credentialSubject']['encodedList'].startswith('u') and vc.is_revoked(ws_name(), row['status_index'])
    # Her wallet no longer offers it; listing issues a fresh membership credential.
    assert [c['id'] for c in wallet(client, session) if c['kind'] == 'shg_membership'] != [row['id']]


def test_expired_credential_fails_verification_and_is_not_offered(client):
    from core import store
    from wallet import keys, service, vc
    session = login(client, 'D-001')
    didi = session['didi']
    old = service._issue(ws_name(), didi, 'training', {'name': 'Sunita Devi', 'course': 'x'}, valid_days=30,
                         when=store.now() - timedelta(days=60))
    presented = vc.present(old['sd_jwt'], ['name'], keys.holder_key(ws_name(), 'D-001'), service.audience('bank'), 'n')
    result = vc.verify(presented, ws_name(), service.audience('bank'), 'n')
    assert not result['valid'] and {c['check']: c['ok'] for c in result['checks']}['not_expired'] is False
    assert service.state(store.get_credential(ws_name(), old['id'])) == 'expired'
    assert service.current(ws_name(), 'D-001', 'training') is None


# --- sharing by voice: selective disclosure and consent ------------------------------------------

def test_share_lakhpati_by_voice_discloses_only_what_the_bank_needs(client, fake_llm):
    session = login(client, 'D-001', agent='plan_execute')
    events, end = run_turn(client, session, SHARE)
    assert fake_llm.calls == []  # code read the intent and worded the answer: no LLM at all
    cards = [e['data'] for e in events if e['type'] == 'consent_request']
    assert [c['purpose']['id'] for c in cards] == ['lakhpati_status_check', 'credential_share']
    assert [i['table'] for i in cards[0]['items']] == ['dar']  # issuing reads her DAR with her consent
    share_card = cards[1]
    assert share_card['items'][0]['fields'] == ['name', 'lakhpati_status', 'status_label', 'rule_id', 'table_version', 'decided_on']
    assert 'बैंक को सिर्फ़ ये बातें दिखेंगी' in share_card['statement']['hi']['speech']
    answer = end['runs'][0]['result']['answer']
    assert answer.startswith('सुनीता जी, आपका लखपति दीदी प्रमाणपत्र बैंक को भेज दिया है; इसमें आपकी स्थिति "लखपति बनने की राह पर" लिखी है।')
    assert 'आमदनी के आँकड़े' in answer and '₹' not in answer and 'लखपति बनने की राह पर' in share_card['statement']['hi']['speech']
    shared = next(e['data'] for e in events if e['type'] == 'wallet' and e['data']['action'] == 'shared')['share']

    received = inbox(client)
    assert len(received) == 1 and received[0]['valid'] and received[0]['share_id'] == shared['share_id']
    got = received[0]['disclosed']
    assert got['lakhpati_status'] == 'ON_TRACK' and got['rule_id'] == '2' and got['table_version'] == '1.2'
    assert set(got) == set(share_card['items'][0]['fields'])
    text = json.dumps(received, ensure_ascii=False)
    for income in ('124800', '1,24,800', '10400', 'annual_income', 'avg_monthly_income', 'basis', 'decision_table'):
        assert income not in text  # her income never existed in the credential; unchosen claims never left the wallet
    assert set(disclosures(received[0]['presentation'])) == set(got)


def test_she_can_choose_fewer_claims_and_the_rest_never_reach_the_verifier(client, fake_llm):
    session = login(client, 'D-001', agent='plan_execute')

    def pick(card):
        if card['purpose']['id'] == 'credential_share':
            return {'button': 'some', 'granted': [{**{k: card['items'][0][k] for k in ('source_id', 'table')},
                                                   'fields': ['name', 'shg_name']}]}
        return {'button': 'yes'}

    events, end = answer_with(client, pick)(session, 'मेरा समूह सदस्यता प्रमाणपत्र बैंक को भेज दो')
    card = next(e['data'] for e in events if e['type'] == 'consent_request')
    assert card['purpose']['id'] == 'credential_share' and 'role' in card['items'][0]['fields']
    received = inbox(client)[0]
    assert received['valid'] and received['disclosed'] == {'name': 'Sunita Devi', 'shg_name': 'Maa Durga Jeevika SHG'}
    assert disclosures(received['presentation']) and set(disclosures(received['presentation'])) == {'name', 'shg_name'}
    text = json.dumps(inbox(client), ensure_ascii=False)
    for hidden in ('treasurer', 'Kharanti', 'SHG-101', 'Gaya', 'Barachatti Jeevika CLF', 'Ujala'):
        assert hidden not in text
    from core import store
    share = store.verifier_shares(ws_name(), 'bank')[0]
    assert 'treasurer' not in share['presentation'] and 'Kharanti' not in share['presentation']


def test_share_consent_is_logged_in_the_ledger_and_becomes_a_receipt(client, fake_llm):
    session = login(client, 'D-001', agent='plan_execute')
    run_turn(client, session, SHARE)
    consents = client.get(f'/v1/sessions/{session["id"]}/consents', headers=WS).json()['consents']
    share = next(c for c in consents if c['purpose']['id'] == 'credential_share')
    assert share['status'] == 'granted' and share['used']
    from core import store
    row = store.get_consent(share['id'])
    assert row['purpose']['verifier'] == 'bank' and row['granted'][0]['fields'][:2] == ['name', 'lakhpati_status']
    events = client.get('/v1/audit', headers=WS).json()['events']
    assert any(e['kind'] == 'consent_decided' and e['detail']['consent_id'] == share['id'] for e in events)
    logged = next(e for e in events if e['kind'] == 'credential_shared')
    assert logged['detail']['consent_id'] == share['id'] and 'name' in logged['detail']['disclosed']
    receipts = [c for c in wallet(client, session) if c['kind'] == 'consent_receipt']
    mine = next(c for c in receipts if any(x['claim'] == 'consent_id' and x['value'] == share['id'] for x in c['claims']))
    assert {x['claim']: x['value'] for x in mine['claims']}['recipient'] == 'बैंक'
    assert len(receipts) == 2  # the DAR read for issuing, and the share


def test_saying_no_sends_nothing(client, fake_llm):
    session = login(client, 'D-001', agent='plan_execute')
    events, end = answer_with(client, lambda card: {'button': 'no' if card['purpose']['id'] == 'credential_share' else 'yes'})(session, SHARE)
    assert inbox(client) == [] and 'नहीं भेजा गया' in end['runs'][0]['result']['answer']


def test_withdrawn_consent_revokes_its_receipt(client, fake_llm):
    session = login(client, 'D-001', agent='plan_execute')
    run_turn(client, session, SHARE)
    wallet(client, session)
    dar = next(c for c in client.get(f'/v1/sessions/{session["id"]}/consents', headers=WS).json()['consents']
               if c['purpose']['id'] == 'lakhpati_status_check')
    client.delete(f'/v1/consents/{dar["id"]}', headers=WS)
    receipts = [{x['claim']: x['value'] for x in c['claims']} for c in wallet(client, session) if c['kind'] == 'consent_receipt']
    assert [r['decision'] for r in receipts if r['consent_id'] == dar['id']] == ['withdrawn']


def test_listing_by_voice_shows_the_wallet_card(client, fake_llm):
    session = login(client, 'D-001', agent='plan_execute')
    run_turn(client, session, SHARE)
    events, end = run_turn(client, session, LIST)
    card = next(e['data'] for e in events if e['type'] == 'wallet')
    kinds = [c['kind'] for c in card['credentials']]
    assert {'shg_membership', 'training', 'lakhpati'} <= set(kinds) and kinds.count('consent_receipt') == 2
    answer = end['runs'][0]['result']['answer']
    assert answer.startswith('सुनीता जी, आपके वॉलेट में 5 प्रमाणपत्र हैं:') and 'लखपति दीदी प्रमाणपत्र' in answer
    assert not [e for e in events if e['type'] == 'consent_request'] and fake_llm.calls == []


def test_a_second_share_reuses_the_lakhpati_credential(client, fake_llm):
    session = login(client, 'D-001', agent='plan_execute')
    run_turn(client, session, SHARE)
    events, _ = run_turn(client, session, 'लखपति दीदी का प्रमाणपत्र ब्लॉक कार्यालय को भेजो')
    assert [e['data']['purpose']['id'] for e in events if e['type'] == 'consent_request'] == ['credential_share']
    assert inbox(client, 'block_office')[0]['valid'] and len(inbox(client, 'bank')) == 1


# --- the business plan ---------------------------------------------------------------------------

def test_business_plan_is_saved_with_her_approval_and_its_hash(client, fake_llm):
    session = login(client, 'D-001', agent='plan_execute')
    plan = {'needs_records': True, 'business_plan': 'dairy', 'plan_changes': {'cows_to_add': 1}, 'purpose': 'other_question',
            'items': [], 'scheme_topics': [], 'steps': ['Build the dairy plan'], 'wallet': None}
    fake_llm.script += [json.dumps(plan)]
    events, _ = run_turn(client, session, 'मेरी डेयरी बढ़ाने का बिज़नेस प्लान बना दो, 1 गाय और')
    file = next(e['data'] for e in events if e['type'] == 'file')
    events, end = run_turn(client, session, 'मेरा बिज़नेस प्लान वॉलेट में रखो')
    card = next(e['data'] for e in events if e['type'] == 'consent_request')
    assert card['purpose']['id'] == 'plan_approval' and 'plan_sha256' in card['items'][0]['fields']
    issued = next(e['data'] for e in events if e['type'] == 'wallet')['credentials'][0]
    claims = {x['claim']: x['value'] for x in issued['claims']}
    digest = hashlib.sha256(client.get(f'/v1/files/{file["file_id"]}', headers=WS).content).hexdigest()
    assert claims['plan_sha256'] == digest and claims['approved_by_didi'] is True and claims['emi'] == 3013
    assert claims['approval_consent_id'] == card['consent_id'] and 'वॉलेट में रख दिया है' in end['runs'][0]['result']['answer']


def test_saving_a_plan_before_one_exists_says_so(client, fake_llm):
    session = login(client, 'D-001', agent='plan_execute')
    _, end = run_turn(client, session, 'मेरा बिज़नेस प्लान वॉलेट में रखो')
    assert 'अभी आपका कोई बिज़नेस प्लान नहीं बना है' in end['runs'][0]['result']['answer']


# --- reading her words ---------------------------------------------------------------------------

@pytest.mark.parametrize('text, expected', [
    (LIST, ('list', None, None)),
    (SHARE, ('share', 'lakhpati', 'bank')),
    ('मेरे सारे प्रमाणपत्रों की सूची बताओ', ('list', None, None)),
    ('समूह सदस्यता का सर्टिफिकेट ब्लॉक को भेज दो', ('share', 'shg_membership', 'block_office')),
    ('लखपति वाला बैंक को भेज दो', ('share', 'lakhpati', 'bank')),
    ('मेरा बिज़नेस प्लान वॉलेट में रखो', ('save_plan', 'business_plan', None)),
    ('send my lakhpati credential to the bank', ('share', 'lakhpati', 'bank')),
])
def test_wallet_intents(text, expected):
    from agents import wallet
    found = wallet.detect(text)
    assert (found['action'], found['kind'], found['verifier']) == expected


@pytest.mark.parametrize('text', ['क्या मैं लखपति दीदी हूँ?', 'मेरी डेयरी का बिज़नेस प्लान बनाओ', 'मेरी कुल बचत कितनी है?',
                                  'लखपति दीदी का प्रमाणपत्र कैसे मिलता है?', 'बैंक लिंकेज क्या होता है?', 'क्या मेरा समूह बैंक लोन के लिए तैयार है?'])
def test_other_questions_are_not_wallet_questions(text):
    from agents import wallet
    assert wallet.detect(text) is None


def test_no_wallet_code_calls_nvidia():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / 'server'
    for path in [*(root / 'wallet').glob('*.py'), root / 'agents' / 'wallet.py']:
        assert 'nvidia' not in path.read_text().lower(), path
