"""Tables and charts for the savings-projection experiment (run via `dspy_projection.py report`).

Prints the held-out results table and the GEPA runs table as Markdown, and writes eval/dspy/projection_charts.html:
the best validation score after each GEPA round for every run, and sample charts (expected vs the model's) from the
held-out set.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'eval' / 'dspy'


def latest_rows():
    """The last held-out row for each (design, prompt label)."""
    rows = {}
    for r in json.loads((OUT / 'projection_results.json').read_text(encoding='utf-8')):
        if r['split'] == 'test':
            rows[(r['design'], r['prompt'])] = r
    return rows


def runs():
    return [json.loads(p.read_text(encoding='utf-8')) for p in sorted(OUT.glob('projection_program-*.json'))]


def tables(rows, gepa):
    lines = ['| Design | Prompt | Held-out score | Parameters | Figures | Chart | Hindi |', '|---|---|---|---|---|---|---|']
    for (design, prompt), r in sorted(rows.items(), key=lambda kv: (kv[0][0], 'projection_program' in kv[0][1], kv[0][1])):
        name = prompt.split('projection_program-')[-1].removesuffix('.json')
        name = f'GEPA ← {name}' if 'projection_program' in prompt else prompt.split('/')[-1]
        p = r['parts']
        lines.append(f'| {design} | {name} | **{r["score"]:.3f}** | {p["params"]:.2f} | {p["figures"]:.2f} | {p["chart"]:.2f} | {p["hindi"]:.2f} |')
    lines += ['', '| GEPA run | Teacher | Val start → best | Candidates | Metric calls | Teacher cost |', '|---|---|---|---|---|---|']
    for g in gepa:
        vals = [c['val'] for c in g['candidates']]
        lines.append(f'| {g["name"]} | {g["teacher"].split("/")[-1]} | {vals[0]:.3f} → {max(vals):.3f} | {len(vals)} | '
                     f'{g["total_metric_calls"]} | ${g["teacher_cost_usd"]} |')
    return '\n'.join(lines)


def curve(g):
    """Best validation score so far, after each candidate is found (x = metric calls spent)."""
    points, best = [], 0
    for c in sorted(g['candidates'], key=lambda c: c['found_at_calls']):
        best = max(best, c['val'])
        points.append({'x': c['found_at_calls'], 'y': round(best, 4)})
    points.append({'x': g['total_metric_calls'], 'y': round(best, 4)})
    return points


def samples(rows):
    """Six held-out charts, the model's next to the expected one: the planner-style prompt before and after GEPA, one per
    kind of question, a miss where there is one (rate and target questions are the hard ones)."""
    data = {c['id']: c for c in json.loads((OUT / 'projection_dataset.json').read_text(encoding='utf-8'))['cases']}
    sources = [r for (d, prompt), r in rows.items() if d == 'full' and ('full-planner-opus' in prompt or prompt == 'full/planner')]
    picked = []
    for kind in ('rate', 'target_add', 'target', 'no_horizon', 'lump', 'percent'):
        options = [(c, r) for r in sources for c in r['cases'] if c['kind'] == kind and isinstance(c['out'].get('chart'), dict)]
        options.sort(key=lambda cr: cr[0]['score'])
        if not options:
            continue
        c, r = options[0]
        case = data[c['id']]
        picked.append({'title': case['question'], 'prompt': r['prompt'].split('projection_program-')[-1].removesuffix('.json'),
                       'score': c['score'], 'feedback': c['feedback'], 'answer': c['out'].get('answer'), 'x': case['expected']['x'],
                       'expected_now': case['expected']['now'], 'expected_new': case['expected']['new'],
                       'model': {str(s.get('name')): s.get('values') for s in c['out']['chart'].get('series') or [] if isinstance(s, dict)}})
    return picked


PAGE = """<!doctype html>
<html lang="hi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Savings Projection Charts</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root { --bg: #fbfaf7; --card: #ffffff; --ink: #1f2328; --muted: #646b73; --line: #e3e1db;
        --a: #2f6fb3; --b: #c2571a; --c: #3c8d5a; --d: #8a55b5; --e: #b8902a; --f: #5a6b7d; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg: #16181b; --card: #1f2226; --ink: #e8e6e1;
        --muted: #9aa1a8; --line: #33373c; --a: #6ea4dd; --b: #e58a52; --c: #6dbb8a; --d: #b48ad8; --e: #d8b65a; --f: #93a3b4; } }
