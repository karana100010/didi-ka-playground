# GRAMStack Python SDK example

**Reference only.** This example shows how an application can use the GRAMStack
Python SDK for one explicitly approved membership-status disclosure. It is
invoked separately from the voice bot's tools, routes, and source configuration.

## Install and configure

From the repository root, install the optional SDK in your virtual environment.

```sh
python -m pip install -r requirements-gramstack-example.txt
```

The platform operator supplies registered consumer OAuth credentials, approved
resource and purpose settings, and client certificates for the private mTLS data
endpoint. Load the credentials from backend configuration. See the
[Python SDK guide](https://docs.gramstack.try-dough.com/developers/sdks/python).

## Request, hand off, and retrieve

Run with `PYTHONPATH=server` from the repository root. The names below represent
backend configuration and an existing product request. Keep both idempotency keys
stable for that request.

```python
from gramstack import Client, Config
from connectors.gramstack_example import GramstackMembershipConnector

client = Client(Config(
    public_origin=gateway_origin,
    token_url=token_url,
    client_id=consumer_client_id,
    client_secret=consumer_client_secret,
    data_origin=private_data_origin,
    data_server_name=data_tls_name,
    ca_file=ca_path,
    certificate_file=client_certificate_path,
    key_file=client_key_path,
))

accepted, connector = GramstackMembershipConnector.request_consent(
    client,
    member_reference=member_reference,
    purpose=approved_purpose,
    retention_policy_id=approved_retention_policy_id,
    create_key=create_idempotency_key,
    grant_key=grant_idempotency_key,
)
# Present accepted.interaction_url to the person in the product UI.

# Later, after they approve the exact notice in the GRAMStack portal:
rows = connector.rows("shg.membership", ["membership_status"])
receipt_id = connector.receipt_id
```

The connector checks the platform's consent state, requests a grant, and uses
the SDK to fetch the single approved field. A voice conversation or the
playground's local consent ledger does not grant platform disclosure authority.
Call `connector.cancel()` before starting a fresh request with a changed scope.

The example tracks the attempt in one connector instance. An application that
runs across processes or restarts must persist its retrieval-attempt state
before sending the data request. After an uncertain result, check platform
status and evidence before taking another action.

## Run the example tests

Use the local TLS/mTLS fixture from a GRAMStack checkout. In a terminal with the
example SDK installed, run the fixture and leave it running during the tests.

```sh
umask 077
fixture_dir=$(mktemp -d)
python /path/to/GRAMStack/sdk/conformance/server.py "$fixture_dir/config.json"
```

In another terminal from this repository, install `requirements-dev.txt` and
run the tests with the fixture's config path.

```sh
GRAMSTACK_SDK_FIXTURE=/path/to/fixture/config.json python -m pytest -q tests/test_gramstack_sdk_example.py
```

The tests cover the portal handoff, consent gate, refusal and cancellation,
exact field scope, one-use retrieval, and a disconnected response. Stop the
fixture and remove its temporary directory after the tests.
