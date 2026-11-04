import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const chat = read('../web/chat.js'), css = read('../web/app.css'), html = read('../web/chat.html');
const section = chat.slice(chat.indexOf('// --- operator panel'), chat.indexOf('async function renderPanel'));

function harness({ narrow = false, stored = null } = {}) {
    const classes = () => { const set = new Set(); return { set, toggle: (c, on) => { (on ?? !set.has(c)) ? set.add(c) : set.delete(c); }, contains: c => set.has(c) }; };
    const button = () => { const b = { attrs: {}, setAttribute: (k, v) => { b.attrs[k] = v; }, addEventListener: (_, f) => { b.click = f; } }; return b; };
    const store = new Map(stored ? [['playground.operatorPanel', stored]] : []);
    const els = { panel: { classList: classes() }, 'panel-toggle': button(), 'panel-close': button() };
    const layout = { classList: classes() };
    const media = { matches: narrow, addEventListener() {} };
    vm.runInContext(section, vm.createContext({
        els, window: { matchMedia: () => media }, document: { querySelector: () => layout },
        localStorage: { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) },
    }));
    return { els, layout, store, toggle: els['panel-toggle'], close: els['panel-close'] };
}

test('the operator button is shown on every width and controls the panel', () => {
    assert.match(html, /id="panel-toggle"[^>]*aria-controls="panel"/);
    assert.doesNotMatch(css, /#panel-close,#panel-toggle\{display:none\}/);
    assert.match(css, /\.chat-layout\.panel-hidden \.panel\{display:none\}/);
});

test('on a wide screen the panel starts shown, hides, and the choice is remembered', () => {
    const h = harness();
    assert.equal(h.toggle.attrs['aria-expanded'], 'true');
    h.close.click();
    assert.ok(h.layout.classList.contains('panel-hidden'));
    assert.equal(h.toggle.attrs['aria-expanded'], 'false');
    assert.equal(h.store.get('playground.operatorPanel'), 'hidden');
    h.toggle.click();
    assert.ok(!h.layout.classList.contains('panel-hidden'));
    assert.equal(h.store.get('playground.operatorPanel'), 'shown');
    const again = harness({ stored: 'hidden' });
    assert.ok(again.layout.classList.contains('panel-hidden'));
    assert.equal(again.toggle.attrs['aria-expanded'], 'false');
});

test('on a narrow screen it slides over the chat and starts closed', () => {
    const h = harness({ narrow: true });
    assert.equal(h.toggle.attrs['aria-expanded'], 'false');
    h.toggle.click();
    assert.ok(h.els.panel.classList.contains('open'));
    h.toggle.click();
    assert.ok(!h.els.panel.classList.contains('open'));
    assert.equal(h.store.size, 0);   // only the wide-screen choice is remembered
});
