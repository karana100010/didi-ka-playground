"""Google Sheets, read as an .xlsx export so every tab comes across in one request.

If GOOGLE_SERVICE_ACCOUNT holds a service-account key (JSON) and the sheet is shared with that account,
the Drive API exports it. Otherwise the sheet must be shared as "Anyone with the link can view", which is
only acceptable for mock data. The server builds every Google URL itself from the sheet id; it never
fetches the link it was given.
"""
import json
import re
import time

import requests

import settings

from .base import ConnectorError
from .excel import ExcelConnector

_ID = re.compile(r'/spreadsheets/d/([A-Za-z0-9_-]{20,})')
_BARE_ID = re.compile(r'^[A-Za-z0-9_-]{20,}$')
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
TTL = 60  # seconds a downloaded export is reused, so one question doesn't re-download per fetch
_downloads = {}


def sheet_id(link):
    link = (link or '').strip()
    match = _ID.search(link)
    if match:
        return match.group(1)
    if _BARE_ID.match(link):
        return link
    raise ConnectorError('bad_link', 'That is not a Google Sheets link. Copy the link from the sheet\'s Share button.')


def _service_account_token():
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    info = json.loads(settings.GOOGLE_SERVICE_ACCOUNT)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=['https://www.googleapis.com/auth/drive.readonly'])
    creds.refresh(Request())
    return creds.token


def download(sid):
    cached = _downloads.get(sid)
    if cached and time.time() - cached[0] < TTL:
        return cached[1]
    data = None
    if settings.GOOGLE_SERVICE_ACCOUNT:
        try:
            response = requests.get(f'https://www.googleapis.com/drive/v3/files/{sid}/export',
                                    params={'mimeType': XLSX}, timeout=30,
                                    headers={'Authorization': f'Bearer {_service_account_token()}'})
            if response.ok:
                data = response.content
        except (requests.RequestException, ValueError):
            data = None
    if data is None:
        try:
            response = requests.get(f'https://docs.google.com/spreadsheets/d/{sid}/export',
                                    params={'format': 'xlsx'}, timeout=30)
        except requests.RequestException as error:
            raise ConnectorError('unreachable', f'Could not reach Google Sheets: {error}.', 502) from error
        data = response.content if response.ok else b''
    if not data.startswith(b'PK'):
        who = f' or share it with {settings.service_account_email()}' if settings.service_account_email() else ''
        raise ConnectorError('not_shared', 'Google did not let the playground read this sheet. Set sharing to '
                                           f'"Anyone with the link can view" (mock data only){who}.', 403)
    _downloads[sid] = (time.time(), data)
    return data


def connector(sid):
    return ExcelConnector(lambda: download(sid), f'gsheet:{sid}:{int(time.time() // TTL)}')
