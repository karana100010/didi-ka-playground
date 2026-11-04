"""Turns and runs: starting a question, driving its agent runs, and finishing them.

A turn is one question. It has one run per agent: position 0 answers the didi, later positions are the
compare view's runs, which go one after another. drive() is called by the event stream; it runs whatever
can run now and returns when a run waits for consent, the time budget is used up, or all runs are done.
"""
import re
import time

import agents
import memory
import settings
from agents import critic, pivot
from agents.context import Pause, RunContext
from connectors import ConnectorError
from core import store
from llm import nvidia

PREF_DEFAULTS = dict(model=nvidia.DEFAULT_MODEL, agent='plan_execute', compare_with='', compare_model='',
                     language_mode='direct', steps_view='hidden', speak=True, demo_cache=False, critic=True,
                     consent_voice=True, planner='llm', verify='code')
# consent_voice: consent cards are read aloud and answered by voice, without buttons.
# planner: who plans a Plan-and-Execute turn: 'llm' (the chat model) or 'laya' (the local Laya classifier).
# verify: the verification harness (server/verify): 'code' checks plans and answers in code; 'code+llm' also asks an
# LLM whether a writer's answer answers her question (one more call); 'off' skips it.
STEPS_VIEWS = ('hidden', 'collapsed', 'open')
HISTORY = 8  # earlier questions and answers sent as context

ERRORS_HI = {
    'not_configured': 'माफ़ कीजिए, अभी जवाब देने की व्यवस्था पूरी नहीं है।',
    'rate_limited': 'माफ़ कीजिए, अभी बहुत लोग पूछ रहे हैं। थोड़ी देर बाद फिर पूछिए।',
    'timeout': 'माफ़ कीजिए, जवाब आने में बहुत देर हो गई। फिर से पूछिए।',
}
OWN_SCRIPT = ('ta', 'te', 'kn', 'ml', 'bn', 'gu', 'pa', 'or', 'as', 'ur')  # not written in Devanagari
ERROR_HI = 'माफ़ कीजिए, अभी जवाब नहीं मिल पाया। फिर से कोशिश कीजिए।'


def clean_prefs(prefs):
    out = {**PREF_DEFAULTS, **{k: v for k, v in (prefs or {}).items() if k in PREF_DEFAULTS}}
    if out['agent'] not in agents.AGENTS:
        out['agent'] = PREF_DEFAULTS['agent']
    if out['compare_with'] and out['compare_with'] not in agents.AGENTS:
        out['compare_with'] = ''
    if out['steps_view'] not in STEPS_VIEWS:
        out['steps_view'] = 'hidden'
    if out['planner'] not in ('llm', 'laya'):
        out['planner'] = 'llm'
    if out['verify'] not in ('code', 'code+llm', 'off'):
        out['verify'] = 'code'
    if out['language_mode'] not in ('direct', 'pivot'):
        out['language_mode'] = 'direct'
    for flag in ('speak', 'demo_cache', 'critic', 'consent_voice'):
        out[flag] = bool(out[flag])
    for text in ('model', 'compare_model'):
        out[text] = str(out[text] or '')[:120]
    out['model'] = out['model'] or PREF_DEFAULTS['model']
    return out


def start_turn(workspace, session, question, mode):
    store.cleanup()
    prefs = session['prefs']
    specs = [(prefs['agent'], prefs['model'])]
    if prefs.get('compare_with'):
        specs.append((prefs['compare_with'], prefs.get('compare_model') or prefs['model']))
    turn, _ = store.add_turn(workspace, session['id'], question.strip(), mode, specs)
    return turn


def in_her_language(ctx, text, names):
    """Code words its answers (a decision, a projection, a fallback) in Hindi or English only. For a didi who speaks
    another language, answered directly, an English answer is translated to hers, and so is a Hindi one (a planner's
    follow-up) when her language has its own script; a Hindi "जी" after her name is dropped from an answer in another
    script."""
    if not text or ctx.llm_language in ('hi', 'en'):
        return text
    letters = [c for c in re.sub('|'.join(map(re.escape, filter(None, names))) or '^$', '', text) if c.isalpha()]
    source = None
    if letters and sum(c.isascii() for c in letters) / len(letters) > 0.5:
        source = 'en'
    elif ctx.llm_language in OWN_SCRIPT and letters and sum('\u0900' <= c <= '\u097F' for c in letters) / len(letters) > 0.5:
        source = 'hi'
    if source:
        try:
            text = pivot.translate(text, source, ctx.llm_language, names)
        except Exception:  # noqa: BLE001 - an untranslated answer is better than none
            return text
    if re.search(r'[\u0980-\u0DFF]', text):
        text = re.sub(r'\s*जी(?=[\s,।.!?]|$)', '', text)
    return text


def leaks_setup(text):
    from agents.writer import leaks_setup as leaks
    return leaks(text)


def history(workspace, session, before_turn_id):
    """Her earlier questions and answers, across logins (memory), before this turn."""
    try:
        entries = memory.recall(workspace, session['didi_id'], limit=HISTORY * 3)
    except Exception:  # noqa: BLE001 - a turn goes on without context rather than fail
        return []
    pairs = []
    for e in entries:
        if e['turn_id'] == before_turn_id:
            break
        if e.get('answer') and not leaks_setup(e['answer']):  # never feed setup text back in
            pairs.append((e['question'], e['answer']))
    return pairs[-HISTORY:]


