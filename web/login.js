import { LANGUAGES, api, busy, errorNotice, h, session, topBar } from '/api.js';

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
