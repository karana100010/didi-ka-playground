"""Error reporting to a Sentry-compatible server (locally GlitchTip at http://localhost:8020; see the README).

Off unless SENTRY_DSN is set, and always off on Vercel and in tests, because event payloads can carry register
values. Server exceptions are captured by the SDK's FastAPI integration; browser errors arrive through
/v1/client-error and are forwarded with `client()`.
"""
import os

import settings

_on = False


def enabled():
    return _on


def init():
    global _on
    dsn = os.environ.get('SENTRY_DSN', '')
    if not dsn or settings.HOSTED or os.environ.get('PYTEST_CURRENT_TEST'):
        return False
    import sentry_sdk
    sentry_sdk.init(dsn=dsn, environment=os.environ.get('SENTRY_ENVIRONMENT', 'local'), send_default_pii=False,
                    traces_sample_rate=float(os.environ.get('SENTRY_TRACES', '0')))
    _on = True
    return True


def client(report):
    """One browser error: {message, stack, page, kind}. Short fields only; nothing else from the page is sent."""
    if not _on:
        return False
    import sentry_sdk
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('source', 'browser')
        scope.set_tag('page', str(report.get('page', ''))[:200])
        scope.set_context('browser_error', {k: str(report.get(k, ''))[:4000] for k in ('kind', 'stack', 'page')})
        scope.fingerprint = ['browser', str(report.get('message', ''))[:200]]
        sentry_sdk.capture_message(f"[browser] {str(report.get('message', ''))[:500]}", level='error')
    return True
