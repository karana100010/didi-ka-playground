import { LANGUAGES, api, authHeaders, errorNotice, h, requestAccessCode, session, toast, topBar, workspace } from '/api.js';
import { createBhashini } from '/bhashini.js';
// History helpers go through the namespace so a stale cached api.js cannot break the page with a missing export.
import * as apiModule from '/api.js';

// What the didi sees and hears, per language. The operator panel stays in English.
const TEXT = {
    hi: {
        audioFailed: 'आवाज़ नहीं चल पाई। जवाब पढ़िए या सुनें दबाइए।', speak: 'बात शुरू करें', stop: 'रोकें', listening: 'सुन रहे हैं… बोलिए', transcribing: 'समझ रहे हैं…', thinking: 'जवाब तैयार हो रहा है…', speaking: 'जवाब सुनिए… माइक रुका हुआ है',
        placeholder: 'या यहाँ लिखिए…', listen: '▶ सुनें', spoke: 'बोला गया', typed: 'लिखा गया', again: 'फिर से बोलें',
        nothing: 'कुछ सुनाई नहीं दिया। फिर से बोलिए।', micDenied: 'माइक चालू नहीं हो पाया। ब्राउज़र में माइक की अनुमति दीजिए।',
        asrFailed: 'आवाज़ समझ नहीं आई। फिर से बोलिए या लिखिए।', error: 'माफ़ कीजिए, अभी जवाब नहीं मिल पाया। फिर से कोशिश कीजिए।',
        greet: name => `नमस्ते ${name} जी! अपना सवाल बोलिए या लिखिए।`, steps: n => `यह जवाब कैसे बना · ${n} कदम`,
        consentTitle: 'अनुमति चाहिए', yes: 'हाँ', no: 'नहीं', some: 'कुछ ही दिखाइए', showThese: 'ये दिखाइए', confirm: 'हाँ, सही है',
        reject: 'नहीं', orSpeak: 'बात चालू है तो अपने शब्दों में बोलिए; वरना माइक दबाइए', speakNow: 'सुन रहे हैं… हाँ या नहीं, अपने शब्दों में बोलिए',
        tapToAnswer: 'जवाब देने के लिए माइक दबाइए', why: 'किसलिए:', pickSome: 'जो दिखाना है, उसे चुनिए:',
        stepAsked: 'आपकी अनुमति माँगी', stepGranted: id => `आपकी अनुमति ली (${id})`, stepDenied: 'आपने अनुमति नहीं दी',
        stepSkipped: 'पहले मना की गई जानकारी नहीं माँगी', stepRead: r => `${r} से जानकारी देखी`, stepBlocked: 'एक रजिस्टर नहीं देख पाए',
        stepCalc: 'हिसाब लगाया', stepScheme: 'योजना की जानकारी देखी', stepDiscard: 'आपकी जानकारी हटा दी',
        planFile: 'बिज़नेस प्लान (Excel)', download: 'डाउनलोड करें', stepPlan: 'बिज़नेस प्लान बनाया',
        stepChart: 'बचत का अनुमान लगाया', after: m => `${m} महीने बाद`, more: v => `${v} ज़्यादा जमा`, less: v => `${v} कम जमा`, target: 'लक्ष्य',
    },
    ta: {
        audioFailed: 'ஒலி இயங்கவில்லை. பதிலைப் படியுங்கள் அல்லது கேளுங்கள் என்பதை அழுத்துங்கள்.', speak: 'பேசத் தொடங்குங்கள்', stop: 'நிறுத்து', listening: 'கேட்கிறோம்… பேசுங்கள்', transcribing: 'புரிந்துகொள்கிறோம்…', thinking: 'பதில் தயாராகிறது…', speaking: 'பதிலைக் கேளுங்கள்… மைக் இடைநிறுத்தப்பட்டுள்ளது',
        placeholder: 'அல்லது இங்கே எழுதுங்கள்…', listen: '▶ கேளுங்கள்', spoke: 'பேசியது', typed: 'எழுதியது', again: 'மீண்டும் பேசுங்கள்',
        nothing: 'எதுவும் கேட்கவில்லை. மீண்டும் பேசுங்கள்.', micDenied: 'மைக் இயங்கவில்லை. உலாவியில் மைக் அனுமதி கொடுங்கள்.',
        asrFailed: 'குரல் புரியவில்லை. மீண்டும் பேசுங்கள் அல்லது எழுதுங்கள்.', error: 'மன்னிக்கவும், இப்போது பதில் கிடைக்கவில்லை. மீண்டும் முயலுங்கள்.',
        greet: name => `வணக்கம் ${name}! உங்கள் கேள்வியைப் பேசுங்கள் அல்லது எழுதுங்கள்.`, steps: n => `இந்தப் பதில் எப்படி உருவானது · ${n} படிகள்`,
        consentTitle: 'அனுமதி தேவை', yes: 'ஆம்', no: 'இல்லை', some: 'சிலவற்றை மட்டும்', showThese: 'இவற்றைக் காட்டுங்கள்',
        confirm: 'ஆம், சரி', reject: 'இல்லை', orSpeak: 'அல்லது மைக்கை அழுத்தி உங்கள் வார்த்தைகளில் சொல்லுங்கள்', why: 'எதற்காக:',
        speakNow: 'கேட்கிறோம்… உங்கள் வார்த்தைகளில் பதில் சொல்லுங்கள்', tapToAnswer: 'பதில் சொல்ல மைக்கை அழுத்துங்கள்',
        pickSome: 'காட்ட வேண்டியதைத் தேர்ந்தெடுங்கள்:', stepAsked: 'உங்கள் அனுமதி கேட்கப்பட்டது', stepGranted: id => `உங்கள் அனுமதி பெறப்பட்டது (${id})`,
        stepDenied: 'நீங்கள் அனுமதி தரவில்லை', stepSkipped: 'முன்பு மறுத்த தகவல் கேட்கப்படவில்லை', stepRead: r => `${r} பார்க்கப்பட்டது`,
        stepBlocked: 'ஒரு பதிவேட்டைப் பார்க்க முடியவில்லை', stepCalc: 'கணக்கிடப்பட்டது', stepScheme: 'திட்டத் தகவல் பார்க்கப்பட்டது',
        stepDiscard: 'உங்கள் தகவல் நீக்கப்பட்டது', planFile: 'வணிகத் திட்டம் (Excel)', download: 'பதிவிறக்கு', stepPlan: 'வணிகத் திட்டம் உருவாக்கப்பட்டது',
        stepChart: 'சேமிப்பு கணிக்கப்பட்டது', after: m => `${m} மாதங்களுக்குப் பிறகு`, more: v => `${v} கூடுதல் சேமிப்பு`, less: v => `${v} குறைவான சேமிப்பு`, target: 'இலக்கு',
    },
    en: {
        audioFailed: 'Audio could not play. Read the reply or tap Listen.', speak: 'Start conversation', stop: 'Stop', listening: 'Listening… speak now', transcribing: 'Understanding…', thinking: 'Preparing the answer…', speaking: 'Listen to the reply… microphone paused',
        placeholder: 'Or type here…', listen: '▶ Listen', spoke: 'spoken', typed: 'typed', again: 'Speak again',
        nothing: 'Nothing was heard. Please speak again.', micDenied: 'The microphone did not start. Allow it in the browser.',
        asrFailed: 'Could not understand the voice. Speak again or type.', error: 'Sorry, no answer right now. Please try again.',
        greet: name => `Hello ${name}! Ask your question by voice or text.`, steps: n => `How this answer was made · ${n} steps`,
        consentTitle: 'Permission needed', yes: 'Yes', no: 'No', some: 'Only some', showThese: 'Show these', confirm: 'Yes, that is right',
        reject: 'No', orSpeak: 'Or press the mic and answer in your own words', speakNow: 'Listening… answer in your own words',
        tapToAnswer: 'Press the mic to answer', why: 'Why:', pickSome: 'Choose what may be seen:',
        stepAsked: 'Asked for your permission', stepGranted: id => `Took your permission (${id})`, stepDenied: 'You did not give permission',
        stepSkipped: 'Did not ask again for what you refused', stepRead: r => `Looked at your ${r}`, stepBlocked: 'Could not read one register',
        stepCalc: 'Did the arithmetic', stepScheme: 'Looked up scheme facts', stepDiscard: 'Deleted your information',
        planFile: 'Business plan (Excel)', download: 'Download', stepPlan: 'Built your business plan',
        stepChart: 'Projected your savings', after: m => `After ${m} months`, more: v => `${v} more saved`, less: v => `${v} less saved`, target: 'Target',
    },
};

const status = await topBar('/chat');
const bhashini = createBhashini({ headers: () => authHeaders() });
const els = Object.fromEntries(['msgs', 'voice-status', 'mic', 'mic-label', 'text-form', 'text', 'panel', 'panel-toggle', 'panel-close',
    'model', 'probe', 'probe-badge', 'agent', 'compare', 'compare-model', 'speak', 'demo-cache', 'steps-view', 'sources',
    'trace', 'compare-box', 'compare-runs', 'key-notice', 'who', 'lang-chip', 'mode-direct', 'mode-direct-label', 'mode-pivot',
    'critic', 'compare-open', 'compare-dialog', 'compare-cols', 'compare-close', 'compare-question'].map(id => [id, document.getElementById(id)]));

let state = { session: null, didi: null, prefs: null, t: TEXT.hi, models: null, busy: false, mic: 'idle', recorder: null,
    cards: {}, pending: null, waiter: null, voiceActive: false, voiceEpoch: 0, speechEpoch: 0, resumeTimer: null, consentBusy: false };

// --- start ---------------------------------------------------------------------------------

