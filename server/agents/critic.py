"""Self-check: every figure in an answer built from her records must be a number that was fetched or
calculated in this run. The check itself is code; the LLM is called only to rewrite an answer that fails it.
"""
from consent import catalog

from . import facts, prompts
from .context import clean_answer

REWRITE = """
This answer to her question contains figures that are not in the data that was read: {missing}.
Exact totals of what was read, labelled: {summaries}
Other numbers that were read or calculated: {known}.
Rewrite the answer in the same language, keeping everything else, so that every figure is one of those
numbers. Never swap in a number that means something else (a one-month figure for a yearly total, say); if a
figure cannot be supported, leave it out. Reply with the rewritten answer only.

Answer:
{answer}"""


SCHEME_NUMBERS = {n for text in catalog.SCHEME_INFO.values() for n in facts.numbers_in(text)} | {100000.0, 10000.0}


def check(ctx, answer):
    known = ctx.known | SCHEME_NUMBERS
    if not ctx.fetched:
        # Nothing was read from her records, so any figure about her must be invented. Scheme figures
        # (₹1 lakh, ₹10,000 a month…) and code's sums of figures she said herself (a business's earnings) are fine;
        # anything else is withheld.
        got, total, missing = facts.grounded(answer, known)
        if not total:
            ctx.metrics['grounded_numbers'] = None
            return answer
        ctx.metrics['grounded_numbers'] = f'{got}/{total}' + (' (withheld)' if missing else '')
        ctx.emit('critic', checked=total, grounded=got, missing=[f'{n:,.0f}' for n in missing], rewrote=False,
                 withheld=bool(missing), rejected=answer if missing else None, no_records=True)
        return NO_RECORDS.get(ctx.llm_language, NO_RECORDS['en']) if missing else answer
    got, total, missing = facts.grounded(answer, known)
    ctx.metrics['grounded_numbers'] = f'{got}/{total}'
    if not missing or ctx.worded_by_code:
        # An answer worded by code from tool results (a business plan, a decision) is never sent to the LLM to be
        # rewritten: that only garbles exact figures. A miss there is a bug in the wording code, so it is reported.
        ctx.emit('critic', checked=total, grounded=got, missing=[f'{n:,.0f}' for n in missing], rewrote=False)
        return answer
    shown = ', '.join(f'{n:,.0f}' for n in sorted(known, reverse=True)[:60])
    import json
    prompt = REWRITE.format(missing=', '.join(f'{n:,.0f}' for n in missing), known=shown, answer=answer,
                            summaries=json.dumps(ctx.summaries, ensure_ascii=False, default=str)[:4000])
    rewritten = clean_answer(ctx.llm([{'role': 'system', 'content': prompts.system(ctx.didi, ctx.llm_language)},
                                      {'role': 'user', 'content': prompt}], temperature=0)['content'])
    got2, total2, missing2 = facts.grounded(rewritten, known)
    withheld = bool(missing2) or not rewritten
    ctx.metrics['grounded_numbers'] = f'{got2}/{total2}' + (' (withheld)' if withheld else '')
    ctx.emit('critic', checked=total, grounded=got, missing=[f'{n:,.0f}' for n in missing], rewrote=True,
             after=f'{got2}/{total2}', withheld=withheld, rejected=answer if withheld else None)
    if withheld:  # never give her a figure that cannot be traced to her records
        return WITHHELD.get(ctx.llm_language, WITHHELD['en'])
    return rewritten


NO_RECORDS = {
    # No permission question in words: she would have to answer it in another turn. Asking again brings a consent card.
    'hi': 'माफ़ कीजिए, आपकी जानकारी देखे बिना मैं यह संख्या नहीं बता सकती। आप यही सवाल फिर से पूछेंगी, तो मैं आपका रजिस्टर देखकर बताऊँगी।',
    'en': 'Sorry, I cannot give that figure without looking at your register. Ask the same question again and I will check your register to answer it.',
}

WITHHELD = {
    'hi': 'माफ़ कीजिए, मैं आपके रजिस्टर के आँकड़े ठीक से नहीं पढ़ पाई, इसलिए गलत संख्या नहीं बताऊँगी। कृपया फिर से पूछिए।',
    'en': 'Sorry, I could not read the figures in your register correctly, so I will not give you a wrong number. Please ask again.',
}