def drive(workspace, turn_id, sink=None):
    """Run what can run now. Returns the turn's status: running | waiting | done | error | busy."""
    turn = store.get_turn(workspace, turn_id)
    session = store.get_session(workspace, turn['session_id'])
    past = history(workspace, session, turn_id)
    for run in store.turn_runs(turn_id):
        if run['status'] in ('done', 'error'):
            continue
        if run['status'] == 'waiting':
            if not _consent_decided(run['id']):
                store.set_turn(turn_id, status='waiting')
                return 'waiting'
            store.set_run(run['id'], status='queued')
        if not store.lease_run(run['id'], settings.RUN_BUDGET + 15):
            return 'busy'
        outcome = _run_once(workspace, run, turn, session, past, sink)
        if outcome in ('waiting', 'running'):
            store.set_turn(turn_id, status=outcome)
            return outcome
    runs = store.turn_runs(turn_id)
    main = runs[0]
    status = 'done' if main['status'] == 'done' else 'error'
    answer = (main['result'] or {}).get('answer')
    store.set_turn(turn_id, status=status, answer=answer)
    if status == 'done' and answer and turn['status'] != 'done':  # once, not on a replayed stream
        try:
            memory.remember(workspace, session, turn_id, turn['question'], answer, main['agent'])
        except Exception:  # noqa: BLE001 - memory never breaks a turn
            pass
    return status


def _consent_decided(run_id):
    return all(c['status'] != 'pending' for c in store.run_consents(run_id))


def _run_once(workspace, run, turn, session, past, sink):
    module = agents.AGENTS.get(run['agent'])
    ctx = RunContext(run, turn, session, workspace, past, sink)
    started = time.monotonic()
    try:
        if module is None:
            raise ConnectorError('unknown_agent', f'Unknown agent {run["agent"]}.', 500)
        # Emitted on every pass, like every event: the replay skips the ones already written by counting them.
        ctx.emit('run_started', agent=run['agent'], agent_name=module.NAME, model=run['model'], position=run['position'],
                 language_mode='pivot' if ctx.llm_language != ctx.language else 'direct', critic=bool(ctx.prefs.get('critic')))
        names = [ctx.didi.get('name'), ctx.didi.get('name_local'), *str(ctx.didi.get('name_local') or '').split()]
        if ctx.llm_language != ctx.language:
            original = ctx.question
            ctx.question = ctx.step('translate', {'text': original, 'to': 'en'},
                                    lambda: pivot.translate(original, ctx.language, 'en', names))
            ctx.emit('translated', direction='in', text=ctx.question)
        from agents.writer import degenerate, strip_english, tidy
        raw = in_her_language(ctx, module.run(ctx), names)
        answer = tidy(raw)
        if ctx.llm_language != 'en':
            answer = strip_english(answer)  # she hears only her language, never an added English translation
        if (raw and not answer) or degenerate(answer or '') or leaks_setup(answer or ''):
            ctx.emit('writer_rejected', reason='degenerate' if degenerate(answer or '') else 'setup_text', sample=(raw or '')[:200])
            answer = ERROR_HI if ctx.llm_language == 'hi' else 'Sorry, no answer right now. Please ask again.'
        if ctx.prefs.get('critic'):
            answer = critic.check(ctx, answer)
        if ctx.prefs.get('verify', 'code') != 'off':
            from verify import answer as gate_c
            answer = gate_c.deliver(ctx, answer)
        answer = in_her_language(ctx, answer, names)  # a code fallback may have replaced it
        if ctx.llm_language != ctx.language:
            english = answer
            answer = ctx.step('translate', {'text': english, 'to': ctx.language},
                              lambda: pivot.translate(english, 'en', ctx.language, names))
            ctx.emit('translated', direction='out', english=english)
    except Pause as pause:
        store.set_run(run['id'], status='waiting' if pause.reason == 'consent' else 'queued', lease_until=None)
        ctx.tracer.flush()
        return 'waiting' if pause.reason == 'consent' else 'running'
    except (nvidia.LlmError, ConnectorError) as error:
        _fail(ctx, run, error.code, error.message)
        ctx.tracer.flush()
        return 'error'
    except Exception as error:  # noqa: BLE001 - one bad run must not break the stream; the trace shows why
        _fail(ctx, run, 'internal', f'{type(error).__name__}: {error}')
        ctx.tracer.flush()
        return 'error'
    ctx.metrics['run_ms'] = int((time.monotonic() - started) * 1000)
    ctx.metrics['fields_read'] = sorted(set(ctx.metrics['fields_read']))
    result = {'answer': answer, 'metrics': ctx.metrics, 'evidence': ctx.evidence}
    ctx.emit('answer', text=answer, language=ctx.language, evidence=ctx.evidence, position=run['position'])
    _discard(ctx, run)
    ctx.emit('metrics', **{**ctx.metrics, 'fields_read': len(ctx.metrics['fields_read'])})
    store.set_run(run['id'], status='done', result=result, lease_until=None)
    ctx.tracer.flush()
    import judge
    judge.start(ctx, answer)  # NVIDIA judge scores on the Langfuse trace; only when tracing is on
    return 'done'


def _discard(ctx, run):
    """Fetched values live only in memory and the run's checkpoint; both go now."""
    store.purge_steps(run['id'])
    for consent in store.run_consents(run['id']):
        if consent['status'] in ('granted', 'partial') and not consent['discarded_at']:
            store.set_consent(consent['id'], discarded_at=store.now())
    ctx.fetched.clear()
    if ctx.evidence:
        ctx.emit('discarded', refs=[e['ref'] for e in ctx.evidence])


def _fail(ctx, run, code, message):
    ctx.emit('error', code=code, message=message, hi=ERRORS_HI.get(code, ERROR_HI), position=run['position'])
    store.purge_steps(run['id'])
    store.set_run(run['id'], status='error', error={'code': code, 'message': message}, lease_until=None)