// topBar has its own service status; keep conversation feedback in the composer.
els.status = els['voice-status'];
const sessionId = session.get();
const adminDidi = new URLSearchParams(location.search).get('admin');
if (adminDidi) {
    // An admin viewing a didi: drawn by the admin view section at the end, once the chart helpers are defined.
} else if (!sessionId) {
    els.msgs.append(h('p', { class: 'notice' }, 'Nobody is logged in. ', h('a', { href: '/connect#login-step' }, 'Log in as a didi')));
    els.mic.disabled = true;
} else {
    try {
        const data = await api(`/v1/sessions/${sessionId}`);
        state = { ...state, session: data, didi: data.didi, prefs: data.prefs, t: TEXT[data.didi.language] || TEXT.en };
        renderHeader();
        greet();
        for (const turn of data.turns) renderPastTurn(turn);
        renderPanel();
        const last = data.turns.at(-1);
        if (last && (last.status === 'running' || last.status === 'waiting')) runTurn(last.id);
    } catch (error) {
        els.msgs.append(errorNotice(error), h('p', {}, h('a', { href: '/connect#login-step' }, 'Log in again')));
        els.mic.disabled = true;
    }
}

function firstName(didi) { return String(didi.name_local || didi.name || '').split(' ')[0]; }

function renderHeader() {
    const d = state.didi;
    els.who.replaceChildren(
        h('span', { class: 'avatar hi', 'aria-hidden': 'true' }, String(d.name_local || d.name).charAt(0)),
        h('div', {}, h('b', { class: 'hi' }, d.name_local || d.name),
            h('small', {}, [d.name, d.shg_name, d.district].filter(Boolean).join(' · '))));
    const speaks = d.asr_language && d.asr_language !== d.language ? ` · ASR ${d.asr_language}` : '';
    els['lang-chip'].textContent = (LANGUAGES[d.language] || d.language) + speaks;
    els['lang-chip'].hidden = false;
    els['mic-label'].textContent = state.t.speak;
    els.text.placeholder = state.t.placeholder;
    document.documentElement.lang = d.language || 'hi';
}

function greet() {
    const text = state.t.greet(firstName(state.didi));
    addBubble('bot', text);
    if (!state.prefs.speak && !state.prefs.consent_voice) return;
    // Browsers may block sound until she touches the page: try now, else play on her first tap or key press.
    speak(text).then(played => {
        if (played) return;
        const later = event => {
            if (state.greeted || state.voiceActive || state.lastQuestion || event.target.closest?.('#mic, #text-form, .panel, .history, #history-toggle')) return;  // her own action comes first
            speak(text);
        };
        document.addEventListener('pointerdown', later, { once: true, capture: true });
        document.addEventListener('keydown', later, { once: true, capture: true });
    });
}

// --- messages ------------------------------------------------------------------------------

function scrollDown() { els.msgs.scrollTop = els.msgs.scrollHeight; }

function addBubble(who, text, { mode, speakable = true, error = false } = {}) {
    const meta = h('span', { class: 'meta' });
    const node = h('div', { class: `msg ${who === 'me' ? 'me' : ''} ${error ? 'error' : ''}` }, h('div', { class: 'bubble hi' }, text), meta);
    if (who === 'me' && mode) {
        meta.append(mode === 'voice' ? state.t.spoke : state.t.typed);
        if (mode === 'voice') meta.append('·', h('button', { class: 'linklike', type: 'button', onclick: () => { if (!state.voiceActive) els.mic.click(); } }, state.t.again));
    }
    if (who === 'bot' && speakable && !error) {
        meta.append(h('button', { class: 'linklike', type: 'button', onclick: () => speak(text) }, state.t.listen));
    }
    els.msgs.append(node);
    scrollDown();
    return node;
}

function renderPastTurn(turn) {
    addBubble('me', turn.question, { mode: turn.mode });
    if (turn.answer) addBubble('bot', turn.answer);
    else if (turn.status === 'error') addBubble('bot', state.t.error, { error: true });
}

function setStatus(text, spinning = false) {
    els.status.replaceChildren(...(text ? [spinning ? h('span', { class: 'spinner', 'aria-hidden': 'true' }) : null, h('span', { class: 'hi' }, text)] : [])
        .filter(Boolean));  // replaceChildren would print a null as the word "null"
}

// --- asking --------------------------------------------------------------------------------

els['text-form'].addEventListener('submit', event => {
    event.preventDefault();
    const text = els.text.value.trim();
    if (!text) return;
    if (state.pending) { els.text.value = ''; answerInWords(text, 'text'); return; }
    if (state.busy) return;
    els.text.value = '';
    send(text, 'text');
});

// While a consent card is open, her words (spoken or typed) answer the card, not a new question.
function answerInWords(text, mode) {
    const card = state.pending;
    addBubble('me', text, { mode });
    return answerConsent(card, { text, mode });
}

async function send(text, mode) {
    if (state.busy || !state.session) return;
    state.busy = true;
    pauseListening();
    setBusy(true);
    state.lastQuestion = text;
    state.lastMode = mode;
    stopSpeaking();
    addBubble('me', text, { mode });
    let turn;
    try {
        turn = await api(`/v1/sessions/${state.session.id}/turns`, { method: 'POST', body: { text, mode } });
    } catch (error) {
        addBubble('bot', state.t.error, { error: true });
        traceAdd('error', error.message, 'e');
        state.busy = false;
        setBusy(false);
        resumeListening();
        return;
    }
    await runTurn(turn.id);
}

async function runTurn(turnId) {
    state.busy = true;
    setBusy(true);
    setStatus(state.t.thinking, true);
    resetTrace();
    const view = { positions: {}, steps: {}, cards: {}, answered: false, runs: {}, files: {}, charts: {}, wallets: {} };
    state.lastView = view;
    try {
        let after = 0;
        for (let attempt = 0; attempt < 30; attempt++) {
            const end = await openStream(turnId, after, ev => { if (ev.id) after = Math.max(after, ev.id); onEvent(ev, view); });
            if (!end) {  // the stream broke: read stored events instead, then try streaming again
                const polled = await api(`/v1/turns/${turnId}/events?after=${after}`);
                for (const ev of polled.events) { after = Math.max(after, ev.id); onEvent(ev, view); }
                if (polled.status === 'done' || polled.status === 'error') break;
                await new Promise(r => setTimeout(r, 1000));
                continue;
            }
            if (end.status === 'running') continue;                 // time budget used: continue in a new call
            if (end.status === 'waiting') { await waitForConsent(turnId); continue; }
            break;
        }
    } catch (error) {
        traceAdd('error', error.message, 'e');
        if (!view.answered) addBubble('bot', state.t.error, { error: true });
    } finally {
        state.busy = false;
        setBusy(false);
        if (state.mic !== 'speaking') setStatus('');
        resumeListening();
    }
}

async function openStream(turnId, after, onEventFn) {
    let response;
    try {
        response = await fetch(`/v1/turns/${turnId}/stream?after=${after}`, { headers: { ...authHeaders(), 'X-Workspace': workspace() } });
    } catch { return null; }
    if (response.status === 401) { await requestAccessCode(); return null; }
    if (!response.ok || !response.body) return null;
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = '', end = null;
    try {
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += value;
            let cut;
            while ((cut = buffer.indexOf('\n\n')) >= 0) {
                const frame = buffer.slice(0, cut);
                buffer = buffer.slice(cut + 2);
                let type = 'message', data = '';
                for (const line of frame.split('\n')) {
                    if (line.startsWith('event: ')) type = line.slice(7);
                    else if (line.startsWith('data: ')) data += line.slice(6);
                }
                if (!data) continue;
                const parsed = JSON.parse(data);
                if (type === 'end') end = parsed;
                else onEventFn(parsed);
            }
        }
    } catch { return end; }
    return end;
}

// The run is waiting for her answer to a consent card: resolve when she has given a final answer.
function waitForConsent() {
    if (!Object.values(state.cards).some(c => c.status === 'pending')) return new Promise(r => setTimeout(r, 800));
    if (state.mic !== 'speaking') setStatus('');
    setBusy(false);  // she answers with the buttons, the mic or the text box
    return new Promise(resolve => { state.waiter = () => { setBusy(true); resolve(); }; });
}

function onEvent(ev, view) {
    if (ev.type === 'llm_detail') { llmDetail(ev); return; }  // fills the matching llm row in the trace
    const d = ev.data || {};
    if (ev.type === 'run_started') view.positions[ev.run_id] = d.position;
    const position = d.position ?? view.positions[ev.run_id] ?? 0;
    const run = view.runs[ev.run_id] ||= { position };
    if (ev.type === 'run_started') Object.assign(run, { agent: d.agent_name, model: d.model, mode: d.language_mode, critic: d.critic });
    if (ev.type === 'answer') run.answer = d.text;
    if (ev.type === 'error') run.answer = `${d.code}: ${d.message}`;
    if (ev.type === 'metrics') run.metrics = d;
    traceEvent(ev, position);
    if (ev.type === 'consent_request') { consentCard(d, position); return; }   // she answers compare runs' requests too
    if (ev.type === 'consent_result') markCard(d.consent_id, d.decision, d.summary);
    if (position > 0) { compareEvent(ev, view, position); return; }
    const step = didiStep(ev);
    if (step) (view.steps[ev.run_id] ||= []).push(step);
    switch (ev.type) {
        case 'status':
            setStatus(d.hi || d.en, true);
            break;
        case 'file':
            (view.files[ev.run_id] ||= []).push(d);
            break;
        case 'chart':
            (view.charts[ev.run_id] ||= []).push(d);
            break;
        case 'wallet':
            (view.wallets[ev.run_id] ||= []).push(d);
            break;
        case 'answer':
            view.answered = true;
            setStatus('');
            addAnswer(d.text, view.steps[ev.run_id] || [], d.evidence || []);
            for (const file of view.files[ev.run_id] || []) addFileCard(file);
            for (const chart of view.charts[ev.run_id] || []) addChartCard(chart);
            for (const wallet of view.wallets[ev.run_id] || []) addWalletCard(wallet);
            if (state.prefs.speak || state.voiceActive || state.lastMode === 'voice') speak(d.text);
            refreshConsents();
            break;
        case 'error':
            view.answered = true;
            addBubble('bot', d.hi || state.t.error, { error: true });
            break;
        default:
    }
}

function lang() { return state.didi.language === 'hi' ? 'hi' : 'en'; }

