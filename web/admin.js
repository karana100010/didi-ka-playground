import { adminAuth, api, busy, errorNotice, h, topBar } from '/api.js';

topBar(null);

const form = document.getElementById('reason-form');
const reasonInput = document.getElementById('reason');
const message = document.getElementById('message');
const report = document.getElementById('report');
const BREAKDOWNS = [['grade', 'SHG grade'], ['district', 'District'], ['shg', 'SHG']];
let data = null;
let by = 'grade';

const number = (value, unit = '') => value == null ? '–' : `${unit}${Number(value).toLocaleString('en-IN')}`;
const stat = (value, label) => h('div', { class: 'stat' }, h('b', {}, String(value)), h('span', {}, label));
const table = (head, rows) => h('div', { class: 'tw' }, h('table', {}, h('tr', {}, ...head.map(c => h('th', {}, c))), ...rows));
const notice = (text, cls = '') => h('p', { class: `notice ${cls}`.trim() }, text);

function signedOut(text) {
    message.replaceChildren(notice(text), h('p', {}, h('a', { class: 'btn primary', href: '/login.html#admin' }, 'Admin sign-in')));
    form.hidden = true;
    report.hidden = true;
}

// A blocked or missing section says why, in place of its figures.
function unavailable(section) {
    if (section.blocked) return notice(`Blocked by the department: ${section.blocked.message}`, 'error');
    if (section.missing) return h('p', { class: 'muted small' }, 'No connected source has this register.');
    return null;
}

function shgSection(shg) {
    const box = h('section', { class: 'card stack' }, h('div', {}, h('p', { class: 'eyebrow' }, 'LokOS · SHG registry'), h('h2', {}, 'SHGs and their meetings')));
    const gap = unavailable(shg);
    if (gap) return box.append(gap), box;
    const o = shg.overall;
    const grades = Object.entries(o.grades).map(([g, n]) => `${g}: ${n}`).join(' · ');
    box.append(h('div', { class: 'stats' }, stat(o.shgs, 'SHGs'), stat(o.members, 'Members'), stat(grades || '–', 'SHGs by grade'),
        ...(o.meetings_held == null ? [] : [stat(`${o.meetings_held}/${o.meetings_recorded}`, 'Meetings held'),
            stat(o.attendance_pct == null ? '–' : `${o.attendance_pct}%`, 'Attendance'), stat(number(o.savings_collected, '₹'), 'Savings collected'),
            stat(number(o.loans_given, '₹'), 'Loans given'), stat(o.repayment_pct == null ? '–' : `${o.repayment_pct}%`, 'Repaid on time')])));
    if (shg.meetings_blocked) box.append(notice(`Meeting register blocked: ${shg.meetings_blocked.message}`, 'error'));
    const row = (name, r) => h('tr', {}, h('td', { style: 'white-space:normal' }, name), h('td', {}, String(r.members)),
        h('td', {}, r.meetings_held == null ? '–' : `${r.meetings_held}/${r.meetings_recorded}`),
        h('td', {}, r.attendance_pct == null ? '–' : `${r.attendance_pct}%`), h('td', {}, number(r.savings_collected, '₹')),
        h('td', {}, number(r.loans_given, '₹')), h('td', {}, number(r.repayments_made, '₹')), h('td', {}, r.repayment_pct == null ? '–' : `${r.repayment_pct}%`));
    const head = ['Members', 'Meetings held', 'Attendance', 'Savings collected', 'Loans given', 'Repaid', 'Repaid %'];
    box.append(h('h3', {}, 'Per SHG'),
        table(['SHG', ...head], shg.per_shg.map(r => row(`${r.shg_name} (${r.shg_id}) · Grade ${r.grade}${r.place ? ` · ${r.place}` : ''}`, r))));
    if (shg.hidden_shgs) box.append(h('p', { class: 'muted small' }, `${shg.hidden_shgs} SHG(s) with fewer than ${data.min_group} members hidden.`));
    box.append(h('h3', {}, 'Per district'), table(['District', 'SHGs', ...head], shg.per_district.map(r => {
        const tr = row(r.group, r);
        tr.children[0].after(h('td', {}, String(r.shgs)));
        return tr;
    })));
    return box;
}

