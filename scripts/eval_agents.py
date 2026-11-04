"""Evaluate the agents with Ragas and record the results in the local Langfuse.

For each agent (and model) it asks every case in eval/cases.json through the running playground (consent is
answered yes), reads the run's trace back from Langfuse, scores it with Ragas using an NVIDIA model as the
evaluator, and writes the scores onto that trace. Each agent's runs are also linked to the Langfuse dataset
"didi-agent-eval" as one dataset run, so Langfuse shows the agents side by side.

Ragas metrics:
  ragas_faithfulness     the answer's claims are supported by what the run read (tool results, scheme notes)
  ragas_answer_accuracy  the answer agrees with the reference answer in eval/cases.json
  ragas_tool_call_f1     the run called the expected tools (record table, decision table, plan type), no more

Code check:
  chart_ok               a savings-projection case's chart event ends the "बढ़ी हुई बचत से" line at the expected
                         balance (case "projection"); a chart on any other case counts as wrong

Needs: the playground on http://localhost:8800, Langfuse with keys in .env, and the Claude CLI logged in (Opus 5.5 is the evaluator). Ragas runs in
its own environment (.eval-venv) because it pins an older LangChain:

    .eval-venv/bin/python scripts/eval_agents.py --agents plan_execute react rewoo direct
    .eval-venv/bin/python scripts/eval_agents.py --agents plan_execute native_tools --model nvidia/nemotron-3.5-lightning-30b-a3b
"""
import argparse
import asyncio
import csv
import hashlib
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'server'))
from consent.catalog import REGISTERS, SCHEME_INFO  # noqa: E402  plain data, no app dependencies

APP = 'http://localhost:8800'
WORKSPACE = {'X-Workspace': 'eval-agents-0001'}
EVALUATOR = 'claude-cli/claude-opus-5-5'  # the judge: Opus 5.5 through the logged-in Claude CLI, never NVIDIA
SCORED_TOOLS = {'fetch_records': 'table', 'decide': 'decision_id', 'build_business_plan': 'enterprise_type',
                'project_savings': 'kind', 'wallet_list': 'didi_id', 'issue_credential': 'kind',
                'share_credential': 'verifier', 'find_business_plan': 'didi_id'}
NEW_SERIES = 'बढ़ी हुई बचत से'  # the projection chart's line with the savings she asked about


def env():
    values = {}
    for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
        key, _, value = line.partition('=')
        if key and not key.startswith('#'):
            values[key.strip()] = value.strip().strip('"\'')
    return values


ENV = env()
LANGFUSE = ENV.get('LANGFUSE_BASE_URL') or ENV.get('LANGFUSE_HOST') or 'http://localhost:3000'
AUTH = (ENV['LANGFUSE_PUBLIC_KEY'], ENV['LANGFUSE_SECRET_KEY'])


# --- running the cases ---------------------------------------------------------------------------

def ask(agent, model, case, language_mode='direct'):
    """One case through the playground in a fresh login; returns the run id, answer, seconds and any chart.

    language_mode 'pivot' has Bhashini NMT translate her question to English for the agent and the answer back."""
    session = requests.post(f'{APP}/v1/sessions', headers=WORKSPACE, json={'didi_id': case['didi']}, timeout=30).json()['id']
    prefs = {'agent': agent, 'compare_with': '', 'planner': 'llm', 'critic': True, 'language_mode': language_mode}
    if model:
        prefs['model'] = model
    requests.put(f'{APP}/v1/sessions/{session}/prefs', headers=WORKSPACE, json=prefs, timeout=30)
    started = time.time()
    turn = requests.post(f'{APP}/v1/sessions/{session}/turns', headers=WORKSPACE, json={'text': case['question']}, timeout=30).json()['id']
    after, status, chart = 0, 'running', None
    for _ in range(10):
        with requests.get(f'{APP}/v1/turns/{turn}/stream', headers=WORKSPACE, params={'after': after}, stream=True, timeout=180) as reply:
            for line in reply.iter_lines(decode_unicode=True):
                if not line or not line.startswith('data:'):
                    continue
                event = json.loads(line[5:])
                after = event.get('id') or after
                if event.get('type') == 'chart':
                    chart = event.get('data')
                if event.get('type') == 'consent_request':
                    requests.post(f'{APP}/v1/consents/{event["data"]["consent_id"]}/answer', headers=WORKSPACE,
                                  json={'button': 'yes'}, timeout=30)
        state = requests.get(f'{APP}/v1/turns/{turn}/events', headers=WORKSPACE, params={'after': 10 ** 9}, timeout=30).json()
        status = state['status']
        if status in ('done', 'error', 'failed'):
            break
    run = state['runs'][0]
    answer = (run.get('result') or {}).get('answer') or ''
    return {'run_id': run['id'], 'status': run['status'], 'answer': answer, 'seconds': round(time.time() - started, 1),
            'error': (run.get('error') or {}).get('message', ''), 'model': run['model'], 'chart': chart}


