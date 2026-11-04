import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const chat = read('../web/chat.js');
const section = chat.slice(chat.indexOf('// --- admin view'), chat.indexOf('function adminExit'));
const ctx = vm.createContext({});
vm.runInContext(`${section}; Object.assign(globalThis, { ADMIN_CHARTS, adminChartBody, adminTurnItems });`, ctx);
const { ADMIN_CHARTS, adminChartBody, adminTurnItems } = ctx;
const plain = value => JSON.parse(JSON.stringify(value));

test('the picker offers the basic charts the server draws', () => {
    assert.deepEqual(plain(ADMIN_CHARTS.map(([kind]) => kind)), ['savings', 'loans', 'income', 'wages', 'projection', 'earnings']);
});

test('chart requests carry the reason and clamp their inputs', () => {
    assert.deepEqual(plain(adminChartBody('savings', 'review', { months: 6, extra: 50 })), { kind: 'savings', reason: 'review' });
    assert.deepEqual(plain(adminChartBody('projection', 'r', { months: '6', extra: '150' })), { kind: 'projection', reason: 'r', months: 6, extra: 150 });
    assert.deepEqual(plain(adminChartBody('projection', 'r', { months: '999', extra: '-5' })), { kind: 'projection', reason: 'r', months: 120, extra: 0 });
    assert.deepEqual(plain(adminChartBody('earnings', 'r', { months: '', extra: 'x' })), { kind: 'earnings', reason: 'r', months: 12 });
});

test('her past turns read as the chat shows them, charts after their answer', () => {
    const chart = { title: 't', spec: { x: [], series: [] } };
    const items = adminTurnItems([{ question: 'q1', answer: 'a1', charts: [chart] }, { question: 'q2', answer: null }]);
    assert.deepEqual(plain(items), [{ who: 'me', text: 'q1' }, { who: 'bot', text: 'a1' }, { chart }, { who: 'me', text: 'q2' }]);
    assert.deepEqual(plain(adminTurnItems(undefined)), []);
});

test('the chat page waits for its chart helpers before drawing the admin view', () => {
    assert.ok(chat.indexOf('if (adminDidi) startAdminView();') > chat.indexOf('function addChartCard'));
    assert.match(chat, /if \(adminDidi\) \{\s*\/\//);
});

test('the login page has the admin sign-in and the reason field', () => {
    const html = read('../web/login.html');
    for (const id of ['admin-form', 'admin-password', 'admin-didi', 'admin-reason', 'admin-open']) assert.match(html, new RegExp(`id="${id}"`));
    assert.match(html, /type="password"/);
});

test('only admin calls send the admin token', () => {
    const api = read('../web/api.js');
    assert.match(api, /path\.startsWith\('\/v1\/admin\/'\) && saved\.get\('admin'\)/);
});
