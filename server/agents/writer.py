"""Writing the final answer from facts, with guards for a small model's failure modes.

A writer reply is rejected when it degenerates (a word or phrase repeating), runs far too long, or asks for
permission after her records were already read. It gets one retry; if that also fails, the answer is built
from the exact figures by code, so she still gets the numbers rather than noise.
"""
import re

import settings
from consent import catalog

from .context import clean_answer

ASKS_PERMISSION = ('अनुमति चाहिए', 'अनुमति दें', 'अनुमति दीजिए', 'देख सकता हूँ?', 'देख सकती हूँ?', 'जानकारी चाहिए।',
                   'क्या मैं देखूं', 'क्या मैं देखूँ', 'क्या मैं देख ', 'रिकॉर्ड देखूंगी', 'रिकॉर्ड देखूँगी', 'दिखाने हैं',
                   'may i see', 'need your permission', 'can i look')


def degenerate(text):
    words = text.split()
    if len(text) > 1500:
        return True
    for n in (1, 2, 3):  # the same word, pair or triple four or more times in a row
        for i in range(len(words) - n * 4 + 1):
            chunk = words[i:i + n]
            if all(words[i + k * n:i + k * n + n] == chunk for k in range(4)):
                return True
    sentences = [' '.join(x.split()) for x in re.split(r'[।.!?\n]+', text) if len(x.strip()) >= 12]
    if any(sentences.count(x) >= 3 for x in sentences):  # "मैं नेमोट्रॉन हूँ, GramStack की सहायक।" three times
        return True
    grams = [tuple(words[i:i + 6]) for i in range(len(words) - 5)]
    return any(grams.count(g) >= 3 for g in grams)  # a long phrase coming back, not necessarily in a row


MODEL_NAMES = ('नेमोट्रॉन', 'नेमोट्रोन', 'नीमोट्रॉन', 'nemotron', 'llama', 'mistral', 'मिस्ट्रल', 'gemma', 'qwen',
               'nvidia', 'एनवीडिया', 'language model', 'भाषा मॉडल')
_SOURCE_IDS = re.compile(r'\([^()]*\bsrc_[0-9a-zA-Z]+\b[^()]*\)|'
                         r'(?:(?:स्रोत|source)\s*[:：]?\s*)?\bsrc_[0-9a-zA-Z]+\b|'
                         r'(?:तालिका|table|कॉलम|column|फ़ील्ड|field)s?\s*[:：]?\s*(?:%s)\b'
                         % '|'.join(sorted(map(re.escape, {*catalog.REGISTERS, *catalog.FIELDS}), key=len, reverse=True)), re.I)


def tidy(text):
    """The answer without what a model copies from its setup: sentences naming the model ("मैं नेमोट्रॉन हूँ")
    and source ids or table names ("स्रोत src_…, तालिका savings"). She hears plain words only."""
    if not text:
        return text
    parts = re.split(r'(?<=[।.!?])\s+', text.strip())
    kept = [p for p in parts if not any(m in p.lower() for m in MODEL_NAMES)]
    text = ' '.join(kept)
    text = _SOURCE_IDS.sub('', text)
    text = re.sub(r'\(\s*[,;]?\s*\)', '', text)
    text = re.sub(r'\s*,\s*(?=[,।.!?)])', '', text)
    text = re.sub(r'\s+([,।.!?])', r'\1', text)
    return re.sub(r'\s{2,}', ' ', text).strip(' ,')


def usable(ctx, text):
    if not text or degenerate(text):
        return False
    if ctx.fetched and any(p in text.lower() for p in ASKS_PERMISSION):
        return False
    if speaks_as_her(text) or leaks_setup(text):
        return False
    return True


PERMISSION_WORDS = ('अनुमति', 'इजाज़त', 'इजाजत', 'सहमति', 'permission', 'consent')
LOOK_QUESTIONS = ('क्या मैं देख', 'देखूँ?', 'देखूं?', 'shall i check', 'may i check', 'can i check')


def asks_permission(text):
    """The reply asks her, in words, whether it may look at her records."""
    text = (text or '').lower()
    return (any(p in text for p in ASKS_PERMISSION + LOOK_QUESTIONS)
            or ('?' in text and any(w in text for w in PERMISSION_WORDS)))


def asks_instead_of_consent(ctx, text):
    """Asking permission in words when request_consent was never called in this run. She then has to say "जी
    देखिए" in another turn; the app needs the tool call to show her the consent card."""
    return not ctx.consent_asked and asks_permission(text)


