import { api, busy, errorNotice, h, toast, topBar } from '/api.js';

const status = await topBar('/connect');
const maxMb = status?.max_upload_mb || 4;
document.getElementById('max-size').textContent = `Up to ${maxMb} MB.`;
if (status?.service_account) {
    document.getElementById('gsheet-help').textContent =
        `Share the sheet with ${status.service_account} (Viewer). For mock data only, "Anyone with the link can view" also works.`;
}

const list = document.getElementById('sources');
const addMessage = document.getElementById('add-message');
let sources = [];
const open = new Set();      // source ids whose setup form is open
const details = new Map();   // source id -> describe() with sample rows

// --- tabs ----------------------------------------------------------------------------------

const tabs = [...document.querySelectorAll('[role=tab]')];
for (const tab of tabs) {
    tab.addEventListener('click', () => {
        for (const other of tabs) {
            other.setAttribute('aria-selected', String(other === tab));
            document.getElementById(other.getAttribute('aria-controls')).hidden = other !== tab;
        }
    });
}

// --- adding sources ------------------------------------------------------------------------

async function add(work, label) {
    addMessage.replaceChildren(h('p', { class: 'notice' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), ` ${label}`));
    try {
        const source = await work();
        addMessage.replaceChildren();
        details.set(source.id, source);
        if (!source.setup.confirmed) open.add(source.id);
        toast(`Connected ${source.name}: ${Object.keys(source.tables).length} tables`);
        await refresh();
    } catch (error) {
        addMessage.replaceChildren(errorNotice(error));
    }
}

function upload(file) {
    if (!file) return;
    if (file.size > maxMb * 1024 * 1024) {
        addMessage.replaceChildren(errorNotice(new Error(`${file.name} is ${(file.size / 1048576).toFixed(1)} MB. Files over ${maxMb} MB cannot be uploaded yet.`)));
        return;
    }
    const form = new FormData();
    form.append('file', file);
    add(() => api('/v1/sources/upload', { method: 'POST', form }), `Reading ${file.name}…`);
}

document.getElementById('excel-file').addEventListener('change', event => { upload(event.target.files[0]); event.target.value = ''; });
document.getElementById('sqlite-file').addEventListener('change', event => { upload(event.target.files[0]); event.target.value = ''; });

