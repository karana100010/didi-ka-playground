// The "About" window: how the chatbot works, end to end. Opened from the top bar on every page.
import { h } from '/api.js';

const FLOW = [
    ['Hears her', 'Bhashini speech-to-text turns her voice into text in her own language (ASR language from her login). She can also type.'],
    ['Plans', 'A planner decides whether the question needs her records, and exactly which fields. It is the chat model, or Laya on this computer when chosen in the operator panel.'],
    ['Asks consent', 'She is asked once, aloud and in her language, for just those fields and why. A yes or no is understood from her words (custom words first, then the model), read back, and anything unclear counts as no.'],
    ['Reads through the gateway', 'Every read goes through one gateway: consent for this question, the owner department\'s policy, her rows only (the filter comes from her login), group data for office bearers only. Each read is logged for the department.'],
    ['Works it out', 'Code adds up the figures. Lakhpati status and bank-loan readiness come from official decision tables (DMN), not from the model. Business plans are Excel files with live formulas and mock JEEViKA unit costs.'],
    ['Answers and forgets', 'The chat model writes a short spoken answer; a self-check withholds any figure that does not come from her records or the scheme notes. Bhashini speaks it. The fetched records are deleted when the answer is done; only the question and answer text are kept as conversation history.'],
];

const PARTS = [
    ['Chat model', 'NVIDIA API (build.nvidia.com), Nemotron by default. Chosen in the operator panel; reasoning is switched off for speed.'],
    ['Planning model', 'Optional: Laya (convaiinnovations/laya), a 322M-parameter multilingual classifier that runs locally. It answers typed questions about her message (what she wants, which register) instead of writing a plan, so it is fast and cheap, but its zero-shot labels can be wrong; unsure decisions fall back to the register. Not available on Vercel.'],
    ['Agents', 'Plan-and-Execute (default: one consent card per question), native tool calling, ReAct, ReWOO and Direct. Compare runs two side by side on the same consent.'],
    ['Voice', 'Bhashini ASR, TTS and translation (bhashini_kit). Pivot mode translates to English for the model and back, protecting names and amounts.'],
    ['Data', 'Excel uploads, Google Sheet links and SQL databases, read-only. Sample LokOS, VB–G RAM G and AgriStack registers with eight invented didis.'],
    ['Runs', 'Each run records its steps so it can pause for her consent (or Vercel\'s 60-second limit) and resume by replaying them. The record is purged at the end.'],
];

let dialog;

export function openAbout() {
    if (!dialog) {
        dialog = h('dialog', { class: 'about-dialog', 'aria-labelledby': 'about-title' },
            h('div', { class: 'row between' },
                h('h2', { id: 'about-title' }, 'How the chatbot works'),
                h('button', { class: 'btn small', type: 'button', onclick: () => dialog.close() }, 'Close')),
            h('p', { class: 'muted' }, 'A simulation of the GramStack shared layer: a didi asks in her own language, the assistant asks her permission before reading her records, reads only what the answer needs, answers, and forgets the data.'),
            h('p', { class: 'hi muted' }, 'दीदी अपनी भाषा में पूछती हैं; रिकॉर्ड देखने से पहले उनकी अनुमति ली जाती है, सिर्फ़ ज़रूरी जानकारी पढ़ी जाती है, और जवाब के बाद वह मिटा दी जाती है।'),
            h('ol', { class: 'about-flow' }, ...FLOW.map(([title, text]) => h('li', {}, h('b', {}, title), h('span', {}, text)))),
            h('h3', {}, 'What it is made of'),
            h('dl', { class: 'about-parts' }, ...PARTS.flatMap(([term, text]) => [h('dt', {}, term), h('dd', {}, text)])),
            h('p', { class: 'muted small' }, 'Everything here is mock data. The operator panel on the chat page shows each step, each model call with its request and response, and the consent ledger. The department view shows the access log.'));
        document.body.append(dialog);
    }
    dialog.showModal();
}