function addAnswer(text, steps, evidence) {
    const node = addBubble('bot', text);
    if (evidence.length) {
        const chips = h('div', { class: 'evidence hi' }, ...evidence.map(e => h('span', { class: 'chip' }, `${e.register[lang()]} · ${e.rows}`)),
            ...[...new Set(evidence.map(e => e.consent_id))].map(id => h('span', { class: 'chip mono' }, id)));
        node.insertBefore(chips, node.lastChild);
        steps = [...steps, state.t.stepDiscard];
    }
    const show = state.prefs.steps_view;
    if (show !== 'hidden' && steps.length) {
        node.append(h('details', { class: 'steps hi', open: show === 'open' }, h('summary', {}, state.t.steps(steps.length)),
            h('ol', {}, ...steps.map(s => h('li', {}, s)))));
    }
    scrollDown();
}

// The didi's version of a step: plain words, no model names, prompts or values.
function didiStep(ev) {
    const d = ev.data || {}, t = state.t;
    if (ev.type === 'consent_request') return t.stepAsked;
    if (ev.type === 'consent_result') return d.decision === 'denied' ? t.stepDenied : t.stepGranted(d.consent_id);
    if (ev.type === 'consent_skipped') return t.stepSkipped;
    if (ev.type === 'tool_call' && d.tool === 'fetch_records') return d.blocked ? t.stepBlocked : t.stepRead(d.register?.[lang()] || '');
    if (ev.type === 'tool_call' && d.tool === 'calculate') return t.stepCalc;
    if (ev.type === 'tool_call' && d.tool === 'scheme_info') return t.stepScheme;
    if (ev.type === 'decision') return d.step?.[lang()] || null;
    if (ev.type === 'file') return t.stepPlan;
    if (ev.type === 'chart') return t.stepChart;
    if (ev.type === 'tool_call' && WALLET_STEPS[d.tool]) return WALLET_STEPS[d.tool][lang()];
    return null;
}

function addFileCard(file) {
    const button = h('button', { class: 'btn primary', type: 'button' },
        h('svg', { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'aria-hidden': 'true' }),
        state.t.download);
    button.firstChild.innerHTML = '<path d="M12 4v11M7 10l5 5 5-5M5 20h14" fill="none" stroke-linecap="round" stroke-linejoin="round"/>';
    button.addEventListener('click', () => download(file, button));
    els.msgs.append(h('div', { class: 'file-card' },
        h('div', {}, h('b', { class: 'hi' }, state.t.planFile), h('div', { class: 'small muted mono' }, file.name),
            h('div', { class: 'small muted' }, 'Formulas stay live · mock JEEViKA unit costs')), button));
    scrollDown();
}

async function download(file, button) {
    button.disabled = true;
    try {
        const response = await fetch(`/v1/files/${file.file_id}`, { headers: { ...authHeaders(), 'X-Workspace': workspace() } });
        if (!response.ok) throw new Error(`Download failed (${response.status}).`);
        const link = h('a', { href: URL.createObjectURL(await response.blob()), download: file.name });
        link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 5000);
    } catch (error) { toast(error.message, 4000); } finally { button.disabled = false; }
}

// --- wallet card -------------------------------------------------------------------------
// Her credentials (she is the holder and sees every claim of her own), or what was just sent and what stayed hidden.

const WALLET_STEPS = {
    wallet_list: { hi: 'आपका वॉलेट खोला', en: 'Opened your wallet' },
    issue_credential: { hi: 'प्रमाणपत्र पर हस्ताक्षर करके वॉलेट में रखा', en: 'Signed a credential into your wallet' },
    share_credential: { hi: 'सिर्फ़ चुनी हुई बातें भेजीं', en: 'Sent only the chosen claims' },
    find_business_plan: { hi: 'आपका बिज़नेस प्लान ढूँढा', en: 'Found your business plan' },
};
const WALLET_TEXT = {
    hi: { title: 'मेरा वॉलेट', sent: to => `${to} को भेजा`, seen: 'दिखेगा:', hidden: 'छिपा रहेगा:', open: 'सत्यापन पेज खोलें',
        expired: 'समय-सीमा खत्म', until: d => `${d} तक मान्य`, signed: 'डिजिटल हस्ताक्षर', issued: 'वॉलेट में रखा गया' },
    en: { title: 'My wallet', sent: to => `Sent to the ${to}`, seen: 'They see:', hidden: 'Stays hidden:', open: 'Open the verifier page',
        expired: 'expired', until: d => `valid until ${d}`, signed: 'digitally signed', issued: 'Saved to your wallet' },
};

function walletValue(value) {
    if (value === true) return '✓';
    if (Array.isArray(value)) return value.join(', ');
    if (typeof value === 'number') return rupeesOrNumber(value);
    const text = String(value);
    return text.length > 24 && /^[0-9a-f]+$/.test(text) ? `${text.slice(0, 12)}…` : text;
}

function rupeesOrNumber(value) { return Number(value).toLocaleString('en-IN'); }

function addWalletCard(d) {
    const l = lang(), w = WALLET_TEXT[l];
    if (d.action === 'shared') {
        const s = d.share;
        els.msgs.append(h('div', { class: 'wallet-card hi' },
            h('b', {}, `${s.title[l]} · ${w.sent(s.verifier_name[l])}`),
            h('div', { class: 'small muted' }, w.seen),
            h('div', { class: 'sent' }, ...s.disclosed.map(c => h('span', { class: 'chip ok' }, h('span', { class: 'dot' }), c.label[l]))),
            s.withheld.length ? h('div', { class: 'small muted' }, w.hidden) : null,
            s.withheld.length ? h('div', { class: 'sent' }, ...s.withheld.map(c => h('span', { class: 'chip off' }, c.label[l]))) : null,
            h('div', { class: 'row' }, h('a', { class: 'btn small', href: s.verify_url, target: '_blank', rel: 'noopener' }, w.open),
                h('span', { class: 'small muted mono' }, s.consent_id))));
        scrollDown();
        return;
    }
    const creds = d.credentials || [];
    const receipts = creds.filter(c => c.kind === 'consent_receipt');
    const cred = c => h('details', { class: 'cred' },
        h('summary', {}, h('span', {}, h('b', {}, c.title[l]), h('span', { class: 'small muted' }, ` · ${c.issuer}`)),
            h('span', { class: `badge ${c.state === 'active' ? 'ok' : 'warn'}` },
                c.state === 'active' ? (c.valid_until ? w.until(c.valid_until) : w.signed) : w.expired)),
        h('dl', {}, ...c.claims.flatMap(x => [h('dt', {}, x.label[l]), h('dd', {}, walletValue(x.value))])));
    els.msgs.append(h('div', { class: 'wallet-card hi' },
        h('b', {}, d.action === 'issued' ? w.issued : `${w.title} · ${creds.length}`),
        h('ul', { class: 'creds' }, ...creds.filter(c => c.kind !== 'consent_receipt').map(c => h('li', {}, cred(c))),
            receipts.length ? h('li', {}, h('details', { class: 'cred' },
                h('summary', {}, h('b', {}, `${receipts[0].title[l]} · ${receipts.length}`), h('span', { class: 'badge ok' }, w.signed)),
                h('ul', { class: 'creds' }, ...receipts.map(c => h('li', {}, cred(c)))))) : null)));
    scrollDown();
}

// --- savings chart -------------------------------------------------------------------------
// The server computes every figure; this only draws them. Pure helpers first, so tests can run them without a DOM.

const MONTHS = {
    hi: ['जन', 'फ़र', 'मार्च', 'अप्रै', 'मई', 'जून', 'जुला', 'अग', 'सित', 'अक्टू', 'नव', 'दिस'],
    en: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
};
const CHART = { width: 340, height: 210, top: 14, bottom: 26 };

// ₹ with Indian grouping: 124800 → ₹1,24,800; negatives keep their sign.
function rupees(value) {
    const n = Math.round(Math.abs(Number(value) || 0)), digits = String(n);
    const head = digits.slice(0, -3), tail = digits.slice(-3);
    return `${value < 0 && n ? '-' : ''}₹${head ? `${head.replace(/\B(?=(\d{2})+$)/g, ',')},` : ''}${tail}`;
}

function monthLabel(ym, language) {
    const [year, month] = String(ym).split('-').map(Number);
    const names = MONTHS[language] || MONTHS.en;
    return month >= 1 && month <= 12 ? `${names[month - 1]} ${year}` : String(ym);
}

// A tick step of 1, 2, 2.5 or 5 × 10^k, about `count` steps over the range.
function niceStep(range, count = 3) {
    const raw = range / count || 1, power = 10 ** Math.floor(Math.log10(raw));
    return [1, 2, 2.5, 5, 10].map(f => f * power).find(step => step >= raw);
}

// Indices of the x labels to print: every `step` months from the first, the smallest calendar-like step
// that keeps labels `gap` pixels apart when months are `dx` pixels apart.
function labelIndices(n, dx, gap = 64) {
    const step = [1, 2, 3, 4, 6, 12, 18, 24].find(s => s * dx >= gap) || Math.ceil(gap / dx);
    return [...Array(n).keys()].filter(i => i % step === 0);
}

function chartGeometry(spec, size = CHART) {
    const x = spec.x || [], series = spec.series || [];
    const values = series.flatMap(s => s.values).filter(Number.isFinite);
    let min = Math.min(...values), max = Math.max(...values);
    if (min === max) { min -= Math.max(1, Math.abs(min) * 0.1); max += Math.max(1, Math.abs(max) * 0.1); }
    const step = niceStep(max - min);
    // A small dip below zero (start-up money not yet earned back) gets a floor just under it, not a whole step down.
    const floor = min < 0 && max > 0 ? niceStep(-min, 1) : step;
    const lo = Math.floor(min / floor) * floor, hi = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = Math.ceil(lo / step) * step || 0; v <= hi + step / 2; v += step) ticks.push(v);  // || 0: never -0
    const left = Math.max(...ticks.map(t => rupees(t).length)) * 6.4 + 10;
    const right = Math.max(...series.map(s => rupees(s.values.at(-1)).length)) * 7.2 + 14;  // gutter for the last values
    const plot = { left, right: size.width - right, top: size.top, bottom: size.height - size.bottom };
    const xAt = i => x.length < 2 ? (plot.left + plot.right) / 2 : plot.left + i * (plot.right - plot.left) / (x.length - 1);
    const yAt = v => plot.bottom - (v - lo) * (plot.bottom - plot.top) / (hi - lo);
    return { plot, ticks, xAt, yAt, lo, hi, points: series.map(s => s.values.map((v, i) => [xAt(i), yAt(v)])) };
}

