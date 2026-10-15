import { api, busy, errorNotice, h, toast, topBar } from '/api.js';

topBar(null);
const owner = document.getElementById('owner');
const statusFilter = document.getElementById('filter-status');
const didiFilter = document.getElementById('filter-didi');
let data = null;

async function load(name = '') {
    try {
        data = await api(`/v1/department?owner=${encodeURIComponent(name)}`);
    } catch (error) {
        document.querySelector('main').append(errorNotice(error));
        return;
    }
    if (!data.owners.length) {
        document.getElementById('stats').replaceChildren(h('p', { class: 'notice' }, 'No sources yet. ', h('a', { href: '/connect' }, 'Connect data')));
        return;
    }
    owner.replaceChildren(...data.owners.map(o => h('option', { value: o, selected: o === data.owner }, o)));
    document.getElementById('title').textContent = `Accesses to ${data.owner}`;
    const s = data.stats;
    document.getElementById('stats').replaceChildren(...[[s.accesses_today, 'Accesses today'], [s.didis, 'Didis'],
        [s.refused, 'Refused by the didi'], [s.blocked, 'Blocked by the gateway']].map(([n, label]) =>
        h('div', { class: 'stat' }, h('b', {}, String(n)), h('span', {}, label))));
    const didis = [...new Set(data.log.map(r => `${r.didi_id}|${r.didi}`))];
    didiFilter.replaceChildren(h('option', { value: '' }, 'All'), ...didis.map(d => h('option', { value: d.split('|')[0] }, d.replace('|', ' · '))));
    renderLog();
    renderPolicies();
}

function rows() {
    return data.log.filter(r => (!statusFilter.value || r.kind === statusFilter.value) && (!didiFilter.value || r.didi_id === didiFilter.value));
}

function renderLog() {
    const pill = r => h('span', { class: `pill ${r.kind === 'ok' ? 'ok' : r.kind === 'refused' ? 'no' : 'blk'}` }, r.status);
    const list = rows();
    document.getElementById('log').replaceChildren(
        h('tr', {}, ...['Time', 'Didi', 'Purpose', 'Fields read', 'Consent', 'Status'].map(c => h('th', {}, c))),
        ...(list.length ? list.map(r => h('tr', {},
            h('td', { class: 'mono' }, new Date(r.ts).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })),
            h('td', {}, `${r.didi_id}${r.didi ? ` · ${r.didi}` : ''}`), h('td', {}, r.purpose), h('td', { style: 'white-space:normal' }, r.fields),
            h('td', { class: 'mono' }, r.consent_id || '–'), h('td', {}, pill(r))))
            : [h('tr', {}, h('td', { colspan: 6, class: 'muted' }, 'No accesses yet. Ask a question in the chat that needs these registers.'))]));
}

function renderPolicies() {
    const box = document.getElementById('policies');
    box.replaceChildren(h('h2', {}, 'Your policy'), ...data.sources.map(source => {
        const policy = source.policy;
        const paused = h('input', { type: 'checkbox', id: `paused-${source.id}`, checked: !policy.paused });
        const grid = h('div', { class: 'tw' }, h('table', {},
            h('tr', {}, h('th', {}, 'Purpose'), ...source.tables.map(t => h('th', { title: t.register }, t.table))),
            ...data.purposes.map(p => h('tr', {}, h('td', { style: 'white-space:normal' }, p.en), ...source.tables.map(t => {
                const allowed = policy.purposes?.[t.table];
                return h('td', {}, h('input', { type: 'checkbox', 'data-table': t.table, 'data-purpose': p.id, 'aria-label': `${p.en}: ${t.table}`,
                    checked: !allowed || allowed.includes(p.id) }));
            })))));
        const save = h('button', { class: 'btn primary', type: 'button' }, 'Save policy');
        save.addEventListener('click', () => busy(save, 'Saving…', async () => {
            const purposes = {};
            for (const t of source.tables) {
                purposes[t.table] = [...grid.querySelectorAll(`input[data-table="${t.table}"]:checked`)].map(i => i.dataset.purpose);
            }
            try {
                await api(`/v1/sources/${source.id}/policy`, { method: 'PUT', body: { paused: !paused.checked, purposes } });
                toast(paused.checked ? 'Policy saved' : `${source.name} is paused`);
                load(data.owner);
            } catch (error) { toast(error.message, 5000); }
        }));
        return h('article', { class: 'card' },
            h('div', { class: 'row between' }, h('h3', {}, source.name),
                h('label', { class: 'check' }, paused, 'Exposed to GramStack (untick to pause access now)')),
            h('p', { class: 'muted small' }, 'Tick the purposes that may read each table. A read needs both the didi\'s consent and your tick.'),
            grid, h('div', { class: 'row' }, save));
    }));
}

owner.addEventListener('change', () => load(owner.value));
statusFilter.addEventListener('change', renderLog);
didiFilter.addEventListener('change', renderLog);
document.getElementById('export').addEventListener('click', () => {
    const head = ['time', 'didi_id', 'didi', 'purpose', 'fields', 'consent_id', 'status'];
    const lines = [head.join(','), ...rows().map(r => [r.ts, r.didi_id, r.didi, r.purpose, r.fields, r.consent_id || '', r.status]
        .map(v => `"${String(v ?? '').replaceAll('"', '""')}"`).join(','))];
    const link = h('a', { href: URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' })), download: `access-log-${data.owner}.csv` });
    link.click();
});

load(new URLSearchParams(location.search).get('owner') || '');
