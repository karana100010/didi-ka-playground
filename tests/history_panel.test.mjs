import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('../web/chat.html', import.meta.url), 'utf8');
const chat = readFileSync(new URL('../web/chat.js', import.meta.url), 'utf8');
const css = readFileSync(new URL('../web/app.css', import.meta.url), 'utf8');
const start = chat.indexOf('// --- history panel');
const section = chat.slice(start);
const tag = id => html.match(new RegExp(`<[a-z]+\\b[^>]*\\bid="${id}"[^>]*>`))?.[0] || '';
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

test('markup: the panel starts hidden and the header button is collapsed', () => {
    const aside = tag('history');
    assert.match(aside, /^<aside\b/);
    assert.match(aside, /\shidden(\s|>|=)/);
    assert.match(tag('history-backdrop'), /\shidden(\s|>|=)/);
    const button = tag('history-toggle');
    assert.match(button, /aria-expanded="false"/);
    assert.match(button, /aria-controls="history"/);
    assert.match(button, /aria-label="[^"]+"/);
    const head = html.slice(html.indexOf('<header class="chat-head">'), html.indexOf('</header>'));
    assert.ok(head.includes('id="history-toggle"'), 'history button lives in the chat header');
    for (const id of ['history-close', 'history-clear', 'history-confirm', 'history-list', 'history-detail', 'history-back']) assert.ok(tag(id), id);
    assert.match(tag('history-confirm'), /\shidden(\s|>|=)/);
    assert.match(tag('history-detail'), /\shidden(\s|>|=)/);
});

test('css keeps [hidden] authoritative and never sets display on the hidden panel', () => {
    assert.match(css, /\[hidden\]\{display:none!important\}/);
    assert.doesNotMatch(css, /\.history\[hidden\]\s*\{[^}]*display:\s*(?!none)/);
});

test('history code stays outside the region the voice test slices', () => {
    assert.ok(start > chat.indexOf('// --- operator panel'));
    assert.ok(start > chat.indexOf('// One tap starts'));
});