function escapeXml(text) {
    return String(text).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function chartSvg(spec, language = 'hi', size = CHART) {
    const g = chartGeometry(spec, size), { plot } = g, x = spec.x || [], series = spec.series || [];
    const r = n => Math.round(n * 10) / 10;
    const grid = g.ticks.map(t => `<line class="grid" x1="${r(plot.left)}" x2="${plot.right}" y1="${r(g.yAt(t))}" y2="${r(g.yAt(t))}"/>`
        + `<text class="ylab" x="${r(plot.left - 6)}" y="${r(g.yAt(t) + 4)}" text-anchor="end">${rupees(t)}</text>`).join('');
    const dx = x.length > 1 ? (plot.right - plot.left) / (x.length - 1) : Infinity;
    const xLabels = labelIndices(x.length, dx).map(i =>
        `<text class="xlab" x="${r(g.xAt(i))}" y="${size.height - 8}" text-anchor="middle">${escapeXml(monthLabel(x[i], language))}</text>`).join('');
    // Last values sit in the right gutter beside each line's end, pushed apart when the ends are close.
    const lasts = series.map(s => s.values.at(-1));
    const labelY = g.points.map(pts => pts.at(-1)[1] + 4);
    const order = labelY.map((_, i) => i).sort((a, b) => labelY[a] - labelY[b]);
    for (let k = 1; k < order.length; k++) labelY[order[k]] = Math.max(labelY[order[k]], labelY[order[k - 1]] + 14);
    const lines = g.points.map((pts, s) => {
        const [ex, ey] = pts.at(-1);
        return `<polyline class="line s${s}" points="${pts.map(([px, py]) => `${r(px)},${r(py)}`).join(' ')}"/>`
            + `<circle class="dot s${s}" cx="${r(ex)}" cy="${r(ey)}" r="3.5"/>`
            + `<text class="last s${s}" x="${r(ex + 7)}" y="${r(labelY[s])}">${rupees(lasts[s])}</text>`;
    }).join('');
    const label = series.map(s => `${s.name}: ${rupees(s.values.at(-1))}`).join('; ');
    return `<svg class="chart-svg" viewBox="0 0 ${size.width} ${size.height}" role="img" aria-label="${escapeXml(label)}">${grid}${xLabels}${lines}</svg>`;
}

function addChartCard(d) {
    const spec = d.spec || {}, f = d.figures || {}, t = state.t, l = lang();
    const svg = h('div', { class: 'chart-wrap' });
    svg.innerHTML = chartSvg(spec, l);
    const legend = h('div', { class: 'chart-legend' }, ...(spec.series || []).map((s, i) =>
        h('span', {}, h('i', { class: `key s${i}`, 'aria-hidden': 'true' }), s.name)));
    const figures = [
        f.months ? `${t.after(f.months)}: ${rupees(f.projected_new)}` : null,
        f.added ? (f.added > 0 ? t.more(rupees(f.added)) : t.less(rupees(-f.added))) : null,
        f.target ? `${t.target}: ${rupees(f.target)}` : null,
    ].filter(Boolean).join(' · ');
    els.msgs.append(h('div', { class: 'chart-card hi' },
        d.title ? h('b', {}, d.title) : null, legend, svg,
        figures ? h('div', { class: 'chart-figures' }, figures) : null,
        d.assumptions?.length ? h('ul', { class: 'small muted' }, ...d.assumptions.map(a => h('li', {}, a))) : null));
    scrollDown();
}

// --- consent cards -------------------------------------------------------------------------

function consentCard(d, position) {
    if (state.cards[d.consent_id]) return;
    const t = state.t, l = lang();
    const words = d.statement[state.didi.language] || d.statement[l] || d.statement.en;
    const body = h('div', { class: 'stack', style: 'gap:8px' });
    const message = h('div');
    const buttons = h('div', { class: 'btns hi' });
    const hint = h('span', { class: 'small muted hi' }, state.voiceActive ? t.speakNow : state.prefs.consent_voice ? t.tapToAnswer : t.orSpeak);
    const node = h('section', { class: 'consent', 'aria-label': t.consentTitle },
        h('div', { class: 'consent-h' }, h('span', { class: 'hi' }, t.consentTitle),
            h('button', { class: 'linklike', type: 'button', onclick: () => speak(words.speech) }, t.listen)),
        position > 0 ? h('p', { class: 'small muted' }, `Asked by the comparison run (${d.agent})`) : null,
        h('p', { class: 'hi' }, words.intro),
        h('ul', { class: 'hi' }, ...d.items.map(item => h('li', {}, h('b', {}, [...new Set(item.labels[l] || item.labels.en)].join(', ')),
            h('span', {}, item.register[l] || item.register.en)))),
        h('p', { class: 'hi muted' }, words.retention),
        h('p', { class: 'hi ask' }, words.ask),
        body, message, buttons,
        hint,
        words.machine_translated ? h('span', { class: 'small muted' }, 'Machine-translated wording') : null);
    const card = { id: d.consent_id, node, items: d.items, status: 'pending', buttons, message, body, words, hint };
    state.cards[d.consent_id] = card;
    state.pending = card;
    showButtons(card, 'ask');
    els.msgs.append(node);
    scrollDown();
    if (state.prefs.consent_voice || state.voiceActive) speakThenListen(words.speech);
    else if (state.prefs.speak) speak(words.speech);
}

// Voice-only consent: read the card (or the read-back) aloud, then open the mic for her answer.
async function speakThenListen(text) {
    await speak(text);
    if (!state.voiceActive && state.pending) setStatus(state.t.tapToAnswer);
}

function showButtons(card, mode) {
    const t = state.t;
    const send = body => answerConsent(card, body);
    if (state.prefs.consent_voice && mode !== 'done') {  // she answers by voice; no buttons
        card.buttons.replaceChildren();
        return;
    }
    if (mode === 'ask') {
        card.buttons.replaceChildren(
            h('button', { class: 'btn yes', type: 'button', onclick: () => send({ button: 'yes' }) }, t.yes),
            h('button', { class: 'btn no', type: 'button', onclick: () => send({ button: 'no' }) }, t.no),
            h('button', { class: 'btn', type: 'button', onclick: () => pickSome(card) }, t.some));
    } else if (mode === 'confirm') {
        card.buttons.replaceChildren(
            h('button', { class: 'btn yes', type: 'button', onclick: () => send({ button: 'confirm' }) }, t.confirm),
            h('button', { class: 'btn no', type: 'button', onclick: () => send({ button: 'reject' }) }, t.reject));
    } else {
        card.buttons.replaceChildren();
    }
}

function pickSome(card) {
    const l = lang();
    const boxes = [];
    card.body.replaceChildren(h('p', { class: 'hi' }, state.t.pickSome), ...card.items.map(item => h('fieldset', { class: 'pick' },
        h('legend', { class: 'small muted' }, item.register[l] || item.register.en),
        ...item.fields.map((field, i) => {
            const box = h('input', { type: 'checkbox', id: `pick-${card.id}-${item.table}-${field}` });
            boxes.push({ box, item, field });
            return h('label', { class: 'check hi' }, box, (item.labels[l] || item.labels.en)[i]);
        }))));
    card.buttons.replaceChildren(h('button', { class: 'btn yes', type: 'button', onclick: () => {
        const granted = [];
        for (const { box, item, field } of boxes) {
            if (!box.checked) continue;
            let g = granted.find(x => x.table === item.table && x.source_id === item.source_id);
            if (!g) granted.push(g = { source_id: item.source_id, table: item.table, fields: [] });
            g.fields.push(field);
        }
        answerConsent(card, { button: 'some', granted });
    } }, state.t.showThese), h('button', { class: 'btn no', type: 'button', onclick: () => answerConsent(card, { button: 'no' }) }, state.t.no));
    scrollDown();
}

async function answerConsent(card, body) {
    if (state.consentBusy || card.status !== 'pending') return;
    state.consentBusy = true;
    pauseListening();
    stopSpeaking();
    for (const b of card.buttons.querySelectorAll('button')) b.disabled = true;
    let out;
    try {
        out = await api(`/v1/consents/${card.id}/answer`, { method: 'POST', body });
    } catch (error) {
        card.message.replaceChildren(h('p', { class: 'notice error' }, error.message));
        for (const b of card.buttons.querySelectorAll('button')) b.disabled = false;
        state.consentBusy = false;
        resumeListening();
        return;
    }
    state.consentBusy = false;
    traceAdd('consent', `${card.id}: ${out.status}${out.interpreter?.by ? ` (by ${out.interpreter.by}${out.interpreter.confidence != null ? `, ${out.interpreter.confidence}` : ''})` : ''}`, 'c');
    if (out.status === 'pending') {
        card.message.replaceChildren(h('p', { class: 'hi notice' }, out.message?.text || ''));
        showButtons(card, out.readback ? 'confirm' : 'ask');
        card.body.replaceChildren();
        if ((state.prefs.consent_voice || state.voiceActive) && out.message?.text) speakThenListen(out.message.text);
        else if (state.prefs.speak && out.message?.text) speak(out.message.text);
        scrollDown();
        resumeListening();
        return;
    }
    markCard(card.id, out.status, out.summary);
    resumeListening();
}

function markCard(id, status, summary) {
    const card = state.cards[id];
    if (!card || card.status !== 'pending') return;
    card.status = status;
    card.body.replaceChildren();
    card.message.replaceChildren();
    showButtons(card, 'done');
    const text = typeof summary === 'string' ? summary : summary?.text || summary?.en || status;
    els.msgs.append(h('div', { class: `interp ${status === 'denied' || status === 'withdrawn' ? 'deny' : ''}` }, h('span', { class: 'hi' }, text)));
    if (state.pending === card) state.pending = null;
    scrollDown();
    if (state.waiter && !Object.values(state.cards).some(c => c.status === 'pending')) {
        const resolve = state.waiter;
        state.waiter = null;
        setStatus(state.t.thinking, true);
        resolve();
    }
}

async function refreshConsents() {
    const box = document.getElementById('consents');
    if (!box || !state.session) return;
    try {
        const { consents } = await api(`/v1/sessions/${state.session.id}/consents`);
        box.replaceChildren(...(consents.length ? consents.map(c => h('div', { class: 'row between', style: 'gap:6px' },
            h('span', { class: 'mono', style: 'font-size:12px' }, `${c.id} · ${c.status}${c.discarded ? ' · discarded' : c.used ? ' · used' : ''}`),
            ['pending', 'granted', 'partial'].includes(c.status) && !c.discarded
                ? h('button', { class: 'btn small quiet', type: 'button', onclick: () => withdraw(c.id) }, 'Withdraw') : null))
            : [h('span', { class: 'muted' }, 'None yet.')]));
    } catch { /* not essential */ }
}

async function withdraw(id) {
    try {
        await api(`/v1/consents/${id}`, { method: 'DELETE' });
        markCard(id, 'withdrawn', 'Withdrawn');
        toast('Consent withdrawn');
        refreshConsents();
    } catch (error) { toast(error.message, 4000); }
}

// --- voice ---------------------------------------------------------------------------------

// One tap starts a conversation; another ends it, even while waiting for ASR/TTS.
els.mic.addEventListener('click', () => {
    if (state.voiceActive) { endVoice(); return; }
    state.voiceActive = true;
    stopSpeaking();
    setMic(state.mic);
    startMic();
});

function setMic(mode) {
    state.mic = mode;
    if (state.pending?.hint) state.pending.hint.textContent = state.voiceActive ? (mode === 'speaking' ? state.t.speaking : state.t.speakNow) : state.t.tapToAnswer;
    els.mic.className = `mic ${mode === 'idle' ? '' : mode}`;
    els.mic.disabled = !state.session;
    els.mic.setAttribute('aria-pressed', String(state.voiceActive));
    els.mic.classList.toggle('active', state.voiceActive);
    els.mic.setAttribute('aria-label', state.voiceActive ? state.t.stop : state.t.speak);
    els['mic-label'].textContent = state.voiceActive ? state.t.stop : state.t.speak;
}

function setBusy(busy) {
    els.text.disabled = busy;
    els['text-form'].querySelector('button').disabled = busy;
    els.mic.disabled = !state.session;
}

function pauseListening() {
    clearTimeout(state.resumeTimer);
    state.voiceEpoch++;
    state.recorder?.cancel();
    state.recorder = null;
    if (state.mic === 'listening' || state.mic === 'busy') setMic('idle');
}

function endVoice() {
    state.voiceActive = false;
    pauseListening();
    stopSpeaking();
    setMic('idle');
    setStatus(state.busy && !state.pending ? state.t.thinking : '');
}

function resumeListening() {
    clearTimeout(state.resumeTimer);
    if (!state.voiceActive || state.mic !== 'idle' || state.consentBusy || (state.busy && !state.pending)) return;
    // Allow the loudspeaker tail to fade before capturing the next utterance.
    state.resumeTimer = setTimeout(() => startMic(), 350);
}

async function startMic() {
    if (!state.voiceActive || (state.busy && !state.pending) || state.consentBusy || state.mic !== 'idle' || !state.session) return;
    clearTimeout(state.resumeTimer);
    const epoch = ++state.voiceEpoch;
    setMic('busy'); // lock before requesting permission to prevent overlapping recorders
    let recorder;
    try {
        recorder = await bhashini.startRecording({ autoStop: true, onSpeech: () => {
            if (epoch === state.voiceEpoch) setStatus(state.t.listening);
        } });
        if (epoch !== state.voiceEpoch) { recorder.cancel(); return; }
        state.recorder = recorder;
        setMic('listening');
        setStatus(state.pending ? state.t.speakNow : state.t.listening);
        const wav = await recorder.done;
        if (epoch !== state.voiceEpoch) return;
        state.recorder = null;
        if (!wav) { setMic('idle'); resumeListening(); return; }
        setMic('busy');
        setStatus(state.t.transcribing, true);
        // Bhojpuri/Chhattisgarhi didis are transcribed with the Hindi model; Dhruva fails bho/hne ASR (HTTP 500).
        const spoken = state.didi.asr_language || state.didi.language || 'hi';
        const language = ['bho', 'hne'].includes(spoken) ? 'hi' : spoken;
        const text = (await bhashini.transcribe(wav, language)).trim();
        if (epoch !== state.voiceEpoch) return;
        setMic('idle');
        if (!text) { setStatus(state.t.nothing); resumeListening(); return; }
        setStatus('');
        if (state.pending) await answerInWords(text, 'voice');
        else await send(text, 'voice');
    } catch (error) {
        if (epoch !== state.voiceEpoch) return;
        const recordingFailed = !recorder;
        endVoice(); // explicit retry on device/network failure; never loop on a denied mic
        setStatus(recordingFailed ? state.t.micDenied : state.t.asrFailed);
        traceAdd(recordingFailed ? 'mic' : 'asr', error.message, 'e');
    } finally {
        if (epoch === state.voiceEpoch) resumeListening();
    }
}

/** Text as the speech engine should get it: "!" is read out or makes TTS stumble, so it becomes a plain stop. */
function forSpeech(text) {
    text = String(text || '');
    const stop = /[\u0900-\u097F]/.test(text) ? '।' : '.';  // Devanagari full stop for Hindi, a period otherwise
    return text.replace(/\s*!+/g, stop).replace(/[।.]\s*([।.?])/g, '$1').replace(/([^\s\d])([।.])(?=[^\s\d.])/g, '$1$2 ').trim();
}

/** Pause capture throughout synthesis/playback; resume only after the current reply ends. */
async function speak(text) {
    pauseListening();
    stopSpeaking();
    const epoch = state.speechEpoch;
    setMic('speaking');
    setStatus(state.t.speaking, true);
    try {
        await bhashini.speak(forSpeech(text), state.didi.language || 'hi');
        if (epoch !== state.speechEpoch) return false;
        state.greeted = true;
        return true;
    } catch (error) {
        if (epoch === state.speechEpoch) {
            traceAdd('tts', error.message, 'e');
            toast(state.t.audioFailed, 6000);
        }
        return false;
    } finally {
        if (epoch === state.speechEpoch) {
            setMic('idle');
            setStatus(state.busy && !state.pending ? state.t.thinking : '');
            resumeListening();
        }
    }
}

function stopSpeaking() {
    state.speechEpoch++;
    bhashini.stop();
    if (state.mic === 'speaking') setMic('idle');
}

window.addEventListener('pagehide', endVoice);
document.addEventListener('visibilitychange', () => { if (document.hidden) endVoice(); });

// --- operator panel ------------------------------------------------------------------------

// Wide screens show the panel beside the chat unless the operator hid it (remembered in this browser);
// narrow screens slide it over the chat and start closed.
const PANEL_KEY = 'playground.operatorPanel';
const narrow = window.matchMedia('(max-width: 900px)');
const layout = document.querySelector('.chat-layout');
const panelOpen = () => narrow.matches ? els.panel.classList.contains('open') : !layout.classList.contains('panel-hidden');
function togglePanel(open) {
    if (narrow.matches) els.panel.classList.toggle('open', open);
    else {
        layout.classList.toggle('panel-hidden', !open);
        try { localStorage.setItem(PANEL_KEY, open ? 'shown' : 'hidden'); } catch { /* storage blocked */ }
    }
    els['panel-toggle'].setAttribute('aria-expanded', String(open));
}
try { layout.classList.toggle('panel-hidden', localStorage.getItem(PANEL_KEY) === 'hidden'); } catch { /* storage blocked */ }
els['panel-toggle'].setAttribute('aria-expanded', String(panelOpen()));
narrow.addEventListener('change', () => els['panel-toggle'].setAttribute('aria-expanded', String(panelOpen())));
els['panel-toggle'].addEventListener('click', () => togglePanel(!panelOpen()));
els['panel-close'].addEventListener('click', () => togglePanel(false));

async function renderPanel() {
    if (status && !status.nvidia_key) {
        els['key-notice'].replaceChildren(h('p', { class: 'notice error' }, h('b', {}, 'NVIDIA_API_KEY is not set. '),
            status.hosted ? 'Add it to the Vercel project\'s environment variables.' : 'Add NVIDIA_API_KEY=nvapi-… to .env. Answers fail until then.'));
    }
    els['mode-direct-label'].textContent = `${(LANGUAGES[state.didi.language] || 'Hindi').split(' · ')[0]} direct`;
    els['mode-direct'].checked = state.prefs.language_mode !== 'pivot';
    els['mode-pivot'].checked = state.prefs.language_mode === 'pivot';
    for (const radio of [els['mode-direct'], els['mode-pivot']]) {
        radio.addEventListener('change', () => radio.checked && savePrefs({ language_mode: radio.value }));
    }
    els.critic.checked = state.prefs.critic;
    const voiceOnly = document.getElementById('consent-voice');
    voiceOnly.checked = state.prefs.consent_voice;
    voiceOnly.addEventListener('change', () => savePrefs({ consent_voice: voiceOnly.checked }));
    els.critic.addEventListener('change', () => savePrefs({ critic: els.critic.checked }));
    els['compare-open'].addEventListener('click', openCompare);
    els['compare-close'].addEventListener('click', () => els['compare-dialog'].close());
    els.speak.checked = state.prefs.speak;
    els['demo-cache'].checked = state.prefs.demo_cache;
    els['steps-view'].value = state.prefs.steps_view;
    for (const [id, key] of [['speak', 'speak'], ['demo-cache', 'demo_cache']]) {
        els[id].addEventListener('change', () => savePrefs({ [key]: els[id].checked }));
    }
    els['steps-view'].addEventListener('change', () => savePrefs({ steps_view: els['steps-view'].value }));
    const verify = document.getElementById('verify');
    verify.value = state.prefs.verify || 'code';
    verify.addEventListener('change', () => savePrefs({ verify: verify.value }));
    try {
        state.models = await api('/v1/llm/models');
    } catch (error) {
        els['key-notice'].append(errorNotice(error));
        return;
    }
    fillModels(els.model, state.prefs.model, false);
    fillModels(els['compare-model'], state.prefs.compare_model, true);
    els.agent.replaceChildren(...state.models.agents.map(a => h('option', { value: a.id, selected: a.id === state.prefs.agent }, a.name)));
    els.compare.replaceChildren(h('option', { value: '' }, 'Off'),
        ...state.models.agents.map(a => h('option', { value: a.id, selected: a.id === state.prefs.compare_with }, a.name)));
    els['compare-model'].hidden = !state.prefs.compare_with;
    showProbe();
    els.model.addEventListener('change', () => { savePrefs({ model: els.model.value }); showProbe(); });
    els.agent.addEventListener('change', () => savePrefs({ agent: els.agent.value }));
    setupPlanner();
    els.compare.addEventListener('change', () => { els['compare-model'].hidden = !els.compare.value; savePrefs({ compare_with: els.compare.value }); });
    els['compare-model'].addEventListener('change', () => savePrefs({ compare_model: els['compare-model'].value }));
    els.probe.addEventListener('click', probe);
    refreshConsents();
    try {
        const { sources } = await api('/v1/sources');
        els.sources.replaceChildren(...sources.map(s => h('div', {}, s.name, h('span', { class: 'muted' }, ` · ${Object.keys(s.tables).length} tables`))),
            h('a', { href: '/connect' }, 'Manage sources & login'));
    } catch { /* not essential */ }
}

function allModels() { return [...state.models.recommended, ...state.models.others]; }

function fillModels(select, current, sameOption) {
    const option = m => h('option', { value: m.id, selected: m.id === current }, m.id);
    select.replaceChildren(
        sameOption ? h('option', { value: '', selected: !current }, 'Same model') : null,
        h('optgroup', { label: 'Recommended' }, ...state.models.recommended.map(option)),
        state.models.others.length ? h('optgroup', { label: 'All chat models' }, ...state.models.others.map(option)) : null);
    if (current && !allModels().some(m => m.id === current)) select.prepend(h('option', { value: current, selected: true }, current));
}

function showProbe() {
    const m = allModels().find(x => x.id === els.model.value);
    const badge = !m?.probe ? h('span', { class: 'badge' }, 'tool support not checked')
        : m.probe === 'tools' ? h('span', { class: 'badge ok' }, 'native tools ✓')
            : h('span', { class: 'badge warn', title: m.probe_detail }, 'text only: runs ReAct and ReWOO');
    els['probe-badge'].replaceChildren(badge);
}

async function probe() {
    const model = els.model.value;
    await (async () => {
        els.probe.disabled = true;
        els['probe-badge'].replaceChildren(h('span', { class: 'badge' }, 'checking…'));
        try {
            const result = await api('/v1/llm/probe', { method: 'POST', body: { model } });
            const m = allModels().find(x => x.id === model);
            if (m) Object.assign(m, { probe: result.probe, probe_detail: result.probe_detail });
        } catch (error) {
            toast(error.message, 5000);
        } finally {
            els.probe.disabled = false;
            showProbe();
        }
    })();
}

// One save at a time: the server merges each change into the stored prefs, so overlapping saves could undo each other.
let saving = Promise.resolve();
async function setupPlanner() {
    const select = document.getElementById('planner');
    const note = document.getElementById('planner-note');
    const laya = select.querySelector('option[value="laya"]');
    select.value = state.prefs.planner || 'llm';
    const show = async () => {
        let health = { ok: false, reason: 'Could not check the Laya sidecar.' };
        try { health = await api('/v1/laya'); } catch { /* shown as unavailable */ }
        laya.disabled = !health.ok && select.value !== 'laya';
        laya.textContent = health.ok ? 'Laya (local classifier)' : 'Laya (local, not running)';
        const agentNote = state.prefs.agent === 'plan_execute' ? '' : ' Used only with the Plan-and-Execute agent.';
        note.textContent = select.value === 'laya'
            ? (health.ok ? `Laya (convaiinnovations/laya) on this computer picks the records; the chat model still writes the answer.${agentNote}`
                         : `Laya is not reachable, so the chat model plans instead. ${health.reason || ''}`)
            : `The chat model decides what records a question needs.${health.ok ? ' Laya is running locally.' : ''}${agentNote}`;
    };
    select.addEventListener('change', () => { savePrefs({ planner: select.value }); show(); });
    els.agent.addEventListener('change', () => setTimeout(show, 300));
    show();
}

function savePrefs(change) {
    saving = saving.then(async () => {
        try {
            const { prefs } = await api(`/v1/sessions/${state.session.id}/prefs`, { method: 'PUT', body: change });
            state.prefs = prefs;
            toast('Saved', 1200);
        } catch (error) {
            toast(error.message, 5000);
        }
    });
    return saving;
}

// --- trace and compare ---------------------------------------------------------------------

function resetTrace() {
    els.trace.replaceChildren();
    els['compare-runs'].replaceChildren();
    els['compare-box'].hidden = true;
}

function traceAdd(tag, text, kind = '') {
    const li = h('li', {}, h('span', { class: `t ${kind}` }, tag), h('span', {}, text));
    els.trace.append(li);
    li.scrollIntoView?.({ block: 'nearest' });
    return li;
}

// Each LLM call's row opens to show its request and response. They arrive live only (never stored), so a
// reloaded page shows the row without them.
const llmRows = {};

function llmRow(ev, text) {
    const body = h('div', { class: 'llm-io' }, h('p', { class: 'muted small' }, 'Request and response are shown only while the question runs; they are not stored.'));
    const li = h('li', { class: 'llm-row' }, h('span', { class: 't' }, 'llm'),
        h('details', {}, h('summary', {}, text), body));
    els.trace.append(li);
    llmRows[`${ev.run_id}:${ev.data.call}`] = body;
    li.scrollIntoView?.({ block: 'nearest' });
}

function llmDetail(ev) {
    const body = llmRows[`${ev.run_id}:${ev.data.call}`];
    if (!body) return;
    const d = ev.data, req = d.request, res = d.response || {};
    const block = (label, value) => h('div', { class: 'io-block' }, h('div', { class: 'io-label' }, label),
        h('pre', {}, typeof value === 'string' ? value : JSON.stringify(value, null, 2)));
    const settings = `model ${req.model} · temperature ${req.temperature} · max_tokens ${req.max_tokens}` +
        (req.stop ? ` · stop ${JSON.stringify(req.stop)}` : '') + (req.tools.length ? ` · tools ${req.tools.join(', ')}` : ' · no tools');
    body.replaceChildren(
        h('div', { class: 'io-head' }, 'Request'), h('p', { class: 'small muted' }, settings),
        ...req.messages.map((m, i) => block(`${i + 1}. ${m.role}${m.tool_call_id ? ` (${m.tool_call_id})` : ''}`,
            m.tool_calls?.length ? `${m.content || ''}\n${JSON.stringify(m.tool_calls, null, 2)}`.trim() : m.content || '')),
        h('div', { class: 'io-head' }, 'Response'),
        h('p', { class: 'small muted' }, `${d.ms ?? '?'} ms · prompt ${d.usage?.prompt_tokens ?? '?'} + completion ${d.usage?.completion_tokens ?? '?'} tokens`),
        res.reasoning ? block('reasoning', res.reasoning) : null,
        block('content', res.content || '(empty)'),
        res.tool_calls?.length ? block('tool calls', res.tool_calls.map(c => ({ name: c.function.name, arguments: tryJson(c.function.arguments) }))) : null,
        h('button', { class: 'btn small', type: 'button', onclick: event => copyText(JSON.stringify({ request: req, response: res }, null, 2), event.currentTarget) }, 'Copy as JSON'));
}

function tryJson(text) { try { return JSON.parse(text); } catch { return text; } }

async function copyText(text, button) {
    try { await navigator.clipboard.writeText(text); button.textContent = 'Copied'; } catch { button.textContent = 'Copy failed'; }
    setTimeout(() => { button.textContent = 'Copy as JSON'; }, 1500);
}

function traceEvent(ev, position) {
    const d = ev.data || {};
    const who = position > 0 ? `[compare ${position}] ` : '';
    const text = {
        run_started: () => `${d.agent_name} · ${d.model}`,
        llm: () => `${d.model} · ${d.ms ?? '?'} ms · ${d.tokens ?? '?'} tokens${d.cached ? ' · from cache' : ''}${d.tool_calls?.length ? ` · calls ${d.tool_calls.join(', ')}` : ''}`,
        status: () => d.en || d.hi,
        tool_call: () => `${d.tool}(${shortJson(d.args)}) → ${d.summary}`,
        answer: () => `${d.text.length} characters${d.evidence?.length ? ` · ${d.evidence.length} sources` : ''}`,
        metrics: () => `LLM calls ${d.llm_calls} · tools ${d.tool_calls} · consent prompts ${d.consent_prompts} · fields read ${d.fields_read}${d.grounded_numbers ? ` · grounded ${d.grounded_numbers}` : ''} · ${d.run_ms} ms`,
        error: () => `${d.code}: ${d.message}`,
        discarded: () => `values dropped: ${(d.refs || []).join(', ')}`,
        consent_request: () => `${d.consent_id} · ${d.purpose?.en} · ${(d.items || []).map(i => `${i.table}(${i.fields.join(', ')})`).join('; ')}`,
        consent_result: () => `${d.consent_id} · ${d.decision}${d.reused ? ' · reused from an earlier run' : ''}${d.refused?.length ? ` · refused: ${d.refused.join(', ')}` : ''}`,
        consent_skipped: () => `not asked again (refused earlier): ${(d.registers || []).join(', ')}`,
        plan: () => (d.steps || []).join(' → ') + (d.items?.length ? ` · needs ${d.items.map(i => `${i.table}(${i.fields.join(', ')})`).join('; ')}` : ''),
        thought: () => d.text,
        critic: () => d.rewrote ? `${d.grounded}/${d.checked} figures grounded; not found: ${d.missing.join(', ')}; rewritten → ${d.after}` : `${d.grounded}/${d.checked} figures grounded`,
        translated: () => d.direction === 'in' ? `question in English: ${d.text}` : `answer before translation: ${d.english}`,
        decision: () => `${d.decision_id} v${d.version} → ${d.outcome} (rule ${(d.rules || []).join(', ') || '–'}) · inputs ${shortJson(d.inputs)}`,
        file: () => `${d.name} · profit ${d.results?.profit_now} → ${d.results?.profit_after} · instalment ${d.results?.emi} · payback ${d.results?.payback_months} months`,
        verification: () => `${d.stage} check · ${d.action}${(d.findings || []).length ? ' · ' + d.findings.map(f => `${f.name}: ${f.detail || ''}`).join('; ') : ''}`,
        chart: () => `${d.kind} · ${d.spec?.x?.length} points · ${d.figures?.projected_now} → ${d.figures?.projected_new} (added ${d.figures?.added}) over ${d.figures?.months} months`,
    }[ev.type];
    const kind = ev.type === 'error' ? 'e' : ev.type.startsWith('consent') ? 'c' : ev.type === 'answer' || ev.type === 'discarded' ? 'g' : '';
    if (ev.type === 'llm' && d.call != null) { llmRow(ev, who + text()); return; }
    traceAdd(ev.type, who + (text ? text() : shortJson(d)), kind);
}

function shortJson(value) {
    const text = JSON.stringify(value ?? {});
    return text.length > 160 ? `${text.slice(0, 160)}…` : text;
}

function compareEvent(ev, view, position) {
    const d = ev.data || {};
    els['compare-box'].hidden = false;
    let card = view.cards[ev.run_id];
    if (!card) {
        card = { node: h('div', { class: 'run-card' }), body: h('div', { class: 'hi' }, 'Running…'), metrics: h('dl', { class: 'metrics' }) };
        card.node.append(h('b', {}, `Run ${position + 1}`), card.body, card.metrics);
        view.cards[ev.run_id] = card;
        els['compare-runs'].append(card.node);
    }
    if (ev.type === 'run_started') card.node.firstChild.textContent = `${d.agent_name} · ${d.model}`;
    if (ev.type === 'answer') card.body.textContent = d.text;
    if (ev.type === 'error') card.body.textContent = `${d.code}: ${d.message}`;
    if (ev.type === 'metrics') {
        card.metrics.replaceChildren(...[['LLM calls', d.llm_calls], ['Tool calls', d.tool_calls], ['Consent prompts', d.consent_prompts],
            ['Fields read', d.fields_read], ['Tokens', d.prompt_tokens + d.completion_tokens], ['Time', `${(d.run_ms / 1000).toFixed(1)} s`]]
            .flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, String(v))]));
    }
}