def trace_id(run_id):
    return hashlib.md5(run_id.encode()).hexdigest()  # as server/tracing.py


def observations(run_id):
    for _ in range(6):  # Langfuse ingests asynchronously
        reply = requests.get(f'{LANGFUSE}/api/public/v2/observations', auth=AUTH, timeout=30,
                             params={'traceId': trace_id(run_id), 'limit': 100, 'fields': 'core,basic,io'})
        data = reply.json().get('data') or []
        if any(o['name'] == 'answer' for o in data) or len(data) > 2:
            return data
        time.sleep(3)
    return data


def parse(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def tool_calls(obs):
    """The scored tool calls, reduced to name + the one argument that matters, in order, without repeats."""
    calls = []
    for o in sorted(obs, key=lambda o: o['startTime']):
        name = o['name'].removeprefix('tool: ')
        if not o['name'].startswith('tool: ') or name not in SCORED_TOOLS:
            continue
        args = parse(o.get('input')) or {}
        call = {'name': name, 'args': {SCORED_TOOLS[name]: args.get(SCORED_TOOLS[name]) or args.get('enterprise') or args.get('decision')}}
        if call not in calls:
            calls.append(call)
    return calls


def contexts(obs):
    """What the run had to go on, in plain words: each register it read (named, with its exact totals and rows),
    each decision or plan result, and the scheme notes every agent is given. Raw JSON without the register's
    name makes Ragas mark true claims like "from your savings register" as unsupported."""
    out = []
    for o in sorted(obs, key=lambda o: o['startTime']):
        name = o['name'].removeprefix('tool: ')
        if not o['name'].startswith('tool: ') or name in ('summarise', 'laya_plan') or o.get('output') in (None, '', 'null'):
            continue
        args, result = parse(o.get('input')) or {}, parse(o['output'])
        if name == 'fetch_records' and isinstance(result, dict):
            table = args.get('table', '')
            register = REGISTERS.get(table, (table, table))[1]
            totals = json.dumps(result.get('exact_totals') or {}, ensure_ascii=False)
            rows = json.dumps(result.get('rows') or [], ensure_ascii=False)
            out.append(f'{register} (her own records, table {table}). Exact totals: {totals}. Rows: {rows[:2000]}')
        else:
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            out.append(f'Result of {name.replace("_", " ")}: {text[:2500]}')
    return out + [f'Scheme note: {note}' for note in SCHEME_INFO.values()]


def chart_ok(case, chart):
    """True/False when the case expects a projection or a chart came anyway; None when neither."""
    expected = (case.get('projection') or {}).get('projected_new')
    if expected is None:
        return False if chart else None
    series = ((chart or {}).get('spec') or {}).get('series') or []
    values = next((s.get('values') or [] for s in series if s.get('name') == NEW_SERIES), [])
    return bool(values) and values[-1] == expected


# --- Ragas -------------------------------------------------------------------------------------------

async def score_all(results, cases, concurrency=3):
    from llm import claude_cli
    from ragas.llms.base import InstructorBaseRagasLLM
    from ragas.messages import AIMessage, HumanMessage, ToolCall
    from ragas.metrics.collections import AnswerAccuracy, Faithfulness, ToolCallF1

    class OpusJudge(InstructorBaseRagasLLM):
        """Ragas's structured-output calls answered by Claude through the CLI: the schema goes in the system prompt
        and the JSON reply is validated into the metric's response model (one retry on a bad reply)."""

        def generate(self, prompt, response_model):
            system = ('Reply with one JSON object only, no prose and no code fences, matching this JSON schema:\n'
                      + json.dumps(response_model.model_json_schema(), ensure_ascii=False))
            last = None
            for _ in range(2):
                text = claude_cli.ask(EVALUATOR, system, prompt).strip()
                try:
                    return response_model.model_validate_json(text[text.index('{'):text.rindex('}') + 1])
                except ValueError as error:
                    last = error
            raise RuntimeError(f'evaluator reply did not match the schema: {last}')

        async def agenerate(self, prompt, response_model):
            return await asyncio.to_thread(self.generate, prompt, response_model)

    llm = OpusJudge()
    faithfulness, accuracy, tool_f1 = Faithfulness(llm=llm), AnswerAccuracy(llm=llm), ToolCallF1()
    gate = asyncio.Semaphore(concurrency)

    async def patient(metric, **inputs):
        """Back off and try again on a failed or unparseable score; NaN also counts as a failed try."""
        for wait in (0, 15, 30, 60, 90):
            await asyncio.sleep(wait)
            try:
                value = (await metric.ascore(**inputs)).value
            except Exception as error:  # noqa: BLE001
                if '429' not in str(error) and wait >= 30:
                    raise
                continue
            if number(value):
                return value
        raise RuntimeError('no score after 5 tries (rate limited or unparseable)')

    async def one(r):
        case = cases[r['case']]
        async with gate:
            if r['status'] != 'done' or not r['answer']:
                r.update(ragas_faithfulness=0.0, ragas_answer_accuracy=0.0, ragas_tool_call_f1=0.0)
                return
            if case.get('score_faithfulness') is False:
                r['ragas_faithfulness'] = None  # e.g. a follow-up question: nothing to be faithful to
            else:
                try:
                    r['ragas_faithfulness'] = await patient(faithfulness, user_input=case['question'], response=r['answer'],
                                                            retrieved_contexts=r['contexts'])
                except Exception as error:  # noqa: BLE001 - one failed metric must not stop the evaluation
                    r['ragas_faithfulness'], r['note'] = None, f'faithfulness: {error}'[:200]
            try:
                r['ragas_answer_accuracy'] = await patient(accuracy, user_input=case['question'], response=r['answer'],
                                                           reference=case['reference'])
            except Exception as error:  # noqa: BLE001
                r['ragas_answer_accuracy'], r['note'] = None, f'accuracy: {error}'[:200]
            if not case['tools']:  # nothing should be read: full marks only when nothing was
                r['ragas_tool_call_f1'] = 1.0 if not r['tool_calls'] else 0.0
            else:
                messages = [HumanMessage(content=case['question']),
                            AIMessage(content='', tool_calls=[ToolCall(name=c['name'], args=c['args']) for c in r['tool_calls']])]
                reference = [ToolCall(name=c['name'], args=c['args']) for c in case['tools']]
                r['ragas_tool_call_f1'] = (await tool_f1.ascore(user_input=messages, reference_tool_calls=reference)).value

    await asyncio.gather(*(one(r) for r in results))


# --- Langfuse ------------------------------------------------------------------------------------------

def ensure_dataset(spec):
    requests.post(f'{LANGFUSE}/api/public/datasets', auth=AUTH, timeout=30,
                  json={'name': spec['dataset'], 'description': spec['description']})
    for case in spec['cases']:
        requests.post(f'{LANGFUSE}/api/public/dataset-items', auth=AUTH, timeout=30, json={
            'datasetName': spec['dataset'], 'id': f'{spec["dataset"]}-{case["id"]}',
            'input': {'didi': case['didi'], 'question': case['question']},
            'expectedOutput': {'reference': case['reference'], 'tools': case['tools']}, 'metadata': {'case': case['id']}})


def number(value):
    return isinstance(value, (int, float)) and math.isfinite(value)  # Ragas gives NaN when a metric cannot be scored


def publish(spec, results, run_name):
    for r in results:
        tid = trace_id(r['run_id'])
        for name in ('ragas_faithfulness', 'ragas_answer_accuracy', 'ragas_tool_call_f1', 'chart_ok'):
            if number(r.get(name)):
                requests.post(f'{LANGFUSE}/api/public/scores', auth=AUTH, timeout=30, json={
                    'id': f'{tid}-{name}', 'traceId': tid, 'name': name,  # fixed id: a rescore replaces the value
                    'value': float(r[name]), 'dataType': 'NUMERIC',
                    'comment': f'Ragas, evaluator {EVALUATOR}, case {r["case"]}' if name != 'chart_ok' else
                    f'code check of the projection chart, case {r["case"]}'})
        requests.post(f'{LANGFUSE}/api/public/dataset-run-items', auth=AUTH, timeout=30, json={
            'runName': run_name, 'datasetItemId': f'{spec["dataset"]}-{r["case"]}', 'traceId': tid,
            'metadata': {'agent': r['agent'], 'model': r['model']}})


# --- main ------------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--agents', nargs='+', default=['plan_execute', 'react', 'rewoo', 'direct'])
    parser.add_argument('--model', default='', help='chat model for every run (default: the playground default)')
    parser.add_argument('--cases', nargs='*', help='only these case ids')
    parser.add_argument('--language-modes', nargs='+', default=['direct'], choices=['direct', 'pivot'],
                        help='direct: the agent works in Hindi; pivot: Bhashini translates Hindi to English and back')
    parser.add_argument('--from-runs', help='score the runs saved in this JSON file again, without asking')
    args = parser.parse_args()

    spec = json.loads((ROOT / 'eval' / 'cases.json').read_text(encoding='utf-8'))
    cases = {c['id']: c for c in spec['cases'] if not args.cases or c['id'] in args.cases}
    requests.get(f'{APP}/v1/sources', headers=WORKSPACE, timeout=60)  # an empty workspace gets the sample registers
    ensure_dataset(spec)
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    results = []
    if args.from_runs:
        results = json.loads(Path(args.from_runs).read_text(encoding='utf-8'))
        stamp = datetime.strptime(Path(args.from_runs).name[:13], '%Y%m%d-%H%M').strftime('%Y-%m-%d %H:%M')  # same dataset run
        args.agents = list(dict.fromkeys(r['agent'] for r in results))
    else:
        labels = []
        for agent in args.agents:
            for mode in args.language_modes:
                label = agent if args.language_modes == ['direct'] else f'{agent}@{mode}'  # one row per agent and mode
                labels.append(label)
                for case in cases.values():
                    r = {'agent': label, 'language_mode': mode, 'case': case['id'], **ask(agent, args.model, case, mode)}
                    print(f'{label:20s} {case["id"]:24s} {r["status"]:5s} {r["seconds"]:5.1f}s  {r["answer"][:60] or r["error"][:60]}', flush=True)
                    results.append(r)
        args.agents = labels
        saved = ROOT / 'eval' / 'results' / f'{datetime.now():%Y%m%d-%H%M}-runs.json'
        saved.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'Runs saved to {saved.relative_to(ROOT)} (rescore with --from-runs)', flush=True)
    time.sleep(5)
    for r in results:
        obs = observations(r['run_id'])
        r['tool_calls'], r['contexts'] = tool_calls(obs), contexts(obs)
        scores = {o['name']: parse(o.get('output')) for o in obs if o['name'] == 'metrics'}.get('metrics') or {}
        r['consent_prompts'], r['llm_calls'] = scores.get('consent_prompts'), scores.get('llm_calls')
        if r['case'] in cases:
            r['chart_ok'] = chart_ok(cases[r['case']], r.get('chart'))
    print(f'\nScoring {len(results)} runs with Ragas ({EVALUATOR})…', flush=True)
    asyncio.run(score_all(results, cases))
    out = ROOT / 'eval' / 'results' / f'{datetime.now():%Y%m%d-%H%M}-{"-".join(args.agents)}.csv'
    fields = ['agent', 'language_mode', 'model', 'case', 'status', 'seconds', 'llm_calls', 'consent_prompts', 'ragas_faithfulness',
              'ragas_answer_accuracy', 'ragas_tool_call_f1', 'chart_ok', 'tool_calls', 'answer', 'error', 'note', 'run_id']
    with out.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows({**r, 'tool_calls': json.dumps(r['tool_calls'], ensure_ascii=False)} for r in results)

    for agent in args.agents:
        model = next((r['model'] for r in results if r['agent'] == agent), args.model)
        publish(spec, [r for r in results if r['agent'] == agent], f'{agent} · {model.split("/")[-1]} · {stamp}')

    mean = lambda xs: sum(xs) / len(xs) if xs else float('nan')  # noqa: E731
    print(f'\n{"agent":20s} {"faithful":>8s} {"accuracy":>8s} {"tools F1":>8s} {"chart_ok":>8s} {"done":>5s} {"avg s":>6s} {"consent":>7s}')
    for agent in args.agents:
        rs = [r for r in results if r['agent'] == agent]
        col = lambda k: mean([r[k] for r in rs if number(r.get(k))])  # noqa: E731
        print(f'{agent:20s} {col("ragas_faithfulness"):8.2f} {col("ragas_answer_accuracy"):8.2f} {col("ragas_tool_call_f1"):8.2f} {col("chart_ok"):8.2f} '
              f'{sum(r["status"] == "done" for r in rs):>2d}/{len(rs):<2d} {col("seconds"):6.1f} {col("consent_prompts"):7.2f}')
    print(f'\nSaved {out.relative_to(ROOT)}; scores and the dataset runs are in Langfuse ({LANGFUSE}).')


if __name__ == '__main__':
    main()