:root[data-theme="dark"] { --bg: #16181b; --card: #1f2226; --ink: #e8e6e1; --muted: #9aa1a8; --line: #33373c;
        --a: #6ea4dd; --b: #e58a52; --c: #6dbb8a; --d: #b48ad8; --e: #d8b65a; --f: #93a3b4; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 system-ui, -apple-system, "Noto Sans Devanagari", sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 17px; margin: 32px 0 8px; }
p.lede { color: var(--muted); margin: 0 0 16px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px; min-width: 0; }
.card h3 { font-size: 14px; margin: 0 0 4px; font-weight: 600; }
.meta { color: var(--muted); font-size: 12.5px; margin: 6px 0 0; }
.wide { height: 340px; } .small { height: 220px; }
</style></head><body><main>
<h1>Savings projection: GEPA on Gemma 4 26B</h1>
<p class="lede">Held-out questions (n = __N__). The score combines parameters 30%, figures 30%, chart 25% and Hindi-only 15%, all computed in code.</p>
<h2>Best validation score after each GEPA round</h2>
<div class="card wide"><canvas id="curves"></canvas></div>
<h2>Sample charts from the held-out set: expected (solid) vs the model (dashed)</h2>
<div class="grid" id="samples"></div>
</main><script>
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const palette = ['--a', '--b', '--c', '--d', '--e', '--f'].map(css);
const RUNS = __RUNS__, SAMPLES = __SAMPLES__;
Chart.defaults.color = css('--muted'); Chart.defaults.borderColor = css('--line');
new Chart(document.getElementById('curves'), {type: 'line', data: {datasets: RUNS.map((r, i) => ({label: r.name, data: r.points,
  stepped: 'before', borderColor: palette[i % 6], backgroundColor: palette[i % 6], pointRadius: 3, borderWidth: 2}))},
  options: {maintainAspectRatio: false, scales: {x: {type: 'linear', title: {display: true, text: 'metric calls (rollouts)'}},
  y: {suggestedMin: 0.4, max: 1, title: {display: true, text: 'validation score'}}}, plugins: {legend: {position: 'bottom'}}}});
const rupee = v => '₹' + Number(v).toLocaleString('en-IN');
SAMPLES.forEach((s, i) => {
  const card = document.createElement('div'); card.className = 'card';
  card.innerHTML = `<h3></h3><div class="small"><canvas></canvas></div><p class="meta"></p>`;
  card.querySelector('h3').textContent = s.title;
  card.querySelector('.meta').textContent = `${s.prompt} · score ${s.score} · ${s.feedback} · "${s.answer || ''}"`;
  document.getElementById('samples').appendChild(card);
  const line = (label, data, color, dash) => ({label, data, borderColor: color, backgroundColor: color, borderDash: dash ? [5, 4] : [], pointRadius: 2, borderWidth: 2});
  new Chart(card.querySelector('canvas'), {type: 'line', data: {labels: s.x, datasets: [
    line('expected: अभी की बचत से', s.expected_now, palette[0], false), line('expected: बढ़ी हुई बचत से', s.expected_new, palette[1], false),
    line('model: अभी की बचत से', s.model['अभी की बचत से'] || [], palette[0], true), line('model: बढ़ी हुई बचत से', s.model['बढ़ी हुई बचत से'] || [], palette[1], true)]},
    options: {maintainAspectRatio: false, scales: {y: {ticks: {callback: rupee}}}, plugins: {legend: {labels: {boxWidth: 10, font: {size: 10}}}}}});
});
</script></body></html>"""


def main():
    rows, gepa = latest_rows(), runs()
    print(tables(rows, gepa))
    n = next(iter(rows.values()))['n'] if rows else 0
    html = (PAGE.replace('__N__', str(n))
            .replace('__RUNS__', json.dumps([{'name': g['name'], 'points': curve(g)} for g in gepa], ensure_ascii=False))
            .replace('__SAMPLES__', json.dumps(samples(rows), ensure_ascii=False)))
    (OUT / 'projection_charts.html').write_text(html, encoding='utf-8')
    print(f'\nwrote {(OUT / "projection_charts.html").relative_to(ROOT)}')


if __name__ == '__main__':
    main()
