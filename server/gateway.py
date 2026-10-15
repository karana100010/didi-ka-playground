"""The tool gateway: the only way an agent reaches a source's data.

Every read needs (1) a consent from this question that covers the source, table and fields, (2) the owning
department's policy to allow the purpose for that table and the source not to be paused, and (3) a column
that ties the table's rows to the logged-in didi. The filter value comes from her login, never from the
model, so no prompt can make an agent read someone else's rows. Every read and every refusal is logged
where the department view can see it.
"""
from connectors import ConnectorError
from consent import catalog
from core import store


class Blocked(Exception):
    """The gateway refused. The message goes back to the agent as the tool's result."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def owner(source, table):
    setup = source.get('setup') or {}
    return (setup.get('table_owners') or {}).get(table) or setup.get('owner_dept') or 'Unassigned'


def scope(source, table, didi):
    """(column, value, is_group) that restricts `table` to this didi, or Blocked."""
    columns = source['tables'].get(table) or []
    keys = didi.get('keys') or {}
    setup = source.get('setup') or {}
    if setup.get('role') == 'primary' and setup.get('key_column') in columns:
        return setup['key_column'], didi['didi_id'], False
    if setup.get('role') == 'linked' and setup.get('join_key') in columns and keys.get(setup['join_key']) not in (None, ''):
        return setup['join_key'], keys[setup['join_key']], False
    for column in ('didi_id', 'household_id'):
        if column in columns and keys.get(column) not in (None, ''):
            return column, keys[column], False
    if 'shg_id' in columns and keys.get('shg_id'):
        return 'shg_id', keys['shg_id'], True
    raise Blocked('no_scope', f'Table "{table}" has no column that ties its rows to this didi, so it cannot be read.')


def check_policy(source, table, purpose_id):
    policy = (source.get('setup') or {}).get('policy') or {}
    if policy.get('paused'):
        raise Blocked('paused', f'{owner(source, table)} has paused access to {source["name"]}. The register is unavailable right now.')
    allowed = (policy.get('purposes') or {}).get(table)
    if allowed is not None and purpose_id not in allowed:
        raise Blocked('purpose_not_allowed', f'{owner(source, table)} does not allow "{purpose_id}" to read {table}.')


def fetch(ctx, source_id, table, fields, consent_id):
    """Rows of `table` for this didi, limited to granted fields. Raises Blocked with a reason the agent can act on."""
    source = ctx.sources.get(source_id)
    detail = {'table': table, 'fields': list(fields), 'consent_id': consent_id, 'session_id': ctx.session['id'],
              'turn_id': ctx.turn['id'], 'agent': ctx.run['agent']}
    try:
        if not source:
            raise Blocked('unknown_source', f'There is no source {source_id}.')
        detail['owner'] = owner(source, table)
        consent = store.get_consent(consent_id or '', ctx.workspace)
        if not consent or consent['session_id'] != ctx.session['id']:
            raise Blocked('no_consent', 'There is no consent with that id for this didi. Call request_consent first.')
        if consent['turn_id'] != ctx.turn['id']:
            raise Blocked('old_consent', 'That consent was for an earlier question. Consent covers one question only; ask again.')
        if consent['status'] not in ('granted', 'partial'):
            raise Blocked('not_granted', f'She did not give permission (consent is {consent["status"]}). Answer without these records.')
        granted = next((g for g in consent['granted'] if g['source_id'] == source_id and g['table'] == table), None)
        missing = [f for f in fields if not granted or f not in granted['fields']]
        if missing:
            raise Blocked('not_covered', f'Her consent does not cover {table}: {", ".join(missing)}. Only granted fields can be read.')
        detail['purpose'] = consent['purpose']['id']
        check_policy(source, table, consent['purpose']['id'])
        column, value, group = scope(source, table, ctx.didi)
        if group and (ctx.didi.get('role') or '').lower() not in catalog.OFFICE_BEARERS:
            raise Blocked('group_data', f'{table} is group data. Only an office bearer of the SHG can allow it to be read.')
        from sources import connector
        rows = connector(source).rows(table, list(fields), where={column: value})
    except Blocked as blocked:
        store.log(ctx.workspace, 'fetch_blocked', source_id=source_id, didi_id=str(ctx.didi['didi_id']),
                  detail={**detail, 'reason': blocked.code, 'message': blocked.message})
        raise
    except ConnectorError as error:
        store.log(ctx.workspace, 'fetch_blocked', source_id=source_id, didi_id=str(ctx.didi['didi_id']),
                  detail={**detail, 'reason': error.code, 'message': error.message})
        raise Blocked(error.code, error.message) from error
    store.log(ctx.workspace, 'fetch', source_id=source_id, didi_id=str(ctx.didi['didi_id']),
              detail={**detail, 'rows': len(rows), 'scope': column, 'group': group})
    if not consent['used_at']:
        store.set_consent(consent_id, used_at=store.now())
    return rows
