import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

gramstack = pytest.importorskip('gramstack')
from connectors.base import ConnectorError
from connectors.gramstack_example import GramstackMembershipConnector


@pytest.fixture
def sdk():
    fixture_path = os.environ.get('GRAMSTACK_SDK_FIXTURE')
    if not fixture_path:
        pytest.skip('Set GRAMSTACK_SDK_FIXTURE to the local SDK conformance fixture')
    config = json.loads(Path(fixture_path).read_text())
    assert config['public_origin'].startswith('http://127.0.0.1:')
    config.pop('issuer')
    config.pop('redirect_url')

    def control(path, body=None):
        payload = json.dumps(body).encode() if body is not None else None
        request = Request(config['public_origin'] + '/_fixture/' + path, data=payload)
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    return gramstack.Client(gramstack.Config(**config)), control


def start(client):
    return GramstackMembershipConnector.request_consent(
        client, member_reference='member-12345', purpose='credit_eligibility',
        retention_policy_id='terms-v1', create_key='voice-example-create', grant_key='voice-example-grant',
    )


def test_explicit_handoff_and_one_exact_retrieval(sdk):
    client, control = sdk
    control('reset', {})
    accepted, connector = start(client)
    assert accepted.interaction_url.endswith('/interactions/' + accepted.request_id)
    assert connector.tables() == {'shg.membership': ['membership_status']}
    with pytest.raises(ConnectorError) as pending:
        connector.rows('shg.membership')
    assert pending.value.code == 'consent_required'
    assert control('counts')['fetches'] == 0

    control('status', {'status': 'CONSENTED'})
    with pytest.raises(ConnectorError):
        connector.rows('shg.membership', ['savings_summary'])
    with pytest.raises(ConnectorError):
        connector.rows('shg.membership', where={'membership_status': 'active'})
    assert control('counts')['fetches'] == 0
    assert connector.rows('shg.membership', ['membership_status']) == [{'membership_status': 'active'}]
    assert connector.receipt_id == 'receipt-1'
    with pytest.raises(ConnectorError) as repeated:
        connector.rows('shg.membership')
    assert repeated.value.code == 'retrieval_attempted'
    assert control('counts')['fetches'] == 1


@pytest.mark.parametrize('outcome', ['REJECTED', 'CANCELLED'])
def test_stopped_request_does_not_retrieve(sdk, outcome):
    client, control = sdk
    control('reset', {})
    _, connector = start(client)
    if outcome == 'CANCELLED':
        assert connector.cancel().status == 'CANCELLED'
    else:
        control('status', {'status': outcome})
    with pytest.raises(ConnectorError) as stopped:
        connector.rows('shg.membership')
    assert stopped.value.code == 'consent_required'
    assert control('counts')['fetches'] == 0


def test_disconnect_consumes_the_local_attempt(sdk):
    client, control = sdk
    control('reset', {'mode': 'disconnect'})
    _, connector = start(client)
    control('status', {'status': 'CONSENTED'})
    with pytest.raises(ConnectorError) as unknown:
        connector.rows('shg.membership')
    assert unknown.value.code == 'delivery_unknown'
    with pytest.raises(ConnectorError) as repeated:
        connector.rows('shg.membership')
    assert repeated.value.code == 'retrieval_attempted'
    assert control('counts')['fetches'] == 1
