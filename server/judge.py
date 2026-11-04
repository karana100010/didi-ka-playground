"""A judge model (Claude Opus 5.5 by default): after each run (when Langfuse tracing is on), score the answer and attach the scores
to the run's Langfuse trace, so agents and models can be compared on quality as well as cost.

The judge sees what the didi asked, the earlier turns, the exact totals of the records the run read, which fields
it read, and the answer. It scores four things from 0 to 1 and gives one reason, which Langfuse shows beside
each score. It runs on a background thread after the answer is sent, never slows or fails a run, and uses a
different, stronger model than the writer (JUDGE_MODEL, default Claude Opus 5.5 through the logged-in Claude CLI).
"""
import json
import os
import threading
import time

import requests

import tracing

JUDGE_MODEL = os.environ.get('JUDGE_MODEL', 'claude-cli/claude-opus-5-5')  # Opus 5.5 via the logged-in Claude CLI
CRITERIA = {
    'judge_grounded': 'Every number and fact about her comes from the records below (or, for a general question, is correct about the scheme). 0 if any figure is invented or wrong.',
    'judge_answers_question': 'It answers what she asked, or asks one short, sensible follow-up when her question is too vague.',
    'judge_hindi_only': 'It is in simple Hindi (Devanagari) for her to hear, with no English sentences or translations, lists or markdown.',
    'judge_minimal_data': 'It read only the records needed for this question (judge from the fields read).',
}
PROMPT = """You judge an assistant that answers rural women (SHG members, "didis") by voice in Hindi, reading her
government records only with her consent. Score the answer on each criterion from 0 to 1 (use 0, 0.5 or 1).

Criteria:
%s

Reply with JSON only: {"judge_grounded": n, "judge_answers_question": n, "judge_hindi_only": n, "judge_minimal_data": n,
"reason": "<one or two sentences in English: the main problem, or why it is good>"}"""


def start(ctx, answer):
    """Queue a judgement of this run's answer; a no-op unless tracing to Langfuse is on."""
    if not tracing.enabled():
        return
    case = {'question': ctx.turn['question'], 'earlier_turns': [{'didi': q, 'assistant': a} for q, a in ctx.history[-3:]],
            'records_read': ctx.summaries, 'fields_read': sorted(set(ctx.metrics['fields_read'])),
            'answer_worded_by_code': ctx.worded_by_code, 'answer': answer}
    _spawn(_judge, tracing.trace_id(ctx.run['id']), case)


def _spawn(target, *args):
    threading.Thread(target=target, args=args, daemon=True).start()


def _judge(trace_id, case):
    from llm import nvidia
    criteria = '\n'.join(f'- {k}: {v}' for k, v in CRITERIA.items())
    messages = [{'role': 'system', 'content': PROMPT % criteria},
                {'role': 'user', 'content': json.dumps(case, ensure_ascii=False, default=str)}]
    try:
        reply = nvidia.chat(JUDGE_MODEL, messages, temperature=0, max_tokens=300, deadline=time.monotonic() + 55)
        text = reply['message']['content'] or ''
        verdict = json.loads(text[text.index('{'):text.rindex('}') + 1])
    except (nvidia.LlmError, ValueError):
        return  # judging is best effort
    public, secret, url = tracing._config()
    reason = str(verdict.get('reason') or '')[:500]
    for name in CRITERIA:
        value = verdict.get(name)
        if isinstance(value, (int, float)):
            try:
                requests.post(f'{url}/api/public/scores', auth=(public, secret), timeout=10,
                              json={'traceId': trace_id, 'name': name, 'value': float(value), 'dataType': 'NUMERIC',
                                    'comment': f'{JUDGE_MODEL}: {reason}'})
            except requests.RequestException:
                return
