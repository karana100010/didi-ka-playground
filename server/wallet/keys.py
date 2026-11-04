"""The issuer's did:web identity and signing key, and each didi's holder key.

The issuer key (EC P-256, ES256) is generated on first use into settings.WALLET_KEY_DIR, outside the repository, or
read from WALLET_ISSUER_JWK. A hosted deployment must set WALLET_ISSUER_JWK and a public PUBLIC_URL: its disk is wiped on
every cold start, so a generated key would change and every credential issued before would stop verifying. Without
them the wallet refuses to issue or verify (NotConfigured, a 503) instead of signing with a throwaway key. Its public half is published as the did:web DID document at
/.well-known/did.json. Holder keys are kept in the core store: the mock wallet is custodial.
"""
import json
import os

from jwcrypto.jwk import JWK

import settings
from core import store

KEY_ID = 'key-1'
_issuer = None


class NotConfigured(store.NotConfigured):
    """The wallet has no fixed issuer key or public DID. main.py answers it with a 503."""


def problem():
    """Why the wallet cannot sign or verify here, or None."""
    if settings.WALLET_ISSUER_JWK:
        try:
            key = JWK.from_json(settings.WALLET_ISSUER_JWK)
        except Exception:  # noqa: BLE001 - jwcrypto raises several types for a bad JWK
            return 'WALLET_ISSUER_JWK is not a valid JWK.'
        if key.get('kty') != 'EC' or key.get('crv') != 'P-256' or not key.has_private:
            return 'WALLET_ISSUER_JWK must be a private EC P-256 key.'
    elif settings.HOSTED:
        return 'WALLET_ISSUER_JWK is not set. A hosted wallet needs a fixed issuer key (see README, Wallet).'
    if settings.HOSTED and any(host in did() for host in ('localhost', '127.0.0.1')):
        return 'PUBLIC_URL is not set to the public address, so the did:web DID would name localhost.'
    return None


def issuer_key():
    global _issuer
    if _issuer is None:
        reason = problem()
        if reason:
            raise NotConfigured(reason)
        if settings.WALLET_ISSUER_JWK:
            _issuer = JWK.from_json(settings.WALLET_ISSUER_JWK)
        else:
            path = settings.WALLET_KEY_DIR / 'issuer.jwk.json'
            if path.exists():
                _issuer = JWK.from_json(path.read_text())
            else:
                _issuer = JWK.generate(kty='EC', crv='P-256', kid=kid())
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_issuer.export_private())
                os.chmod(path, 0o600)
    return _issuer


def reset():
    """Forget the cached key (tests point WALLET_KEY_DIR somewhere else)."""
    global _issuer
    _issuer = None


def health():
    """For /healthz: which key signs, so a changed key shows up before verifiers notice."""
    reason = problem()
    if reason:
        return {'ok': False, 'reason': reason}
    return {'ok': True, 'did': did(), 'kid': kid(), 'thumbprint': issuer_key().thumbprint(),
            'source': 'env' if settings.WALLET_ISSUER_JWK else 'local_file'}


def did():
    return settings.WALLET_DID


def kid():
    return f'{did()}#{KEY_ID}'


def did_document():
    public = json.loads(issuer_key().export_public())
    public.pop('kid', None)
    return {
        '@context': ['https://www.w3.org/ns/did/v1', 'https://w3id.org/security/suites/jws-2020/v1'],
        'id': did(),
        'verificationMethod': [{'id': kid(), 'type': 'JsonWebKey2020', 'controller': did(), 'publicKeyJwk': public}],
        'assertionMethod': [kid()],
    }


def resolve(issuer_did, header):
    """The issuer's public key from its DID document. Only this playground's own did:web is trusted (a one-entry trust
    list); Phase 2 resolves did:web over HTTPS and checks a trust registry."""
    if issuer_did != did():
        raise ValueError(f'{issuer_did} is not a trusted issuer')
    method = next((m for m in did_document()['verificationMethod'] if m['id'] == header.get('kid')), None)
    if not method:
        raise ValueError(f'the DID document has no key {header.get("kid")!r}')
    return JWK(**method['publicKeyJwk'])


def holder_key(workspace, didi_id):
    jwk = store.get_holder(workspace, didi_id)
    if not jwk:
        jwk = store.add_holder(workspace, didi_id, json.loads(JWK.generate(kty='EC', crv='P-256').export_private()))
    return JWK(**jwk)


def public_jwk(key):
    return json.loads(key.export_public())