SETUP_WORDS = ('परीक्षण मॉडल', 'NVIDIA_API', 'API_KEY', 'API key', 'एपीआई', 'एनवीआईडीआईए',
               '<|tool_call', 'tool_call>', 'call:fetch_records', 'call:request_consent')  # a model's raw tool syntax


def leaks_setup(text):
    """Text about the playground's setup (the local stand-in model, API keys), which a didi must never hear. A real
    model copies it from the conversation history if a stand-in answer ever got in there."""
    if settings.FAKE_LLM and not settings.nvidia_key():
        return False  # the stand-in itself is running: its marker is expected
    return any(w.lower() in (text or '').lower() for w in SETUP_WORDS)


def speaks_as_her(text):
    """The writer answering in her voice ("मेरा नाम सुनीता है… मेरी बचत…") instead of speaking to her."""
    words = text.replace('।', ' ').split()
    mine = sum(w in ('मेरा', 'मेरी', 'मेरे') for w in words)
    return text.lstrip().startswith('मेरा नाम') or (mine >= 2 and not any(w.startswith('आप') for w in words))


def write(ctx, system, history, fallback):
    """The writer's answer, or fallback() if two tries fail."""
    for temperature in (0.2, 0.5):
        text = clean_answer(ctx.llm([{'role': 'system', 'content': system}, *history, {'role': 'user', 'content': ctx.question}],
                                    max_tokens=450, temperature=temperature)['content'])
        if usable(ctx, text):
            return text
        ctx.emit('writer_rejected', reason='degenerate' if text and degenerate(text) else 'asked for permission again' if text else 'empty',
                 sample=(text or '')[:200])
    return fallback()


def plain_answer(ctx, registers):
    """Exact figures stated plainly, by code: the last resort when the writer fails twice."""
    hi = ctx.llm_language == 'hi'
    name = str(ctx.didi.get('name_local' if hi else 'name') or '').split(' ')[0]
    lines = []
    for reg in registers:
        figures = []
        latest = reg.get('latest_month') or {}
        for key, value in {**(reg.get('current') or {}), **{k: v for k, v in latest.items() if k != 'month'}}.items():
            if isinstance(value, (int, float)) or (isinstance(value, str) and value):
                label = catalog.FIELDS.get(key, (key, key, ''))[0 if hi else 1]
                figures.append(f'{label} {value:,}' if isinstance(value, (int, float)) else f'{label} {value}')
        for key, value in reg.items():
            if key.startswith('last_12_months_') and not key.startswith('last_12_months_monthly') and isinstance(value, (int, float)):
                column = key.removeprefix('last_12_months_')
                label = catalog.FIELDS.get(column, (column, column, ''))[0 if hi else 1]
                figures.append(f'पिछले 12 महीनों में {label} {value:,}' if hi else f'{label} over the last 12 months {value:,}')
        if figures:
            when = f' ({latest.get("month")})' if latest.get('month') else ''
            names = catalog.REGISTERS.get(reg.get('table'), (reg.get('register', ''), reg.get('register', '')))
            lines.append(f'{names[0] if hi else names[1]}{when}: ' + ', '.join(figures[:6]))
    if not lines:
        return (f'{name} जी, माफ़ कीजिए, अभी ठीक से जवाब नहीं बन पाया। कृपया फिर से पूछिए।' if hi
                else f'{name}, sorry, I could not put the answer together. Please ask again.')
    head = f'{name} जी, आपके रिकॉर्ड के अनुसार: ' if hi else f'{name}, according to your records: '
    return head + '। '.join(lines) + ('।' if hi else '.')


_BRACKETED = re.compile(r'\s*[(\[]([^()\[\]]*)[)\]]')
_SENTENCE = re.compile(r'[^।.!?]+[।.!?]?')


def _mostly_latin(text, words=3):
    letters = [c for c in text if c.isalpha()]
    latin = sum(c.isascii() for c in letters)
    return len(text.split()) >= words and letters and latin / len(letters) > 0.7


def strip_english(text):
    """Drop English translations a model adds to an Indian-language answer: bracketed English and whole English
    sentences. Short English words inside a sentence (SHG, NRLM, Excel) stay."""
    if not text or not re.search(r'[\u0900-\u0DFF]', text):
        return text  # nothing in an Indian script: leave it
    text = _BRACKETED.sub(lambda m: '' if _mostly_latin(m.group(1)) else m.group(0), text)
    kept = [s for s in _SENTENCE.findall(text) if not _mostly_latin(s)]
    return re.sub(r'\s{2,}', ' ', ''.join(kept)).strip()