// --- compare dialog ------------------------------------------------------------------------

function openCompare() {
    const view = state.lastView;
    if (!view) return;
    const runs = Object.values(view.runs).filter(r => r.agent).sort((a, b) => a.position - b.position);
    const rowsDef = [
        ['LLM calls', m => m.llm_calls], ['Tool calls', m => m.tool_calls], ['Consent prompts', m => m.consent_prompts, true], ['  of them reused', m => m.consent_reused ?? 0],
        ['Fields read', m => m.fields_read, true], ['Grounded figures', m => m.grounded_numbers ?? 'n/a'],
        ['Decision', m => m.decision || '–'], ['Tokens', m => (m.prompt_tokens || 0) + (m.completion_tokens || 0)], ['Time', m => `${((m.run_ms || 0) / 1000).toFixed(1)} s`],
    ];
    const best = {};
    for (const [label, get, lower] of rowsDef) {
        if (!lower) continue;
        const values = runs.filter(r => r.metrics).map(r => get(r.metrics));
        if (values.length > 1) best[label] = Math.min(...values);
    }
    els['compare-question'].textContent = state.lastQuestion ? `Question: ${state.lastQuestion}` : '';
    els['compare-cols'].replaceChildren(...runs.map(run => h('article', { class: 'run-col' },
        h('header', {}, h('b', {}, run.agent), h('span', { class: 'mono small muted' }, run.model),
            h('span', { class: 'small muted' }, [run.position === 0 ? 'answered her' : `compare run ${run.position}`, run.mode === 'pivot' ? 'pivot' : '', run.critic ? 'self-check on' : ''].filter(Boolean).join(' · '))),
        h('p', { class: 'hi' }, run.answer || 'No answer.'),
        h('dl', { class: 'metrics' }, ...rowsDef.flatMap(([label, get]) => {
            const value = run.metrics ? get(run.metrics) : '–';
            const cls = best[label] === undefined || !run.metrics ? '' : value === best[label] ? 'good' : 'bad';
            return [h('dt', {}, label), h('dd', { class: cls }, String(value))];
        })))));
    els['compare-dialog'].showModal();
}