// One register's totals, and its figures per group for the chosen breakdown, smaller groups hidden.
function registerSection(eyebrow, title, section) {
    const box = h('section', { class: 'card stack' }, h('div', {}, h('p', { class: 'eyebrow' }, eyebrow), h('h2', {}, title)));
    const gap = unavailable(section);
    if (gap) return box.append(gap), box;
    const unit = section.unit;
    const money = Object.fromEntries(section.metrics.map(m => [m.label, m.unit]));
    if (section.overall.hidden) {
        box.append(h('p', { class: 'muted' }, `Only ${section.overall.n} ${unit} in this register: fewer than ${data.min_group}, so nothing is shown.`));
        return box;
    }
    box.append(h('div', { class: 'stats' }, stat(section.overall.n, `${unit[0].toUpperCase()}${unit.slice(1)} with records`),
        ...Object.entries(section.overall.values).map(([label, v]) => stat(number(v, money[label]), label))));
    const b = section.breakdowns[by];
    if (!b) return box;
    const labels = section.metrics.map(m => m.label);
    const row = (group, r, cls) => h('tr', cls ? { class: cls } : {}, h('td', { style: 'white-space:normal' }, group), h('td', {}, String(r.n)),
        ...labels.map(l => h('td', {}, number(r.values[l], money[l]))));
    const rows = b.rows.map(r => row(r.group, r));
    if (b.others) rows.push(row(`Smaller groups combined (${b.hidden_groups})`, b.others, 'muted'));
    const name = BREAKDOWNS.find(([k]) => k === by)[1];
    if (rows.length) box.append(table([name, unit, ...labels], rows));
    if (b.hidden_groups) {
        box.append(h('p', { class: 'muted small' }, rows.length
            ? `${b.hidden_groups} of ${b.groups} groups hidden or combined so no group under ${data.min_group} ${unit} can be worked out.`
            : `All ${b.groups} groups by ${name.toLowerCase()} have fewer than ${data.min_group} ${unit}, so none is shown.`));
    }
    return box;
}

function render() {
    const o = data.overview;
    const picker = h('select', { id: 'by' }, ...BREAKDOWNS.map(([k, label]) => h('option', { value: k, selected: k === by }, label)));
    picker.addEventListener('change', () => { by = picker.value; render(); });
    const lakhpati = data.lakhpati;
    report.replaceChildren(
        h('p', { class: 'notice', role: 'note' }, h('b', {}, `Admin totals · ${data.admin}. `), 'Reason recorded: ', h('i', {}, data.reason),
            '. Groups under ', String(data.min_group), ' are hidden. ', h('a', { href: '/department' }, 'See the reads in the department view'), '.'),
        h('section', { class: 'stats' }, stat(o.didis, 'Didis'), stat(o.shgs, 'SHGs'), stat(o.districts, 'Districts'), stat(o.households, 'Households')),
        shgSection(data.shg),
        h('label', { class: 'field', style: 'max-width:280px' }, 'Break registers down by', picker),
        registerSection(`Decision table ${lakhpati.decision || 'lakhpati_status'}${lakhpati.version ? ` v${lakhpati.version}` : ''}`, 'Lakhpati Didi status', lakhpati),
        ...data.registers.map(r => registerSection(r.owner || r.table, r.title, r)));
    report.hidden = false;
}

async function show(reason) {
    try {
        data = await api('/v1/admin/aggregates', { method: 'POST', body: { reason } });
        message.replaceChildren();
        render();
    } catch (error) {
        if (error.code === 'admin_required') { adminAuth.set(null); return signedOut('Your admin sign-in ended.'); }
        message.replaceChildren(errorNotice(error));
    }
}

form.addEventListener('submit', event => {
    event.preventDefault();
    const reason = reasonInput.value.trim();
    if (reason.length < 3) return reasonInput.focus();
    adminAuth.reason('aggregate', reason);
    busy(document.getElementById('open'), 'Reading registers…', () => show(reason));
});

if (!adminAuth.get()) signedOut('Sign in as admin to see totals.');
else {
    form.hidden = false;
    reasonInput.value = adminAuth.reason('aggregate');
}
