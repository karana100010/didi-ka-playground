"""W3C Verifiable Credentials 2.0 secured as SD-JWT (VC-JOSE-COSE, typ vc+sd-jwt), with sd-jwt-python.

Every credentialSubject claim is selectively disclosable, with decoy digests so a verifier cannot count what was
withheld. The envelope (type, issuer, validity, status, holder key) is always visible. A presentation carries only
the disclosures the didi chose and a key-binding JWT signed with her holder key for one verifier and nonce.
Revocation is a W3C Bitstring Status List per workspace.
"""
import base64
import gzip
import json
import uuid
from datetime import datetime, timedelta, timezone

from sd_jwt.common import SDObj
from sd_jwt.holder import SDJWTHolder
from sd_jwt.issuer import SDJWTIssuer
from sd_jwt.verifier import SDJWTVerifier

import settings
from core import store

from . import keys

TYP = 'vc+sd-jwt'
CONTEXT = ['https://www.w3.org/ns/credentials/v2', 'https://www.w3.org/ns/credentials/examples/v2']
STATUS_BITS = 131072  # the spec's minimum list length (16 KB), so one bit does not single out a holder


def status_url():
    return f'{settings.PUBLIC_URL}/v1/wallet/status'


def issue(workspace, didi_id, vc_type, subject, valid_days=None, issuer_name='GramStack mock issuer', when=None):
    """Sign a credential for her. Returns (credential id, the SD-JWT with all disclosures, the visible envelope)."""
    when = (when or store.now()).replace(microsecond=0)
    until = when + timedelta(days=valid_days) if valid_days else None
    index = store.next_status_index(workspace)
    holder = keys.holder_key(workspace, didi_id)
    credential_id = f'urn:uuid:{uuid.uuid4()}'
    envelope = {
        'iss': keys.did(), 'iat': int(when.timestamp()), 'nbf': int(when.timestamp()),
        **({'exp': int(until.timestamp())} if until else {}),
        '@context': CONTEXT, 'id': credential_id, 'type': ['VerifiableCredential', vc_type],
        'issuer': {'id': keys.did(), 'name': issuer_name},
        'validFrom': _iso(when), **({'validUntil': _iso(until)} if until else {}),
        'credentialStatus': {'id': f'{status_url()}#{index}', 'type': 'BitstringStatusListEntry', 'statusPurpose': 'revocation',
                             'statusListIndex': str(index), 'statusListCredential': status_url()},
        'cnf': {'jwk': keys.public_jwk(holder)},
    }
    claims = {**envelope, 'credentialSubject': {SDObj(k): v for k, v in subject.items() if v not in (None, '')}}
    issuer = SDJWTIssuer(claims, keys.issuer_key(), add_decoy_claims=True,
                         extra_header_parameters={'typ': TYP, 'cty': 'vc', 'kid': keys.kid()})
    return credential_id, issuer.sd_jwt_issuance, {**envelope, 'status_index': index, 'issued_at': when, 'expires_at': until}


def present(sd_jwt, claims, holder_key, audience, nonce):
    """Her presentation for one verifier: only `claims` of credentialSubject disclosed, bound to her key."""
    holder = SDJWTHolder(sd_jwt)
    holder.create_presentation({'credentialSubject': {c: True for c in claims}}, nonce=nonce, aud=audience,
                               holder_key=holder_key)
    return holder.sd_jwt_presentation


def disclosed_claims(sd_jwt):
    """Every claim an SD-JWT discloses, without checking the signature (the holder reading her own credential)."""
    holder = SDJWTHolder(sd_jwt)
    return {key: value for _, key, value in holder._hash_to_decoded_disclosure.values()}