test('every page asks for the new asset version', () => {
    const versions = new Set();
    for (const page of ['chat', 'login', 'connect', 'department', 'decisions', 'index']) {
        const source = readFileSync(new URL(`../web/${page}.html`, import.meta.url), 'utf8');
        assert.doesNotMatch(source, /\?v=20261028"/, page);
        for (const [, v] of source.matchAll(/\?v=(\w+)"/g)) versions.add(v);
    }
    assert.equal(versions.size, 1, [...versions].join(', '));  // one version, bumped together
});

// --- a stubbed DOM for the panel logic ------------------------------------------------------

class El {
    constructor(tagName = 'div', id = '') {
        this.tagName = tagName; this.id = id; this.attrs = {}; this.listeners = {}; this.children = []; this.hidden = false;
        this.disabled = false; this._text = ''; this.isConnected = true;
    }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return this.attrs[k] ?? null; }
    addEventListener(type, f) { (this.listeners[type] ||= []).push(f); }
    fire(type, event = {}) { for (const f of this.listeners[type] || []) f({ preventDefault() {}, ...event }); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes.filter(n => n !== '' && n != null); }
    focus() { harnessFocus.current = this; }
    set textContent(v) { this._text = String(v); this.children = []; }
    get textContent() { return this._text + this.children.map(c => (typeof c === 'string' ? c : c.textContent)).join(''); }
    findAll(pred, out = []) { for (const c of this.children) if (typeof c === 'object') { if (pred(c)) out.push(c); c.findAll(pred, out); } return out; }
}
const harnessFocus = { current: null };

function harness({ sessionId = 's1', getHistory, clearHistory } = {}) {
    const ids = ['history-toggle', 'history', 'history-backdrop', 'history-close', 'history-status', 'history-list', 'history-detail',
        'history-back', 'history-detail-when', 'history-detail-q', 'history-detail-a', 'history-clear', 'history-confirm',
        'history-clear-yes', 'history-clear-no'];
    const nodes = Object.fromEntries(ids.map(id => [id, new El('div', id)]));
    for (const id of ['history', 'history-backdrop', 'history-detail', 'history-confirm']) nodes[id].hidden = true;
    nodes['history-toggle'].setAttribute('aria-expanded', 'false');
    const docListeners = {};
    const toasts = [], calls = [];
    const h = (tagName, attrs = {}, ...children) => {
        const node = new El(tagName);
        for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) node.setAttribute(k, v);
        if (attrs?.class) node.className = attrs.class;
        node.append(...children.flat().filter(c => c != null && c !== false));
        return node;
    };
    const context = vm.createContext({
        sessionId, h, toast: m => toasts.push(m), api: async (path, options) => { calls.push(['api', path, options?.method || 'GET']); return { turns: [] }; },
        apiModule: {
            getHistory: getHistory || (async id => { calls.push(['get', id]); return { backend: 'store', turns: [] }; }),
            clearHistory: clearHistory || (async id => { calls.push(['delete', id]); return { cleared: 1 }; }),
        },
        document: {
            getElementById: id => nodes[id] || null,
            addEventListener: (type, f) => (docListeners[type] ||= []).push(f),
        },
    });
    vm.runInContext(section, context);
    harnessFocus.current = null;
    const key = k => { let prevented = false; for (const f of docListeners.keydown || []) f({ key: k, defaultPrevented: false, preventDefault() { prevented = true; } }); return prevented; };
    return { nodes, toasts, calls, key, run: code => vm.runInContext(code, context), focus: () => harnessFocus.current };
}

const at = (y, m, d, hh = 10) => new Date(y, m - 1, d, hh, 0).toISOString();

test('grouping: today, yesterday, then dated days, newest first; undated last', () => {
    const { run } = harness();
    const now = new Date(2026, 9, 28, 15, 0);
    const turns = [
        { turn_id: 'a', question: 'old', at: at(2026, 10, 2) },
        { turn_id: 'b', question: 'today early', at: at(2026, 10, 28, 9) },
        { turn_id: 'c', question: 'yesterday', at: at(2026, 10, 27) },
        { turn_id: 'd', question: 'today late', at: at(2026, 10, 28, 14) },
        { turn_id: 'e', question: 'no date', at: 'not a date' },
        { turn_id: 'f', question: 'last year', at: at(2025, 12, 31) },
    ];
    const groups = run('groupHistory')(turns, now);
    assert.deepEqual(JSON.parse(JSON.stringify(groups.map(g => g.items.map(i => i.turn.turn_id)))), [['d', 'b'], ['c'], ['a'], ['f'], ['e']]);
    assert.equal(groups[0].label, 'आज');
    assert.equal(groups[1].label, 'कल');
    assert.equal(groups[2].label, new Date(2026, 9, 2).toLocaleDateString('hi-IN', { day: 'numeric', month: 'long' }));
    assert.equal(groups[3].label, new Date(2025, 11, 31).toLocaleDateString('hi-IN', { day: 'numeric', month: 'long', year: 'numeric' }));
    assert.notEqual(groups[4].label, 'आज');
    assert.equal(run('groupHistory')(undefined, now).length, 0);
});

test('yesterday across a month boundary', () => {
    const { run } = harness();
    assert.equal(run('historyDayLabel')(new Date(2026, 9, 31, 22), new Date(2026, 10, 1, 8)), 'कल');
});

test('opening fetches each time, renders items; Esc and close return focus to the button', async () => {
    const turns = [{ turn_id: 't1', question: 'मेरी बचत कितनी है?', answer: '₹12,000', at: new Date().toISOString() }];
    let gets = 0;
    const hs = harness({ getHistory: async id => { gets++; assert.equal(id, 's1'); return { backend: 'redis', turns }; } });
    const { nodes } = hs;
    nodes['history-toggle'].fire('click');
    assert.equal(nodes.history.hidden, false);
    assert.equal(nodes['history-toggle'].getAttribute('aria-expanded'), 'true');
    assert.equal(hs.focus(), nodes['history-close']);
    await tick();
    assert.equal(gets, 1);
    const items = nodes['history-list'].findAll(n => n.className === 'history-item');
    assert.equal(items.length, 1);
    assert.match(nodes['history-list'].textContent, /आज/);
    assert.equal(nodes['history-clear'].disabled, false);

    // detail view and back
    items[0].fire('click');
    assert.equal(nodes['history-detail'].hidden, false);
    assert.equal(nodes['history-list'].hidden, true);
    assert.equal(nodes['history-detail-q'].textContent, 'मेरी बचत कितनी है?');
    assert.equal(nodes['history-detail-a'].textContent, '₹12,000');
    assert.equal(hs.focus(), nodes['history-back']);
    nodes['history-back'].fire('click');
    assert.equal(nodes['history-detail'].hidden, true);
    assert.equal(nodes['history-list'].hidden, false);
    assert.equal(hs.focus(), items[0]);

    assert.equal(hs.key('Escape'), true);
    assert.equal(nodes.history.hidden, true);
    assert.equal(nodes['history-backdrop'].hidden, true);
    assert.equal(nodes['history-toggle'].getAttribute('aria-expanded'), 'false');
    assert.equal(hs.focus(), nodes['history-toggle']);
    assert.equal(hs.key('Escape'), false, 'Esc does nothing while closed');

    nodes['history-toggle'].fire('click');
    await tick();
    assert.equal(gets, 2, 'fetches again on every open');
    nodes['history-close'].fire('click');
    assert.equal(nodes.history.hidden, true);
    assert.equal(hs.focus(), nodes['history-toggle']);
});

test('empty and error states', async () => {
    const empty = harness();
    empty.nodes['history-toggle'].fire('click');
    await tick();
    assert.match(empty.nodes['history-status'].textContent, /कोई पिछली बात नहीं/);
    assert.equal(empty.nodes['history-clear'].disabled, true);

    let fail = true;
    const broken = harness({ getHistory: async () => { if (fail) throw Object.assign(new Error('Request failed (404).'), { status: 404 }); return { turns: [{ question: 'q', at: new Date().toISOString() }] }; } });
    broken.nodes['history-toggle'].fire('click');
    await tick();
    const alert = broken.nodes['history-status'].findAll(n => n.getAttribute('role') === 'alert');
    assert.equal(alert.length, 1);
    assert.equal(broken.nodes['history-clear'].disabled, true);
    fail = false;
    const retry = alert[0].findAll(n => n.tagName === 'button')[0];
    retry.fire('click');
    await tick();
    assert.equal(broken.nodes['history-list'].findAll(n => n.className === 'history-item').length, 1);
    assert.equal(broken.nodes['history-status'].textContent, '');

    const nobody = harness({ sessionId: null });
    nobody.nodes['history-toggle'].fire('click');
    await tick();
    assert.match(nobody.nodes['history-status'].textContent, /लॉग इन/);
});

test('a slow response after closing does not repaint the panel', async () => {
    let resolve;
    const hs = harness({ getHistory: () => new Promise(r => { resolve = r; }) });
    hs.nodes['history-toggle'].fire('click');
    hs.nodes['history-close'].fire('click');
    resolve({ turns: [{ question: 'late', at: new Date().toISOString() }] });
    await tick();
    assert.equal(hs.nodes['history-list'].children.length, 0);
});

test('clearing needs a confirm, then calls DELETE and empties the list', async () => {
    const turns = [{ question: 'q1', at: new Date().toISOString() }, { question: 'q2', at: new Date().toISOString() }];
    const hs = harness({ getHistory: async () => ({ turns }) });
    hs.nodes['history-toggle'].fire('click');
    await tick();
    hs.nodes['history-clear'].fire('click');
    assert.equal(hs.nodes['history-confirm'].hidden, false);
    assert.equal(hs.nodes['history-clear'].hidden, true);
    assert.equal(hs.focus(), hs.nodes['history-clear-no']);
    hs.nodes['history-clear-no'].fire('click');
    assert.equal(hs.nodes['history-confirm'].hidden, true);
    assert.equal(hs.calls.filter(c => c[0] === 'delete').length, 0, 'no delete without confirm');

    hs.nodes['history-clear'].fire('click');
    hs.nodes['history-clear-yes'].fire('click');
    await tick();
    assert.deepEqual(hs.calls.filter(c => c[0] === 'delete'), [['delete', 's1']]);
    assert.equal(hs.nodes['history-list'].children.length, 0);
    assert.equal(hs.nodes['history-confirm'].hidden, true);
    assert.equal(hs.nodes['history-clear'].disabled, true);
    assert.match(hs.nodes['history-status'].textContent, /मिटा दी/);
});

test('a failed clear keeps her list and says so', async () => {
    const hs = harness({ getHistory: async () => ({ turns: [{ question: 'q', at: new Date().toISOString() }] }),
        clearHistory: async () => { throw new Error('nope'); } });
    hs.nodes['history-toggle'].fire('click');
    await tick();
    hs.nodes['history-clear'].fire('click');
    hs.nodes['history-clear-yes'].fire('click');
    await tick();
    assert.equal(hs.toasts.length, 1);
    assert.equal(hs.nodes['history-list'].findAll(n => n.className === 'history-item').length, 1);
    assert.equal(hs.nodes['history-clear-yes'].disabled, false);
});

test('an api.js without the helpers falls back to the routes directly', async () => {
    const hs = harness();
    hs.run('apiModule.getHistory = undefined; apiModule.clearHistory = undefined;');
    hs.nodes['history-toggle'].fire('click');
    await tick();
    assert.deepEqual(hs.calls.at(-1), ['api', '/v1/sessions/s1/history?limit=50', 'GET']);
    hs.run('clearHistoryNow()');
    await tick();
    assert.deepEqual(hs.calls.at(-1), ['api', '/v1/sessions/s1/history', 'DELETE']);
});