// --- admin view ----------------------------------------------------------------------------
// An admin sees one didi's chat as she sees it (her answers and their charts), view only, and draws charts from her
// registers. The server records the reason with every read; she still answers her own consent cards.

const ADMIN_CHARTS = [['savings', 'Savings'], ['loans', 'Loans'], ['income', 'Income'], ['wages', 'Wages'],
    ['projection', 'Savings projection'], ['earnings', 'Business earnings']];

// The request body for one chart: months for projections (1–120), an extra monthly deposit for savings (₹0–1,00,000).
function adminChartBody(kind, reason, inputs = {}) {
    const clamp = (value, lo, hi, fallback) => {
        const n = Math.round(Number(value));
        return value === '' || value == null || !Number.isFinite(n) ? fallback : Math.min(Math.max(n, lo), hi);
    };
    const body = { kind, reason };
    if (kind === 'projection' || kind === 'earnings') body.months = clamp(inputs.months, 1, 120, 12);
    if (kind === 'projection') body.extra = clamp(inputs.extra, 0, 100000, 0);
    return body;
}

// Her past turns (oldest first) as what the chat shows: her question, the answer, then the answer's charts.
function adminTurnItems(turns) {
    return (turns || []).flatMap(t => [{ who: 'me', text: t.question }, t.answer ? { who: 'bot', text: t.answer } : null,
        ...(t.charts || []).map(chart => ({ chart }))].filter(Boolean));
}

