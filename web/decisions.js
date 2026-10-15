import { api, errorNotice, h, topBar } from '/api.js';

topBar(null);
const box = document.getElementById('tables');

try {
    const { decisions } = await api('/v1/decisions');
    box.replaceChildren(...decisions.map(render));
} catch (error) {
    box.replaceChildren(errorNotice(error));
}

function render(t) {
    const inputs = t.inputs.map(i => i.name);
    const result = h('div');
    const fields = t.inputs.map(i => h('label', { class: 'field' }, i.label.en,
        h('input', { type: 'text', id: `${t.id}-${i.name}`, 'data-name': i.name, autocomplete: 'off', placeholder: i.name })));
    const form = h('form', { class: 'stack' }, h('div', { class: 'grid2' }, ...fields),
        h('div', { class: 'row' }, h('button', { class: 'btn primary', type: 'submit' }, 'Try these inputs')), result);
    form.addEventListener('submit', async event => {
        event.preventDefault();
        const values = {};
        for (const input of form.querySelectorAll('input')) {
            const raw = input.value.trim();
            values[input.dataset.name] = raw === '' ? null : Number.isNaN(Number(raw)) ? raw : Number(raw);
        }
        try {
            const out = await api(`/v1/decisions/${t.id}/evaluate`, { method: 'POST', body: { inputs: values } });
            result.replaceChildren(h('div', { class: 'notice' },
                h('b', {}, `${out.outcome} · ${out.outcome_label.en || ''} `), h('span', { class: 'hi' }, out.outcome_label.hi || ''),
                h('div', { class: 'small' }, `Rules matched: ${out.matched_rules.join(', ') || 'none'}`),
                ...out.reasons.map(r => h('div', { class: 'small hi' }, `${r.en} · ${r.hi}`)),
                out.missing_inputs.length ? h('div', { class: 'small' }, `Missing: ${out.missing_inputs.join(', ')}`) : null));
        } catch (error) { result.replaceChildren(errorNotice(error)); }
    });
    return h('article', { class: 'card' },
        h('div', { class: 'row between' }, h('h2', {}, `${t.name.en} `, h('span', { class: 'hi muted', style: 'font-size:18px' }, t.name.hi)),
            h('span', { class: 'mono small muted' }, `${t.id} · v${t.version}`)),
        h('p', { class: 'small muted' }, `Asks consent for: ${t.needs.map(n => `${n.table} (${n.fields.join(', ')})`).join('; ')} · purpose ${t.purpose}`),
        h('div', { class: 'tw' }, h('table', { class: 'dmn' },
            h('tr', {}, h('th', { class: 'hp', title: `Hit policy: ${t.hit_policy}` }, t.hit_policy[0]), ...inputs.map(n => h('th', {}, n)),
                h('th', { class: 'out' }, t.hit_policy === 'COLLECT' ? 'finding' : 'outcome'), h('th', {}, 'Basis')),
            ...t.rules.map((rule, n) => h('tr', { class: rule.basis === 'official' ? 'official' : '' },
                h('td', { class: 'rn' }, String(n + 1)), ...inputs.map(name => h('td', { class: 'mono' }, rule.when[name] ?? '–')),
                h('td', { class: 'out mono' }, rule.outcome), h('td', { class: 'small', style: 'white-space:normal' }, rule.note || rule.basis))))),
        t.aggregate ? h('p', { class: 'small muted' }, `Outcome: ${t.aggregate.none} when no row matches, otherwise ${t.aggregate.any} with the matching rows as findings.`) : null,
        h('p', { class: 'small muted' }, t.notes),
        h('details', {}, h('summary', { style: 'cursor:pointer' }, 'Try it with your own inputs'), form));
}
