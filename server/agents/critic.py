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


def check(ctx, answer):
    if not ctx.fetched:  # nothing was read from her records: there is nothing to check figures against
        ctx.metrics['grounded_numbers'] = None
        return answer
    known = ctx.known | {n for text in catalog.SCHEME_INFO.values() for n in facts.numbers_in(text)}
    got, total, missing = facts.grounded(answer, known)
    ctx.metrics['grounded_numbers'] = f'{got}/{total}'
    if not missing:
        ctx.emit('critic', checked=total, grounded=got, missing=[], rewrote=False)
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


WITHHELD = {
    'hi': 'माफ़ कीजिए, मैं आपके रजिस्टर के आँकड़े ठीक से नहीं पढ़ पाई, इसलिए गलत संख्या नहीं बताऊँगी। कृपया फिर से पूछिए।',
    'en': 'Sorry, I could not read the figures in your register correctly, so I will not give you a wrong number. Please ask again.',
}