def verify(presentation, workspace, audience, nonce=None):
    """Check a presentation the way a verifier would, from the presentation alone. Returns the checks and only the
    claims it discloses."""
    out = {'valid': False, 'checks': [], 'disclosed': {}, 'hidden': False, 'type': None, 'issuer': None,
           'valid_from': None, 'valid_until': None, 'credential_id': None}

    def check(name, ok, detail=''):
        out['checks'].append({'check': name, 'ok': bool(ok), 'detail': detail})
        return ok

    parts = (presentation or '').strip().split('~')
    if len(parts) < 2:
        check('format', False, 'not an SD-JWT presentation')
        return out
    try:
        header = _part(parts[0], 0)
        unverified = _part(parts[0], 1)
    except ValueError:
        check('format', False, 'the SD-JWT does not decode')
        return out
    check('format', header.get('typ') == TYP, f'typ {header.get("typ")}')
    try:
        keys.resolve(unverified.get('iss'), header)
        check('issuer', True, f'{unverified.get("iss")} (did:web, trusted)')
    except ValueError as error:
        check('issuer', False, str(error))
        return out
    try:
        payload = SDJWTVerifier(presentation, keys.resolve).get_verified_payload()
        check('signature', True, f'{header.get("alg")} by {header.get("kid")}')
    except Exception as error:  # noqa: BLE001 - any failure here means the signature or a disclosure is wrong
        check('signature', False, f'{type(error).__name__}: {error}')
        return out
    binding = parts[-1]
    if not binding:
        check('holder_binding', False, 'no key-binding JWT')
    else:
        expected = nonce or _part(binding, 1).get('nonce')
        try:
            SDJWTVerifier(presentation, keys.resolve, expected_aud=audience, expected_nonce=expected)
            check('holder_binding', True, f'signed by the holder for {audience}' + ('' if nonce else ' (nonce not checked)'))
        except Exception as error:  # noqa: BLE001
            check('holder_binding', False, str(error))
    now = int(store.now().timestamp())
    check('not_expired', 'exp' not in payload or payload['exp'] > now,
          f'valid until {payload.get("validUntil")}' if payload.get('validUntil') else 'no expiry')
    check('active', payload.get('nbf', 0) <= now + 60, f'valid from {payload.get("validFrom")}')
    status = payload.get('credentialStatus') or {}
    revoked = is_revoked(workspace, int(status.get('statusListIndex', -1))) if status else None
    check('not_revoked', status and not revoked, 'status list: revoked' if revoked else 'status list: active' if status else 'no status')
    subject = payload.get('credentialSubject') or {}
    raw_subject = unverified.get('credentialSubject') or {}
    out.update(disclosed={k: v for k, v in subject.items() if k != '_sd'}, hidden=bool(raw_subject.get('_sd')),
               type=next((t for t in payload.get('type', []) if t != 'VerifiableCredential'), None),
               issuer=payload.get('issuer'), valid_from=payload.get('validFrom'), valid_until=payload.get('validUntil'),
               credential_id=payload.get('id'))
    out['valid'] = all(c['ok'] for c in out['checks'])
    return out


# --- status list ----------------------------------------------------------------------------

def status_list(workspace):
    """The revocation Bitstring Status List: gzip of the bits, multibase base64url ('u' prefix)."""
    bits = bytearray(STATUS_BITS // 8)
    for index in store.revoked_indices(workspace):
        if 0 <= index < STATUS_BITS:
            bits[index // 8] |= 0x80 >> (index % 8)
    encoded = 'u' + base64.urlsafe_b64encode(gzip.compress(bytes(bits), mtime=0)).decode().rstrip('=')
    return {'@context': CONTEXT[:1], 'id': status_url(), 'type': ['VerifiableCredential', 'BitstringStatusListCredential'],
            'issuer': keys.did(), 'credentialSubject': {'id': f'{status_url()}#list', 'type': 'BitstringStatusList',
                                                        'statusPurpose': 'revocation', 'encodedList': encoded}}


def is_revoked(workspace, index):
    """Read her bit from the status list the way a verifier would, from the encoded list."""
    encoded = status_list(workspace)['credentialSubject']['encodedList'][1:]
    bits = gzip.decompress(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
    return 0 <= index < len(bits) * 8 and bool(bits[index // 8] & (0x80 >> (index % 8)))


def _part(jwt, n):
    segment = jwt.split('.')[n]
    try:
        return json.loads(base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4)))
    except (ValueError, IndexError) as error:
        raise ValueError('bad JWT') from error


def _iso(moment):
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if isinstance(moment, datetime) else moment