const drop = document.getElementById('drop');
drop.addEventListener('dragover', event => { event.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', event => { event.preventDefault(); drop.classList.remove('over'); upload(event.dataTransfer.files[0]); });

document.getElementById('gsheet-form').addEventListener('submit', event => {
    event.preventDefault();
    const url = document.getElementById('gsheet-url').value.trim();
    add(() => api('/v1/sources/link', { method: 'POST', body: { gsheet_url: url } }), 'Reading the Google Sheet…')
        .then(() => { if (!addMessage.childElementCount) document.getElementById('gsheet-url').value = ''; });
});

document.getElementById('sql-form').addEventListener('submit', event => {
    event.preventDefault();
    const url = document.getElementById('sql-url').value.trim();
    add(() => api('/v1/sources/link', { method: 'POST', body: { sql_url: url } }), 'Connecting to the database…')
        .then(() => { if (!addMessage.childElementCount) document.getElementById('sql-url').value = ''; });
});

document.getElementById('sample').addEventListener('click', event => busy(event.currentTarget, 'Loading…', async () => {
    try {
        await api('/v1/sources/sample', { method: 'POST' });
        toast('Sample registers connected and set up');
        await refresh();
    } catch (error) {
        addMessage.replaceChildren(errorNotice(error));
    }
}));

// --- listing -------------------------------------------------------------------------------

async function refresh() {
    try {
        ({ sources } = await api('/v1/sources'));
    } catch (error) {
        list.replaceChildren(errorNotice(error));
        return;
    }
    const primary = primarySource();
    list.replaceChildren(...(sources.length ? sources.map(card) : [h('p', { class: 'muted' }, 'Nothing connected yet. Add a register above, or use the sample registers.')]));
    const next = document.getElementById('next');
    next.setAttribute('aria-disabled', String(!primary));
    next.classList.toggle('primary', Boolean(primary));
    document.getElementById('next-note').textContent = primary
        ? `Didis come from ${primary.name} › ${primary.setup.users_table}.`
        : 'Set up the source that lists the didis to continue.';
}

document.getElementById('next').addEventListener('click', event => {
    if (event.currentTarget.getAttribute('aria-disabled') === 'true') {
        event.preventDefault();
        toast('First set up the source that lists the didis.');
    }
});

function primarySource() {
    return sources.find(s => s.setup.role === 'primary' && s.setup.confirmed);
}

function rolePill(source) {
    const s = source.setup;
    if (!s.confirmed) return h('span', { class: 'pill todo' }, 'Needs setup');
    if (s.role === 'primary') return h('span', { class: 'pill primary' }, `Lists the didis · ${s.users_table}`);
    return h('span', { class: 'pill linked' }, `Linked by ${s.join_key}`);
}

const KIND = { excel: 'Excel', gsheet: 'Google Sheet', sql: 'SQL' };

function card(source) {
    const node = h('article', { class: `source${source.setup.confirmed ? '' : ' needs'}`, id: `source-${source.id}` });
    const tableNames = Object.keys(source.tables);
    const preview = h('div', { class: 'stack', hidden: true });
    node.append(
        h('div', { class: 'row between' },
            h('div', { class: 'stack', style: 'gap:4px' },
                h('span', { class: 'kind' }, KIND[source.kind] || source.kind),
                h('h3', {}, source.name, rolePill(source)),
                h('span', { class: 'muted small' }, [source.where, source.setup.owner_dept && `Owner: ${source.setup.owner_dept}`].filter(Boolean).join(' · '))),
            h('div', { class: 'row' },
                h('button', { class: 'btn small', type: 'button', onclick: () => toggleSetup(source.id) }, open.has(source.id) ? 'Close setup' : 'Set up'),
                h('button', { class: 'btn small', type: 'button', onclick: () => toggleWording(source.id) }, wordingOpen.has(source.id) ? 'Close wording' : 'Consent wording'),
                h('button', { class: 'btn small', type: 'button', onclick: event => togglePreview(source, preview, event.currentTarget) }, 'Preview'),
                h('button', { class: 'btn small quiet danger', type: 'button', onclick: event => remove(source, event.currentTarget) }, 'Remove'))),
        h('div', { class: 'cols' }, ...tableNames.map(t => h('code', { title: source.tables[t].columns.join(', ') }, `${t} · ${source.tables[t].columns.length}`))),
        preview,
    );
    if (open.has(source.id)) node.append(setupForm(source));
    if (wordingOpen.has(source.id)) node.append(wordingForm(source));
    return node;
}

// --- consent wording: what the didi hears about this source ---------------------------------

const wordingOpen = new Set();
function toggleWording(id) {
    wordingOpen.has(id) ? wordingOpen.delete(id) : wordingOpen.add(id);
    refresh();
}

function wordingForm(source) {
    const w = source.wording;
    const id = source.id;
    const form = h('form', { class: 'setup' });
    const message = h('div');
    const input = (key, value, label, attrs = {}) => h('input', { type: 'text', id: key, value: value || '', 'aria-label': label, autocomplete: 'off', ...attrs });
    const tables = Object.keys(w.fields);
    const sections = tables.map(table => {
        const regHi = input(`w-${id}-${table}-hi`, w.registers[table]?.hi, `${table} name in Hindi`, { class: 'hi' });
        const regEn = input(`w-${id}-${table}-en`, w.registers[table]?.en, `${table} name in English`);
        const rows = Object.entries(w.fields[table]).map(([column, label]) => h('tr', {},
            h('td', { class: 'mono' }, column),
            h('td', {}, input(`w-${id}-${table}-${column}-hi`, label.hi, `${column} label in Hindi`, { class: 'hi', 'data-col': column, 'data-lang': 'hi' })),
            h('td', {}, input(`w-${id}-${table}-${column}-en`, label.en, `${column} label in English`, { 'data-col': column, 'data-lang': 'en' })),
            h('td', {}, h('select', { id: `w-${id}-${table}-${column}-s`, 'data-col': column, 'data-lang': 'sensitivity', 'aria-label': `${column} sensitivity` },
                ...['low', 'medium', 'high'].map(s => h('option', { value: s, selected: s === label.sensitivity }, s))))));
        const section = h('details', { 'data-table': table },
            h('summary', { style: 'cursor:pointer' }, h('b', {}, table), h('span', { class: 'muted small' }, ` · ${w.registers[table]?.hi || ''} · ${w.registers[table]?.en || ''}`)),
            h('div', { class: 'stack', style: 'margin-top:8px' },
                h('div', { class: 'grid2' }, h('label', { class: 'field' }, 'Register name (Hindi)', regHi), h('label', { class: 'field' }, 'Register name (English)', regEn)),
                h('div', { class: 'tw' }, h('table', {}, h('tr', {}, ...['Column', 'Label (Hindi)', 'Label (English)', 'Sensitivity'].map(c => h('th', {}, c))), ...rows))));
        section._get = () => {
            const fields = {};
            for (const el of section.querySelectorAll('[data-col]')) (fields[el.dataset.col] ||= {})[el.dataset.lang] = el.value.trim();
            return { register: { hi: regHi.value.trim(), en: regEn.value.trim() }, fields };
        };
        return section;
    });
    const tplHi = h('input', { type: 'text', id: `w-${id}-tpl-hi`, class: 'hi', value: w.template.hi, autocomplete: 'off' });
    const tplEn = h('input', { type: 'text', id: `w-${id}-tpl-en`, value: w.template.en, autocomplete: 'off' });
    const preview = h('p', { class: 'notice hi' });
    const first = tables[0];
    const showPreview = () => {
        const labels = Object.values(w.fields[first] || {}).slice(0, 2).map(l => l.hi);
        preview.textContent = 'Preview: ' + tplHi.value.replaceAll('{source_name}', w.registers[first]?.hi || first)
            .replaceAll('{fields}', labels.join(' और ')).replaceAll('{purpose}', 'आपके सवाल का जवाब देने के लिए');
    };
    tplHi.addEventListener('input', showPreview);
    showPreview();
    form.append(
        h('p', { class: 'muted small' }, 'These words are what the didi sees and hears on the consent card. Open a table to rename it or relabel its fields.'),
        ...sections,
        h('div', { class: 'grid2' },
            h('label', { class: 'field' }, 'Consent sentence per register (Hindi)', tplHi),
            h('label', { class: 'field' }, 'Consent sentence per register (English)', tplEn)),
        h('p', { class: 'muted small' }, 'Placeholders: {source_name} (the register), {fields} (what will be read, required), {purpose}. The card adds the purpose line, "used only for this question, then deleted" and the question itself.'),
        preview, message,
        h('div', { class: 'row' }, h('button', { class: 'btn primary', type: 'submit' }, 'Save wording')));
    form.addEventListener('submit', event => {
        event.preventDefault();
        const body = { registers: {}, fields: {}, consent: { template: { hi: tplHi.value, en: tplEn.value } } };
        for (const section of sections) {
            const { register, fields } = section._get();
            body.registers[section.dataset.table] = register;
            body.fields[section.dataset.table] = fields;
        }
        busy(form.querySelector('button[type=submit]'), 'Saving…', async () => {
            try {
                await api(`/v1/sources/${id}/wording`, { method: 'PUT', body });
                wordingOpen.delete(id);
                toast('Consent wording saved');
                await refresh();
            } catch (error) { message.replaceChildren(errorNotice(error)); }
        });
    });
    return form;
}

// --- the didi's own words for yes and no ---------------------------------------------------

async function vocabulary() {
    const box = document.getElementById('vocabulary');
    let data;
    try { data = await api('/v1/vocabulary'); } catch (error) { box.replaceChildren(errorNotice(error)); return; }
    const langSelect = h('select', { id: 'vocab-lang', 'aria-label': 'Language' }, ...Object.keys(data.defaults).map(l => h('option', { value: l }, l)));
    const defaults = h('div', { class: 'cols' });
    const table = h('table');
    const custom = structuredClone(data.custom || {});
    const render = () => {
        const lang = langSelect.value;
        const d = data.defaults[lang] || {};
        defaults.replaceChildren(...['yes', 'no', 'why'].flatMap(m => (d[m] || []).map(p => h('code', { title: m, class: 'hi' }, `${p} → ${m}`))));
        const rows = custom[lang] ||= [];
        table.replaceChildren(h('tr', {}, ...['Her words', 'Means', 'Note', ''].map(c => h('th', {}, c))),
            ...rows.map((entry, i) => h('tr', {},
                h('td', {}, h('input', { type: 'text', class: 'hi', value: entry.phrase, id: `vocab-${lang}-${i}-p`, 'aria-label': 'Her words', oninput: e => { entry.phrase = e.target.value; } })),
                h('td', {}, h('select', { id: `vocab-${lang}-${i}-m`, 'aria-label': 'Means', onchange: e => { entry.meaning = e.target.value; } },
                    ...['yes', 'no', 'why'].map(m => h('option', { value: m, selected: m === entry.meaning }, m)))),
                h('td', {}, h('input', { type: 'text', value: entry.note || '', id: `vocab-${lang}-${i}-n`, 'aria-label': 'Note', oninput: e => { entry.note = e.target.value; } })),
                h('td', {}, h('button', { class: 'btn small quiet danger', type: 'button', onclick: () => { rows.splice(i, 1); render(); } }, 'Remove')))));
    };
    langSelect.addEventListener('change', render);
    const save = h('button', { class: 'btn primary', type: 'button' }, 'Save words');
    save.addEventListener('click', () => busy(save, 'Saving…', async () => {
        const body = Object.fromEntries(Object.entries(custom).map(([l, rows]) => [l, rows.filter(r => r.phrase.trim())]));
        try { await api('/v1/vocabulary', { method: 'PUT', body }); toast('Consent words saved'); } catch (error) { toast(error.message, 5000); }
    }));
    box.replaceChildren(
        h('div', { class: 'row between' }, h('h2', {}, 'Words that count as yes or no'), langSelect),
        h('p', { class: 'muted small' }, 'When a didi answers a consent card in her own words, these are checked first. Your words win over the defaults. Anything else goes to the language model, and an unclear answer counts as no.'),
        h('details', {}, h('summary', { class: 'small muted', style: 'cursor:pointer' }, 'Default words'), defaults),
        h('div', { class: 'tw' }, table),
        h('div', { class: 'row' }, h('button', { class: 'btn small', type: 'button', onclick: () => { (custom[langSelect.value] ||= []).push({ phrase: '', meaning: 'yes', note: '' }); render(); } }, 'Add words'), save));
    render();
}

vocabulary();

function toggleSetup(id) {
    open.has(id) ? open.delete(id) : open.add(id);
    refresh();
}

async function togglePreview(source, container, button) {
    if (!container.hidden) { container.hidden = true; button.textContent = 'Preview'; return; }
    container.hidden = false;
    button.textContent = 'Hide preview';
    if (!details.get(source.id)?.tables[Object.keys(source.tables)[0]]?.sample) {
        container.replaceChildren(h('p', { class: 'muted small' }, 'Loading sample rows…'));
        try { details.set(source.id, await api(`/v1/sources/${source.id}`)); } catch (error) { container.replaceChildren(errorNotice(error)); return; }
    }
    const full = details.get(source.id);
    const names = Object.keys(full.tables);
    const body = h('div');
    const tabsRow = h('div', { class: 'tabs', role: 'tablist' }, ...names.map((t, i) =>
        h('button', { role: 'tab', 'aria-selected': String(i === 0), type: 'button', onclick: event => {
            for (const b of tabsRow.children) b.setAttribute('aria-selected', String(b === event.currentTarget));
            body.replaceChildren(sampleTable(full.tables[t]));
        } }, t)));
    body.append(sampleTable(full.tables[names[0]]));
    container.replaceChildren(tabsRow, body, h('p', { class: 'muted small' }, 'First 5 rows of each table. Values are shown only on this setup screen.'));
}

function sampleTable({ columns, sample }) {
    return h('div', { class: 'tw' }, h('table', {},
        h('tr', {}, ...columns.map(c => h('th', {}, c))),
        ...(sample || []).map(row => h('tr', {}, ...columns.map(c => h('td', {}, row[c] == null ? '' : String(row[c])))))));
}

async function remove(source, button) {
    if (button.dataset.confirm !== 'yes') {
        button.dataset.confirm = 'yes';
        button.textContent = 'Click again to remove';
        setTimeout(() => { button.dataset.confirm = ''; button.textContent = 'Remove'; }, 4000);
        return;
    }
    await busy(button, 'Removing…', async () => {
        try {
            await api(`/v1/sources/${source.id}`, { method: 'DELETE' });
            open.delete(source.id);
            details.delete(source.id);
            toast(`Removed ${source.name}`);
            await refresh();
        } catch (error) { addMessage.replaceChildren(errorNotice(error)); }
    });
}

// --- setup form ----------------------------------------------------------------------------

function setupForm(source) {
    const id = source.id;
    const tables = Object.keys(source.tables);
    const s = source.setup;
    const primary = primarySource();
    const canLink = primary && primary.id !== id;
    // A linked source needs a primary one to link to; until there is one, the only choice is primary.
    let role = s.role === 'linked' && canLink ? 'linked' : s.role === 'primary' || !canLink ? 'primary' : 'linked';

    const form = h('form', { class: 'setup' });
    const message = h('div');

    const usersSelect = h('select', { id: `setup-${id}-users` }, ...tables.map(t => h('option', { value: t, selected: t === (s.users_table || guessUsers(tables)) }, t)));
    const keySelect = h('select', { id: `setup-${id}-key` });
    const fillKeys = () => {
        const columns = source.tables[usersSelect.value].columns;
        const want = s.key_column && columns.includes(s.key_column) ? s.key_column : columns.find(c => /^(didi_id|member_id|id)$/i.test(c)) || columns[0];
        keySelect.replaceChildren(...columns.map(c => h('option', { value: c, selected: c === want }, c)));
    };
    usersSelect.addEventListener('change', fillKeys);
    fillKeys();

    const usersColumns = primary ? primary.tables[primary.setup.users_table].columns : [];
    const shared = usersColumns.filter(c => tables.some(t => source.tables[t].columns.includes(c)));
    const joinSelect = h('select', { id: `setup-${id}-join` }, ...(shared.length ? shared : usersColumns).map(c =>
        h('option', { value: c, selected: c === (s.join_key || shared[0]) }, c)));
    const joinNote = h('p', { class: 'muted small' });
    const fillJoinNote = () => {
        const having = tables.filter(t => source.tables[t].columns.includes(joinSelect.value));
        joinNote.textContent = having.length ? `Tables with ${joinSelect.value}: ${having.join(', ')}.` : `No table here has a ${joinSelect.value} column.`;
    };
    joinSelect.addEventListener('change', fillJoinNote);
    fillJoinNote();

    const primaryFields = h('div', { class: 'grid2' },
        h('label', { class: 'field' }, 'Table that lists the didis', usersSelect),
        h('label', { class: 'field' }, 'Column that identifies each didi', keySelect));
    const linkedFields = h('div', { class: 'stack' },
        h('label', { class: 'field' }, `Link rows to a didi by (a column of ${primary ? primary.setup.users_table : 'the users table'})`, joinSelect), joinNote);

    const radio = (value, title, note, disabled) => h('label', { class: 'radio' },
        h('input', { type: 'radio', name: `setup-${id}-role`, value, checked: role === value, disabled, onchange: () => { role = value; show(); } }),
        h('span', {}, h('b', {}, title), h('small', {}, note)));
    const show = () => { primaryFields.hidden = role !== 'primary'; linkedFields.hidden = role !== 'linked'; };

    const owner = h('input', { type: 'text', id: `setup-${id}-owner`, value: s.owner_dept || '', placeholder: 'e.g. LokOS (DAY-NRLM)', autocomplete: 'off' });
    const tableOwners = h('details', {},
        h('summary', { class: 'small muted', style: 'cursor:pointer' }, 'Some tables belong to a different department'),
        h('div', { class: 'grid2', style: 'margin-top:8px' }, ...tables.map(t => h('label', { class: 'field' }, t,
            h('input', { type: 'text', id: `setup-${id}-owner-${t}`, 'data-table': t, value: s.table_owners?.[t] || '', placeholder: 'Same as the source', autocomplete: 'off' })))));
    if (Object.keys(s.table_owners || {}).length) tableOwners.open = true;

    form.append(
        h('div', { class: 'grid2' },
            radio('primary', 'This source lists the didis', 'Its users table fills the login dropdown.', false),
            radio('linked', 'This source links to the didis', canLink ? `Rows match a didi through a column of ${primary.name}.` : 'Set up the source that lists the didis first.', !canLink)),
        primaryFields, linkedFields,
        h('label', { class: 'field' }, 'Department that owns this source (shown in the department view)', owner),
        tableOwners,
        message,
        h('div', { class: 'row' }, h('button', { class: 'btn primary', type: 'submit' }, 'Save setup'),
            h('span', { class: 'muted small' }, 'Field labels and the consent sentence are under "Consent wording".')));
    show();

    form.addEventListener('submit', event => {
        event.preventDefault();
        const body = { role, owner_dept: owner.value.trim(), table_owners: {} };
        for (const input of tableOwners.querySelectorAll('input')) if (input.value.trim()) body.table_owners[input.dataset.table] = input.value.trim();
        if (role === 'primary') Object.assign(body, { users_table: usersSelect.value, key_column: keySelect.value });
        else body.join_key = joinSelect.value;
        busy(form.querySelector('button[type=submit]'), 'Saving…', async () => {
            try {
                await api(`/v1/sources/${id}/setup`, { method: 'PUT', body });
                open.delete(id);
                toast('Setup saved');
                await refresh();
            } catch (error) { message.replaceChildren(errorNotice(error)); }
        });
    });
    return form;
}

function guessUsers(tables) {
    return tables.find(t => /^(users|members|didis)$/i.test(t)) || tables[0];
}

refresh();
