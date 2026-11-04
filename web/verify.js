import { api, errorNotice, h, topBar } from '/api.js';

// The verifier's page: what one verifier (the bank, by default) received from didis' wallets, verified server-side
// from each presentation alone. It shows only disclosed claims.
topBar(null);
const els = Object.fromEntries(['verifier', 'verifier-name', 'refresh', 'inbox', 'paste', 'check', 'pasted'].map(id => [id, document.getElementById(id)]));
const params = new URLSearchParams(location.search);
let current = params.get('as') || 'bank';

const CHECKS = {
    format: 'SD-JWT VC format (vc+sd-jwt)', issuer: 'Issuer DID is trusted', signature: 'Issuer signature',
    holder_binding: 'Holder key binding (this verifier, this nonce)', not_expired: 'Not expired', active: 'Valid from',
    not_revoked: 'Not revoked (status list)',
};

try {
    const { verifiers } = await api('/v1/verifiers');
    els.verifier.replaceChildren(...verifiers.map(v => h('option', { value: v.id, selected: v.id === current }, `${v.name.hi} · ${v.name.en}`)));
} catch (error) { els.inbox.replaceChildren(errorNotice(error)); }
els.verifier.addEventListener('change', () => { current = els.verifier.value; history.replaceState(null, '', `?as=${current}`); load(); });
els.refresh.addEventListener('click', load);
els.check.addEventListener('click', checkPasted);
await load();
setInterval(() => { if (document.visibilityState === 'visible') load(true); }, 5000);

async function load(quiet = false) {
    try {
        const data = await api(`/v1/verifiers/${current}/inbox`);
        els['verifier-name'].textContent = data.verifier.name.hi;
        document.title = `${data.verifier.name.hi} · Verifier · Didi ka Playground`;
        if (quiet && els.inbox.dataset.count === String(data.received.length)) return;
        els.inbox.dataset.count = String(data.received.length);
        els.inbox.replaceChildren(...(data.received.length ? data.received.map(result) :
            [h('p', { class: 'notice' }, 'Nothing received yet. In the chat, a didi can say “लखपति दीदी का प्रमाणपत्र बैंक को भेजो”.')]));
        const target = location.hash && document.getElementById(location.hash.slice(1));
        if (target && !quiet) { target.scrollIntoView({ block: 'start' }); target.classList.add('focus'); }
    } catch (error) { if (!quiet) els.inbox.replaceChildren(errorNotice(error)); }
}

async function checkPasted() {
    try {
        const out = await api('/v1/verify', { method: 'POST', body: { presentation: els.paste.value.trim(), verifier: current } });
        els.pasted.replaceChildren(result(out));
    } catch (error) { els.pasted.replaceChildren(errorNotice(error)); }
}

function result(r) {
    const claims = Object.entries(r.disclosed || {});
    return h('article', { class: `card verified ${r.valid ? 'ok' : 'bad'}`, id: r.share_id || null },
        h('div', { class: 'between' },
            h('div', {}, h('h2', { class: 'hi', style: 'font-size:22px' }, r.title?.hi || r.type || 'Credential'),
                h('div', { class: 'small muted' }, [r.title?.en, r.type, r.received_at ? `received ${r.received_at.replace('T', ' ')}` : null].filter(Boolean).join(' · '))),
            h('span', { class: `badge ${r.valid ? 'ok' : 'bad'}` }, r.valid ? '✓ सत्यापित · Verified' : '✗ अमान्य · Not valid')),
        claims.length ? h('table', { class: 'claims' }, h('tbody', {}, ...claims.map(([k, v]) => h('tr', {},
            h('th', {}, h('span', { class: 'hi' }, r.labels?.[k]?.hi || k), h('span', { class: 'small muted' }, r.labels?.[k]?.en || '')),
            h('td', { class: k.endsWith('sha256') ? 'mono' : 'hi' }, show(v))))))
            : h('p', { class: 'muted' }, 'No claims disclosed.'),
        r.hidden ? h('p', { class: 'small muted hi' }, 'बाकी दावे छिपे हैं: वे इस प्रस्तुति में हैं ही नहीं। · Other claims were withheld and never reached this verifier.') : null,
        h('ul', { class: 'checks' }, ...r.checks.map(c => h('li', { class: c.ok ? 'ok' : 'bad' },
            h('b', {}, c.ok ? '✓' : '✗'), ` ${CHECKS[c.check] || c.check}`, c.detail ? h('span', { class: 'small muted' }, ` · ${c.detail}`) : null))),
        h('div', { class: 'small muted' }, `Issuer: ${r.issuer?.name || ''} (${r.issuer?.id || '?'}) · valid ${r.valid_from || '?'} → ${r.valid_until || 'no expiry'}`),
        r.presentation ? h('details', {}, h('summary', { class: 'small' }, 'Raw presentation (what was received)'),
            h('pre', { class: 'mono raw' }, r.presentation)) : null);
}

function show(value) {
    if (value === true) return 'हाँ · yes';
    if (value === false) return 'नहीं · no';
    if (Array.isArray(value)) return value.join(', ');
    if (typeof value === 'number') return value.toLocaleString('en-IN');
    return String(value);
}
