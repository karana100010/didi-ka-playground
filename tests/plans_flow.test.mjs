import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// web/plans/flow.js mounts nothing without a document; its model is left on globalThis.GramstackFlow.
const ctx = vm.createContext({});
vm.runInContext(readFileSync(new URL('../web/plans/flow.js', import.meta.url), 'utf8'), ctx);
const flow = ctx.GramstackFlow;
const all = flow.SCENARIOS.flatMap(sc => sc.steps);

test('the flow model is well formed', () => {
    assert.deepEqual([...flow.problems], []);
    assert.deepEqual(flow.SCENARIOS.map(sc => sc.id).join(), 'call,app,third,history');
});

test('every check on the plan passes', () => {
    assert.equal(flow.CHECKS.length, 9);
    assert.deepEqual(flow.CHECKS.filter(c => !c.ok).map(c => c.t).join('; '), '');
});

test('Bhashini hosts the model: it receives her record fields, and nothing secret', () => {
    const into = all.filter(s => s.to === 'bhashini');
    assert.ok(into.some(s => s.kind === 'record'));
    assert.ok(!into.some(s => s.kind === 'secret'));
});

test('a request that is fetched walks the lifecycle states in order', () => {
    for (const id of ['call', 'app', 'third']) {
        const states = flow.SCENARIOS.find(sc => sc.id === id).steps.map(s => s.state).filter(Boolean);
        assert.equal(states.join(), flow.STATES.join(), id);
    }
});

test('withdrawal ends the unused request and nothing is fetched afterwards', () => {
    const steps = flow.SCENARIOS.find(sc => sc.id === 'history').steps;
    assert.equal(steps.at(-1).reached, 'WITHDRAWN');
    assert.ok(!steps.some(s => s.kind === 'record'));
});

test('every link has a path that starts and ends outside the cards', () => {
    const inside = ([x, y]) => flow.NODES.some(n => Math.abs(x - n.x) < 84 && Math.abs(y - n.y) < 24);
    for (const key of flow.PAIRS.keys()) {
        const numbers = flow.pathOf(key).match(/-?\d+(\.\d+)?/g).map(Number);
        assert.ok(numbers.length >= 6 && numbers.every(Number.isFinite), key);
        assert.ok(!inside(numbers.slice(0, 2)) && !inside(numbers.slice(-2)), `${key} ends inside a card`);
    }
});
