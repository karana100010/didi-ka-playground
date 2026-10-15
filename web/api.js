// Shared helpers for every page: API calls (with access code and workspace), the top bar, small DOM helpers.

const KEYS = { code: 'playground.accessCode', workspace: 'playground.workspace', session: 'playground.session' };

export const LANGUAGES = {
    hi: 'Hindi · हिंदी', bho: 'Bhojpuri', hne: 'Chhattisgarhi', mag: 'Magahi', mai: 'Maithili', raj: 'Rajasthani',
    mr: 'Marathi · मराठी', ta: 'Tamil · தமிழ்', te: 'Telugu · తెలుగు', bn: 'Bengali · বাংলা', or: 'Odia · ଓଡ଼ିଆ',
    gu: 'Gujarati · ગુજરાતી', kn: 'Kannada · ಕನ್ನಡ', ml: 'Malayalam · മലയാളം', pa: 'Punjabi · ਪੰਜਾਬੀ', en: 'English',
};

export const saved = {
    get(key) { try { return localStorage.getItem(KEYS[key]); } catch { return null; } },
    set(key, value) { try { value == null ? localStorage.removeItem(KEYS[key]) : localStorage.setItem(KEYS[key], value); } catch { /* storage blocked */ } },
};

let memoryWorkspace = null;
export function workspace() {
    let id = saved.get('workspace') || memoryWorkspace;
    if (!id) {
        id = (crypto.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`).replace(/[^A-Za-z0-9-]/g, '');
        saved.set('workspace', id);
        memoryWorkspace = id;
    }
    return id;
}

export class ApiError extends Error {
    constructor(message, code, status) { super(message); this.code = code; this.status = status; }
}

let accessCode = saved.get('code') || '';

/** Headers for calls that do not go through api(): the Bhashini routes and the event stream. */
export function authHeaders() {
    return accessCode ? { 'X-Access-Code': accessCode } : {};
}

/** Ask for the access code (after a 401 outside api()). */
export function requestAccessCode() {
    return askAccessCode(accessCode ? 'That code did not work. Try again.' : '');
}

export async function api(path, { method = 'GET', body, form } = {}, retried = false) {
    const headers = { 'X-Workspace': workspace() };
    if (accessCode) headers['X-Access-Code'] = accessCode;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    let response;
    try {
        response = await fetch(path, { method, headers, body: form || (body !== undefined ? JSON.stringify(body) : undefined) });
    } catch {
        throw new ApiError('Could not reach the playground server. Check your connection and try again.', 'offline', 0);
    }
    const data = await response.json().catch(() => null);
    if (response.status === 401 && data?.error?.code === 'access_denied' && !retried) {
        await askAccessCode(accessCode ? 'That code did not work. Try again.' : '');
        return api(path, { method, body, form }, true);
    }
    if (response.status === 413 && !data) throw new ApiError('That file is too large to upload.', 'too_large', 413);
    if (!response.ok) throw new ApiError(data?.error?.message || `Request failed (${response.status}).`, data?.error?.code, response.status);
    return data;
}

function askAccessCode(message) {
    return new Promise(resolve => {
        const dialog = h('dialog', {},
            h('form', { method: 'dialog', class: 'stack' },
                h('h2', {}, 'Enter the access code'),
                h('p', { class: 'muted small' }, 'This playground is shared with the field team. Ask whoever sent you the link for the code.'),
                message ? h('p', { class: 'notice error' }, message) : null,
                h('label', { class: 'field' }, 'Access code', h('input', { type: 'password', id: 'access-code', autocomplete: 'off', required: true })),
                h('div', { class: 'row', style: 'justify-content:flex-end' }, h('button', { class: 'btn primary', type: 'submit' }, 'Continue'))));
        dialog.addEventListener('cancel', event => event.preventDefault());
        dialog.querySelector('form').addEventListener('submit', () => {
            accessCode = dialog.querySelector('input').value.trim();
            saved.set('code', accessCode);
            dialog.remove();
            resolve();
        });
        document.body.append(dialog);
        dialog.showModal();
    });
}

// --- top bar -------------------------------------------------------------------------------

const STEPS = [['/connect', 'Connect data'], ['/login', 'Log in as a didi'], ['/chat', 'Ask']];

export async function topBar(active) {
    const index = STEPS.findIndex(([href]) => href === active);
    const bar = h('header', { class: 'top' },
        h('a', { class: 'brand', href: '/' }, h('i'), 'Didi ka Playground'),
        index < 0 ? null : h('ol', { class: 'steps', 'aria-label': 'Steps' }, ...STEPS.map(([href, label], i) =>
            h('li', {}, h('a', { href, class: i === index ? 'on' : i < index ? 'done' : '', 'aria-current': i === index ? 'step' : null },
                h('b', {}, i < index ? '✓' : String(i + 1)), label)))),
        h('a', { class: 'btn small quiet', href: '/department', 'aria-current': location.pathname === '/department' ? 'page' : null }, 'Department view'),
        h('a', { class: 'btn small quiet', href: '/decisions', 'aria-current': location.pathname === '/decisions' ? 'page' : null }, 'Decision tables'),
        h('div', { class: 'status', id: 'status' }));
    document.body.prepend(bar);
    try {
        const s = await api('/v1/status');
        const chips = [
            ['Bhashini', s.bhashini_key ? 'ok' : 'bad', s.bhashini_key ? 'Bhashini key set' : 'Bhashini key missing'],
            ['Database', s.database === 'postgres' ? 'ok' : s.database === 'missing' ? 'bad' : 'warn',
                s.database === 'postgres' ? 'Core store: Postgres' : s.database === 'missing' ? 'DATABASE_URL missing' : 'Core store: local SQLite'],
            ['Files', s.files === 'blob' ? 'ok' : s.files === 'missing' ? 'bad' : 'warn',
                s.files === 'blob' ? 'Files: Vercel Blob' : s.files === 'missing' ? 'Blob token missing' : 'Files: local disk'],
        ];
        bar.querySelector('#status').append(...chips.map(([, kind, text]) => h('span', { class: `chip ${kind}`, title: text }, h('span', { class: 'dot' }), text)));
        return s;
    } catch { return null; }
}

// --- DOM helpers ---------------------------------------------------------------------------

export function h(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
        if (value == null || value === false) continue;
        if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
        else if (key === 'text') node.textContent = value;
        else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children.flat()) if (child != null && child !== false) node.append(child);
    return node;
}

export function toast(message, ms = 3200) {
    const node = h('div', { class: 'toast', role: 'status' }, message);
    document.body.append(node);
    setTimeout(() => node.remove(), ms);
}

export async function busy(button, label, work) {
    const original = [...button.childNodes];
    button.disabled = true;
    button.replaceChildren(h('span', { class: 'spinner', 'aria-hidden': 'true' }), label);
    try { return await work(); } finally { button.disabled = false; button.replaceChildren(...original); }
}

export function errorNotice(error) {
    return h('p', { class: 'notice error', role: 'alert' }, h('b', {}, 'Could not do that. '), error.message);
}

export const session = {
    get: () => saved.get('session'),
    set: id => saved.set('session', id),
};
