import { LANGUAGES, adminAuth, api, busy, errorNotice, h, session, topBar } from '/api.js';

topBar('/login');

const picker = document.getElementById('picker');
const select = document.getElementById('didi');
const preview = document.getElementById('preview');
const button = document.getElementById('login');
const message = document.getElementById('message');
let didis = [];

const language = code => LANGUAGES[code] || code || '';
const firstName = didi => String(didi.name || didi.didi_id).split(' ')[0];

function render(didi) {
    const speaks = didi.asr_language && didi.asr_language !== didi.language
        ? `${language(didi.language)} · speaks ${language(didi.asr_language)} (ASR: ${didi.asr_language})` : language(didi.language);
    const place = [didi.village, didi.block, didi.district, didi.state].filter(Boolean).join(', ');
    const facts = [
        ['Didi id', didi.didi_id], ['Language', speaks], ['SHG', [didi.shg_name, didi.shg_id].filter(Boolean).join(' · ')],
        ['Role', didi.role], ['Place', place], ['Household', didi.household_id],
    ].filter(([, value]) => value);
    preview.replaceChildren(h('div', { class: 'stack' },
        h('div', { class: 'didi' },
            h('span', { class: 'avatar hi', 'aria-hidden': 'true' }, String(didi.name_local || didi.name || '?').charAt(0)),
            h('div', {}, h('div', { class: 'name' }, didi.name), didi.name_local ? h('div', { class: 'hi', style: 'font-size:18px' }, didi.name_local) : null)),
        h('dl', { class: 'facts' }, ...facts.flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, String(v))]))));
    button.textContent = `Log in as ${firstName(didi)}`;
}

try {
    const data = await api('/v1/didis');
    didis = data.didis;
    document.getElementById('source-name').textContent = `the users table of ${data.source.name}`;
    select.replaceChildren(...didis.map(d => h('option', { value: d.didi_id },
        [d.name, d.name_local].filter(Boolean).join(' · ') + (d.language ? ` (${language(d.language).split(' · ')[0]})` : ''))));
    picker.hidden = false;
    if (didis.length) render(didis[0]);
    else button.disabled = true;
} catch (error) {
    message.replaceChildren(errorNotice(error));
    if (error.code === 'no_primary') message.append(h('p', {}, h('a', { class: 'btn primary', href: '/connect' }, 'Connect data')));
}

for (const type of ['change', 'input']) select.addEventListener(type, () => render(didis.find(d => String(d.didi_id) === select.value)));

button.addEventListener('click', () => busy(button, 'Logging in…', async () => {
    try {
        const created = await api('/v1/sessions', { method: 'POST', body: { didi_id: select.value } });
        session.set(created.id);
        location.href = '/chat';
    } catch (error) {
        message.replaceChildren(errorNotice(error));
    }
}));

// --- admin ---------------------------------------------------------------------------------

const adminEls = Object.fromEntries(['form', 'user', 'password', 'signin', 'picker', 'who', 'signout', 'didi', 'reason', 'open', 'message']
    .map(id => [id, document.getElementById(`admin-${id}`)]));

function showAdmin(user) {
    adminEls.form.hidden = Boolean(user);
    adminEls.picker.hidden = !user;
    if (!user) return;
    adminEls.who.textContent = `Signed in as ${user}`;
    adminEls.didi.replaceChildren(...didis.map(d => h('option', { value: d.didi_id }, `${d.didi_id} · ${[d.name, d.name_local].filter(Boolean).join(' · ')}`)));
}

async function startAdmin() {
    if (location.hash === '#admin') document.getElementById('admin').scrollIntoView();
    let user = null;
    if (adminAuth.get()) {
        try { user = (await api('/v1/admin/me')).user; } catch { adminAuth.set(null); }
    }
    showAdmin(user);
}

adminEls.form.addEventListener('submit', event => {
    event.preventDefault();
    busy(adminEls.signin, 'Signing in…', async () => {
        try {
            const out = await api('/v1/admin/login', { method: 'POST', body: { user: adminEls.user.value, password: adminEls.password.value } });
            adminEls.password.value = '';
            adminAuth.set(out.token);
            adminEls.message.replaceChildren();
            showAdmin(out.user);
        } catch (error) {
            adminEls.message.replaceChildren(errorNotice(error));
        }
    });
});

adminEls.signout.addEventListener('click', () => { adminAuth.set(null); showAdmin(null); });

adminEls.open.addEventListener('click', () => {
    const reason = adminEls.reason.value.trim();
    if (reason.length < 3) {
        adminEls.message.replaceChildren(h('p', { class: 'notice error', role: 'alert' }, 'Say why you are opening her records. It is recorded.'));
        adminEls.reason.focus();
        return;
    }
    adminAuth.reason(adminEls.didi.value, reason);
    location.href = `/chat?admin=${encodeURIComponent(adminEls.didi.value)}`;
});

startAdmin();