function adminExit(message) {
    els.msgs.append(h('p', { class: 'notice' }, message, ' ', h('a', { href: '/login.html#admin' }, 'Admin sign-in')));
}

function askAdminReason(didiId) {
    return new Promise(resolve => {
        const input = h('input', { maxlength: '200', required: true, placeholder: 'e.g. monthly savings review' });
        const form = h('form', { class: 'card stack admin-reason' },
            h('label', { class: 'field' }, `Why are you opening ${didiId}'s records? It is recorded.`, input),
            h('button', { class: 'btn primary', type: 'submit' }, 'Open her chat'));
        form.addEventListener('submit', event => {
            event.preventDefault();
            if (input.value.trim().length < 3) return;
            form.remove();
            resolve(apiModule.adminAuth.reason(didiId, input.value.trim()));
        });
        els.msgs.append(form);
        input.focus();
    });
}

function adminTools(reason) {
    const months = h('input', { type: 'number', min: '1', max: '120', value: '12', 'aria-label': 'Months to project' });
    const extra = h('input', { type: 'number', min: '0', max: '100000', step: '50', value: '100', 'aria-label': 'Extra saved each month (₹)' });
    const draw = async (kind, label, button) => {
        button.disabled = true;
        setStatus(`Drawing ${label.toLowerCase()}…`, true);
        try {
            const d = await api(`/v1/admin/didis/${encodeURIComponent(adminDidi)}/charts`, { method: 'POST',
                body: adminChartBody(kind, reason, { months: months.value, extra: extra.value }) });
            els.msgs.append(h('div', { class: 'msg me admin-ask' }, h('div', { class: 'bubble' }, `Admin chart: ${label}`)));
            addChartCard(d);
        } catch (error) {
            if (error.code === 'admin_required') { apiModule.adminAuth.set(null); adminExit('Your admin sign-in ended.'); }
            else els.msgs.append(errorNotice(error));
            scrollDown();
        } finally {
            button.disabled = false;
            setStatus('');
        }
    };
    return h('div', { class: 'admin-tools', role: 'group', 'aria-label': 'Draw a chart from her registers' },
        h('span', { class: 'h5' }, 'Draw a chart from her registers'),
        h('div', { class: 'row admin-chart-buttons' }, ...ADMIN_CHARTS.map(([kind, label]) => {
            const button = h('button', { class: 'btn small', type: 'button', 'data-kind': kind }, label);
            button.addEventListener('click', () => draw(kind, label, button));
            return button;
        })),
        h('div', { class: 'row admin-inputs small' },
            h('label', {}, 'Months ', months), h('label', {}, 'Extra saved each month ₹ ', extra)));
}

