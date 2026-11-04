import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const chat = readFileSync(new URL('../web/chat.js', import.meta.url), 'utf8');
const section = chat.slice(chat.indexOf('// --- savings chart'), chat.indexOf('// --- consent cards'));
const ctx = vm.createContext({});
vm.runInContext(`${section}; Object.assign(globalThis, { rupees, monthLabel, labelIndices, chartGeometry, chartSvg, CHART });`, ctx);
const { rupees, monthLabel, labelIndices, chartGeometry, chartSvg, CHART } = ctx;

// D-001: ₹12,000 now, ₹200 a month against ₹300 a month for 12 months.
const months = Array.from({ length: 12 }, (_, i) => `${2026 + Math.floor((9 + i) / 12)}-${String((9 + i) % 12 + 1).padStart(2, '0')}`);
const spec = { type: 'line', x: months, y_label: '₹', series: [
    { name: 'अभी की बचत से', values: months.map((_, i) => 12000 + 200 * (i + 1)) },
    { name: 'बढ़ी हुई बचत से', values: months.map((_, i) => 12000 + 300 * (i + 1)) }] };
const points = svg => [...svg.matchAll(/<polyline[^>]*points="([^"]+)"/g)].map(m => m[1].split(' ').map(p => p.split(',').map(Number)));

test('rupees use Indian grouping', () => {
    assert.equal(rupees(0), '₹0');
    assert.equal(rupees(999), '₹999');
    assert.equal(rupees(12200), '₹12,200');
    assert.equal(rupees(124800), '₹1,24,800');
    assert.equal(rupees(12345678), '₹1,23,45,678');
    assert.equal(rupees(-1200), '-₹1,200');
    assert.equal(rupees(1999.6), '₹2,000');
});

test('months read as Hindi month-year', () => {
    assert.equal(monthLabel('2026-10', 'hi'), 'अक्टू 2026');
    assert.equal(monthLabel('2027-01', 'en'), 'Jan 2027');
    assert.equal(monthLabel('2027-01', 'ta'), 'Jan 2027');
});

test('long series thin their x labels to a calendar step that fits', () => {
    assert.deepEqual([...labelIndices(3, 100)], [0, 1, 2]);
    assert.deepEqual([...labelIndices(12, 18)], [0, 4, 8]);
    assert.deepEqual([...labelIndices(60, 3.4)], [0, 24, 48]);
    assert.deepEqual([...labelIndices(1, Infinity)], [0]);
    const svg = chartSvg({ ...spec, x: Array(60).fill('2026-10'), series: [{ name: 'a', values: Array.from({ length: 60 }, (_, i) => i * 100) }] });
    const xs = [...svg.matchAll(/class="xlab" x="([\d.]+)"/g)].map(m => Number(m[1]));
    assert.ok(xs.length >= 3 && xs.length <= 5);
    xs.slice(1).forEach((x, i) => assert.ok(x - xs[i] >= 64));
});

test('one polyline point per month, scaled into the plot', () => {
    const svg = chartSvg(spec);
    const lines = points(svg);
    assert.equal(lines.length, 2);
    assert.deepEqual(lines.map(l => l.length), [12, 12]);
    const g = chartGeometry(spec);
    assert.ok(g.lo <= 12200 && g.hi >= 15600);
    for (const [x, y] of lines.flat()) {
        assert.ok(x >= g.plot.left - 0.1 && x <= g.plot.right + 0.1);
        assert.ok(y >= g.plot.top - 0.1 && y <= g.plot.bottom + 0.1);
    }
    // Linear scale: equal monthly steps give equal pixel steps, and the faster saver ends higher (smaller y).
    const [now, more] = lines;
    assert.ok(Math.abs((now[1][1] - now[0][1]) - (now[11][1] - now[10][1])) < 0.25);
    assert.ok(more[11][1] < now[11][1]);
    assert.equal(now[0][0], Math.round(g.plot.left * 10) / 10);
    assert.equal(now[11][0], Math.round(g.plot.right * 10) / 10);
    assert.ok(g.plot.right < CHART.width - 50);  // room for ₹15,600 beside the line end
    assert.match(svg, /₹14,400/);
    assert.match(svg, /₹15,600/);
    assert.match(svg, /अक्टू 2026/);
});

test('flat or single-point series still draw inside the plot', () => {
    const svg = chartSvg({ x: ['2026-10'], series: [{ name: 'a', values: [5000] }, { name: 'b', values: [5000] }] });
    for (const [x, y] of points(svg).flat()) assert.ok(Number.isFinite(x) && Number.isFinite(y));
});

test('series names are escaped in the svg', () => {
    const svg = chartSvg({ ...spec, series: [{ name: '<b>&"', values: spec.series[0].values }] });
    assert.ok(!svg.includes('<b>'));
    assert.match(svg, /&lt;b&gt;&amp;&quot;/);
});

test('a small dip below zero gets a floor just under it, not a whole step down', () => {
    const x = Array.from({ length: 12 }, (_, i) => `2027-${String(i + 1).padStart(2, '0')}`);
    const earned = x.map((_, i) => 6000 * (i + 1) - 15000), sales = x.map((_, i) => 10000 * (i + 1));
    const g = chartGeometry({ x, series: [{ name: 'a', values: sales }, { name: 'b', values: earned }] });
    assert.equal(g.lo, -10000);
    assert.ok(g.hi >= 120000);
    const below = g.ticks.filter(t => t <= 0);
    assert.ok(below.length === 1 && Object.is(below[0], 0));  // ₹0 is the lowest label, and never -₹0
    for (const [, y] of g.points.flat()) assert.ok(y >= g.plot.top - 0.1 && y <= g.plot.bottom + 0.1);
});
