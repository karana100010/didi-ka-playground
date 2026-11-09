"""Reference connector for one explicitly approved GRAMStack membership request."""

from threading import Lock

from gramstack import Client, GramstackError, RemoteError

from .base import Connector, ConnectorError


class GramstackMembershipConnector(Connector):
    """Use one instance per product request, in a single process."""

    def __init__(self, client: Client, request_id: str, grant_key: str):
        self._client = client
        self._request_id = request_id
        self._grant_key = grant_key
        self._lock = Lock()
        self._attempted = False
        self.receipt_id: str | None = None

    @classmethod
    def request_consent(
        cls, client: Client, *, member_reference: str, purpose: str,
        retention_policy_id: str, create_key: str, grant_key: str,
    ):
        accepted = client.create(create_key, {
            'registry': 'lokos',
            'resource': 'shg.membership',
            'subject_hint': {'type': 'member_reference', 'value': member_reference},
            'fields': ['membership_status'],
            'purpose': purpose,
            'access': {'mode': 'single_use', 'valid_for_seconds': 300},
            'recipient_terms': {'retention_policy_id': retention_policy_id},
        })
        return accepted, cls(client, accepted.request_id, grant_key)

    def cancel(self):
        return self._client.cancel(self._request_id)

    def tables(self):
        return {'shg.membership': ['membership_status']}

    def rows(self, table, columns=None, where=None, limit=None):
        fields = self.check(table, columns, where)
        if fields != ['membership_status'] or where or limit not in (None, 1):
            raise ConnectorError('unsupported_query', 'Use the exact membership status request.')

        with self._lock:
            if self._attempted:
                raise ConnectorError(
                    'retrieval_attempted', 'Check the existing request status before proceeding.', 409,
                )
            try:
                status = self._client.status(self._request_id)
                if status.status not in {'CONSENTED', 'AUTHORIZED'}:
                    raise ConnectorError(
                        'consent_required', 'The request needs active GRAMStack consent.', 409,
                    )
                grant = self._client.issue_grant(self._request_id, self._grant_key)
            except GramstackError as error:
                raise ConnectorError('gramstack_unavailable', 'Could not prepare retrieval.', 502) from error

            self._attempted = True

        try:
            result = self._client.retrieve(grant, fields)
        except GramstackError as error:
            if isinstance(error, RemoteError) and 400 <= error.status < 500:
                raise ConnectorError('retrieval_failed', 'GRAMStack declined the retrieval.', 502) from error
            raise ConnectorError(
                'delivery_unknown', 'Check request status and evidence before proceeding.', 502,
            ) from error

        self.receipt_id = result.receipt_id
        return [result.data]
