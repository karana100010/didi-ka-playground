"""Settings from environment variables. Locally, a `.env` file in the project root fills in any that are unset.

On Vercel (VERCEL=1) every setting comes from the project's environment variables:
    BHASHINI_API_KEY, NVIDIA_API_KEY, PLAYGROUND_ACCESS_CODE, DATABASE_URL, BLOB_READ_WRITE_TOKEN,
    GOOGLE_SERVICE_ACCOUNT (optional)
Import this module before `bhashini`, so BHASHINI_CONF from `.env` is seen when the kit's config loads.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
MOCK = ROOT / 'data' / 'mock'
LOCAL = ROOT / 'data' / 'local'  # local-only core store and uploads; never used on Vercel


def _load_dotenv(path):
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            name, value = line.split('=', 1)
            os.environ.setdefault(name.strip(), value.strip())


_load_dotenv(ROOT / '.env')

HOSTED = bool(os.environ.get('VERCEL'))
ACCESS_CODE = os.environ.get('PLAYGROUND_ACCESS_CODE') or os.environ.get('BHASHINI_ACCESS_CODE', '')
# Neon through the Vercel Marketplace sets DATABASE_URL (and POSTGRES_URL on older integrations).
DATABASE_URL = os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL', '')
BLOB_TOKEN = os.environ.get('BLOB_READ_WRITE_TOKEN', '')
BLOB_ACCESS = os.environ.get('BLOB_ACCESS', 'private')  # match the Blob store's access setting
GOOGLE_SERVICE_ACCOUNT = os.environ.get('GOOGLE_SERVICE_ACCOUNT', '')
MAX_UPLOAD = 4 * 1024 * 1024  # Vercel rejects request bodies over 4.5 MB
FAKE_LLM = os.environ.get('PLAYGROUND_FAKE_LLM') == '1' and not HOSTED  # local UI checks only
LAYA_URL = os.environ.get('LAYA_URL', '' if HOSTED else 'http://localhost:8790').rstrip('/')  # local Laya sidecar
RUN_BUDGET = float(os.environ.get('RUN_BUDGET_SECONDS', '50'))  # stop and checkpoint before Vercel's maxDuration (60 s)
REDIS_URL = os.environ.get('REDIS_URL', '')  # session memory across logins; empty = the core store's turns
MEMORY_TTL_DAYS = float(os.environ.get('MEMORY_TTL_DAYS', '30'))
# Admin sign-in (server/admin.py). Locally an unset password is generated once into data/local/admin.json.
ADMIN_USER = os.environ.get('PLAYGROUND_ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('PLAYGROUND_ADMIN_PASSWORD', '')


def nvidia_key():
    """Read on every call, so a key added to .env locally works without a restart."""
    key = os.environ.get('NVIDIA_API_KEY', '')
    if not key and not HOSTED and (ROOT / '.env').exists():
        for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
            if line.strip().startswith('NVIDIA_API_KEY='):
                key = line.split('=', 1)[1].strip()
    return key


def service_account_email():
    try:
        return json.loads(GOOGLE_SERVICE_ACCOUNT).get('client_email', '') if GOOGLE_SERVICE_ACCOUNT else ''
    except ValueError:
        return ''

# The wallet's issuer: a did:web DID and its signing key. The key is generated on first use and kept outside the
# repository (WALLET_KEY_DIR, default ~/.didi_playground/wallet); a hosted deployment sets WALLET_ISSUER_JWK instead.
PUBLIC_URL = os.environ.get('PUBLIC_URL', 'http://localhost:8800').rstrip('/')
WALLET_DID = os.environ.get('WALLET_DID', '') or 'did:web:' + PUBLIC_URL.split('://', 1)[-1].replace(':', '%3A').replace('/', ':')
WALLET_KEY_DIR = Path(os.environ.get('WALLET_KEY_DIR', '~/.didi_playground/wallet')).expanduser()
WALLET_ISSUER_JWK = os.environ.get('WALLET_ISSUER_JWK', '')
# The wallet is off on Vercel unless WALLET_ENABLED=1 (it then needs WALLET_ISSUER_JWK and PUBLIC_URL).
WALLET_ENABLED = os.environ.get('WALLET_ENABLED', '0' if HOSTED else '1') == '1'