async function startAdminView() {
    document.body.classList.add('admin-mode');
    layout.classList.add('panel-hidden');
    for (const id of ['mic', 'mic-label', 'text-form', 'panel-toggle']) els[id].hidden = true;
    els.mic.disabled = true;
    const historyToggle = document.getElementById('history-toggle');
    if (historyToggle) historyToggle.hidden = true;
    const switchLink = document.querySelector('.chat-head a[href="/login"]');
    if (switchLink) { switchLink.href = '/login.html#admin'; switchLink.textContent = 'Switch didi'; }
    if (!apiModule.adminAuth?.get()) return adminExit('Sign in as admin to view a didi.');
    const reason = apiModule.adminAuth.reason(adminDidi) || await askAdminReason(adminDidi);
    let data;
    try {
        data = await api(`/v1/admin/didis/${encodeURIComponent(adminDidi)}/view`, { method: 'POST', body: { reason } });
    } catch (error) {
        if (error.code === 'admin_required') apiModule.adminAuth.set(null);
        els.msgs.append(errorNotice(error));
        return adminExit(error.code === 'admin_required' ? 'Your admin sign-in ended.' : 'Pick another didi.');
    }
    state = { ...state, didi: data.didi, prefs: { speak: false, consent_voice: false }, t: TEXT[data.didi.language] || TEXT.en };
    renderHeader();
    const sessions = data.sessions.length;
    els.msgs.append(h('div', { class: 'notice admin-banner', role: 'note' },
        h('b', {}, `Admin view · ${data.admin}. `), 'View only. ',
        `${sessions} login${sessions === 1 ? '' : 's'}, ${data.turns.length} answered question${data.turns.length === 1 ? '' : 's'}. `,
        'Reason recorded: ', h('i', {}, data.reason), '. Every chart reads her register and is logged in the ',
        h('a', { href: '/department' }, 'department view'), '.'));
    for (const item of adminTurnItems(data.turns)) {
        if (item.chart) addChartCard(item.chart);
        else addBubble(item.who, item.text, { speakable: false });
    }
    if (!data.turns.length) els.msgs.append(h('p', { class: 'muted small' }, 'She has not asked anything yet.'));
    document.querySelector('.composer').append(adminTools(reason));
}

if (adminDidi) startAdminView();

// --- history panel -------------------------------------------------------------------------
// Her past questions and answers (session memory), grouped by day, newest first. Clearing them is her right,
// so the panel offers it with one confirm step. Self-contained: needs h, toast, api, apiModule, sessionId.

const HISTORY_TEXT = {
    today: 'आज', yesterday: 'कल', unknownDay: 'तारीख़ पता नहीं', loading: 'पिछली बातें ला रहे हैं…',
    empty: 'अभी कोई पिछली बात नहीं है। आप जो पूछेंगी, वह यहाँ दिखेगा।',
    error: 'पिछली बातें अभी नहीं मिल पाईं।', retry: 'फिर से कोशिश करें',
    noSession: 'पहले किसी दीदी के रूप में लॉग इन कीजिए।', noAnswer: 'इस सवाल का जवाब नहीं मिला था।',
    cleared: 'आपकी सारी पिछली बातें मिटा दी गईं।', clearFailed: 'अभी मिटा नहीं पाए। फिर से कोशिश कीजिए।',
};

// A browser holding an api.js from before these helpers still gets her history, through api() directly.
const historyApi = {
    get: id => (apiModule.getHistory ? apiModule.getHistory(id) : api(`/v1/sessions/${encodeURIComponent(id)}/history?limit=50`)),
    clear: id => (apiModule.clearHistory ? apiModule.clearHistory(id) : api(`/v1/sessions/${encodeURIComponent(id)}/history`, { method: 'DELETE' })),
};

function historyDayKey(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function historyDayLabel(date, now = new Date()) {
    if (!Number.isFinite(date.getTime())) return HISTORY_TEXT.unknownDay;
    const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    const key = historyDayKey(date);
    if (key === historyDayKey(now)) return HISTORY_TEXT.today;
    if (key === historyDayKey(yesterday)) return HISTORY_TEXT.yesterday;
    const options = { day: 'numeric', month: 'long' };
    if (date.getFullYear() !== now.getFullYear()) options.year = 'numeric';
    return date.toLocaleDateString('hi-IN', options);
}

function historyTime(date) {
    return Number.isFinite(date.getTime()) ? date.toLocaleTimeString('hi-IN', { hour: 'numeric', minute: '2-digit' }) : '';
}

/** Turns -> [{key, label, items: [{turn, date}]}], newest day first and newest turn first; undated turns last. */
function groupHistory(turns, now = new Date()) {
    const dated = (Array.isArray(turns) ? turns : []).filter(Boolean).map(turn => ({ turn, date: new Date(turn.at) }));
    const time = item => { const t = item.date.getTime(); return Number.isFinite(t) ? t : -Infinity; };
    dated.sort((a, b) => time(b) - time(a) || 0);
    const groups = new Map();
    for (const item of dated) {
        const key = Number.isFinite(item.date.getTime()) ? historyDayKey(item.date) : 'unknown';
        if (!groups.has(key)) groups.set(key, { key, label: historyDayLabel(item.date, now), items: [] });
        groups.get(key).items.push(item);
    }
    return [...groups.values()];
}

const historyEls = Object.fromEntries(['history-toggle', 'history', 'history-backdrop', 'history-close', 'history-status', 'history-list',
    'history-detail', 'history-back', 'history-detail-when', 'history-detail-q', 'history-detail-a', 'history-clear', 'history-confirm',
    'history-clear-yes', 'history-clear-no'].map(id => [id.replace(/^history-?/, '') || 'panel', document.getElementById(id)]));
const historyState = { open: false, turns: [], token: 0, from: null };

if (historyEls.panel && historyEls.toggle) {
    historyEls.toggle.addEventListener('click', () => (historyState.open ? closeHistory() : openHistory()));
    historyEls.close.addEventListener('click', () => closeHistory());
    historyEls.backdrop.addEventListener('click', () => closeHistory());
    historyEls.back.addEventListener('click', () => showHistoryList(true));
    historyEls.clear.addEventListener('click', () => showHistoryConfirm(true));
    historyEls['clear-no'].addEventListener('click', () => showHistoryConfirm(false, true));
    historyEls['clear-yes'].addEventListener('click', () => clearHistoryNow());
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || !historyState.open || event.defaultPrevented) return;
        event.preventDefault();
        closeHistory();
    });
}

function openHistory() {
    historyState.open = true;
    historyEls.panel.hidden = false;
    historyEls.backdrop.hidden = false;
    historyEls.toggle.setAttribute('aria-expanded', 'true');
    showHistoryConfirm(false);
    showHistoryList(false);
    historyEls.close.focus();
    return loadHistory();
}

function closeHistory() {
    if (!historyState.open) return;
    historyState.open = false;
    historyState.token++;  // a late response must not repaint a closed panel
    historyEls.panel.hidden = true;
    historyEls.backdrop.hidden = true;
    historyEls.toggle.setAttribute('aria-expanded', 'false');
    historyEls.toggle.focus();
}

function setHistoryStatus(...children) {
    historyEls.status.replaceChildren(...children.filter(child => child !== ''));  // keep :empty true when there is nothing to say
}

async function loadHistory() {
    const token = ++historyState.token;
    historyState.turns = [];
    historyEls.list.replaceChildren();
    historyEls.clear.disabled = true;
    if (!sessionId) { setHistoryStatus(HISTORY_TEXT.noSession); return; }
    setHistoryStatus(h('span', { class: 'spinner', 'aria-hidden': 'true' }), ' ', HISTORY_TEXT.loading);
    let data;
    try {
        data = await historyApi.get(sessionId);
    } catch (error) {
        if (token !== historyState.token) return;
        const retry = h('button', { class: 'btn small', type: 'button' }, HISTORY_TEXT.retry);
        retry.addEventListener('click', () => loadHistory());
        setHistoryStatus(h('div', { class: 'notice error history-error', role: 'alert' },
            h('b', {}, HISTORY_TEXT.error), error?.status !== undefined ? h('span', { class: 'small' }, error.message) : null, h('div', {}, retry)));
        return;
    }
    if (token !== historyState.token) return;
    historyState.turns = Array.isArray(data?.turns) ? data.turns : [];
    renderHistoryList();
}

function renderHistoryList() {
    const groups = groupHistory(historyState.turns);
    historyEls.clear.disabled = groups.length === 0;
    setHistoryStatus(groups.length ? '' : HISTORY_TEXT.empty);
    historyEls.list.replaceChildren(...groups.map((group, index) => h('section', { class: 'history-day', 'aria-labelledby': `history-day-${index}` },
        h('h3', { class: 'history-day-h hi', id: `history-day-${index}` }, group.label),
        h('ul', { class: 'history-items' }, ...group.items.map(({ turn, date }) => {
            const button = h('button', { class: 'history-item', type: 'button' },
                h('span', { class: 'q hi' }, turn.question || '…'), h('span', { class: 't' }, historyTime(date)));
            button.addEventListener('click', () => showHistoryDetail(turn, button));
            return h('li', {}, button);
        })))));
}

function showHistoryDetail(turn, from) {
    const date = new Date(turn.at);
    historyState.from = from;
    historyEls['detail-when'].textContent = [historyDayLabel(date), historyTime(date)].filter(Boolean).join(' · ');
    historyEls['detail-q'].textContent = turn.question || '…';
    historyEls['detail-a'].textContent = turn.answer || HISTORY_TEXT.noAnswer;
    historyEls.list.hidden = true;
    historyEls.status.hidden = true;
    historyEls.detail.hidden = false;
    historyEls.back.focus();
}

function showHistoryList(refocus) {
    const from = historyState.from;
    historyState.from = null;
    historyEls.detail.hidden = true;
    historyEls.list.hidden = false;
    historyEls.status.hidden = false;
    if (refocus && from?.isConnected !== false) from?.focus();
}

function showHistoryConfirm(open, refocus) {
    historyEls.confirm.hidden = !open;
    historyEls.clear.hidden = open;
    if (open) historyEls['clear-no'].focus();
    else if (refocus) historyEls.clear.focus();
}

async function clearHistoryNow() {
    const yes = historyEls['clear-yes'];
    yes.disabled = true;
    try {
        await historyApi.clear(sessionId);
    } catch {
        toast(HISTORY_TEXT.clearFailed);
        return;
    } finally {
        yes.disabled = false;
    }
    historyState.token++;
    historyState.turns = [];
    showHistoryConfirm(false);
    showHistoryList(false);
    renderHistoryList();
    setHistoryStatus(HISTORY_TEXT.cleared);
    historyEls.close.focus();
}
