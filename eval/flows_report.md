# Which agent flow to keep: results (29 Oct 2026)

Phases 5–6 of [docs/verification_harness_plan.md](../docs/verification_harness_plan.md).

- **Model:** Gemma 4 26B on Bhashini.
- **Test set:** 80 hard cases from the eight mock didis.
- **Runs:** 1,360 end to end through the playground.
- **Scoring:** all in code (`scripts/eval_flows.py`).
- **Raw data:** runs in `eval/flows/`, misses in `eval/flows_misses.json`.
- **Chart:** accuracy against p95 seconds, in [flows_report.html](flows_report.html).

## Result

| Flow | n | Verified-correct (95% CI) | vs pe_code (95% CI) | Wrong figures | Read beyond need | Consent cards | LLM calls | p50 s | p95 s |
|---|---|---|---|---|---|---|---|---|---|
| **pe_code**: Plan-and-Execute + code checks ★ | 240 | **0.97** (0.95–0.99) | – | 0 | 0 | 0.91 | 1.85 | 1.6 | 2.8 |
| pe_llm: + LLM "answers her question" verifier | 240 | 0.97 (0.95–0.99) | +0.00 | 0 | 0 | 0.91 | 2.63 | 2.0 | 3.6 |
| self_review: + self-critique and revise | 80 | 0.97 (0.94–1.00) | +0.00 | 0 | 0 | 0.91 | 2.52 | 1.8 | 3.5 |
| plan_review: + LLM plan reviewer | 80 | 0.97 (0.94–1.00) | +0.00 | 0 | 0 | 0.91 | 2.88 | 2.7 | 4.5 |
| hybrid: ReAct only when no valid plan ★ | 80 | 0.97 (0.94–1.00) | +0.00 | 0 | 0 | 0.91 | 1.85 | 1.6 | 2.8 |
| pe_off: Plan-and-Execute, harness off | 240 | 0.97 (0.95–0.99) | −0.00 (−0.01 to +0.00) | 0 | 0 | 0.93 | 1.87 | 1.6 | 3.1 |
| rewoo_code: ReWOO + harness | 240 | 0.79 (0.74–0.84) | −0.18 (−0.24 to −0.14) | 0 | 0 | 0.95 | 2.12 | 1.7 | 4.1 |
| react_code: ReAct + harness | 80 | 0.21 (0.12–0.30) | −0.76 (−0.85 to −0.68) | 0 | 2 | 0.51 | 5.25 | 6.8 | 27.6 |
| react_off: ReAct, harness off | 80 | 0.20 (0.11–0.29) | −0.78 (−0.86 to −0.68) | 0 | 3 | 0.53 | 5.22 | 5.4 | 27.6 |

★ marks the Pareto front: no other flow is at least as correct and at least as fast. The differences are paired
per case, with 2,000-sample bootstrap intervals.

- **Verified-correct** means four things hold:
  - the answer states the expected figure, verdict or follow-up, and mentions any register it couldn't read;
  - it has no figure that isn't hers (or that comes from a register she refused or that was blocked);
  - it read nothing beyond the case's registers;
  - the run finished.
- **The 80 cases cover:**
  - savings, loans, wages, land, income, turnover, Lakhpati status and a savings projection for each didi;
  - Hinglish questions;
  - consent refused, consent given for some registers only, and a paused source;
  - bank readiness for an office bearer, and for a member (group data she can't open);
  - empty registers;
  - a two-turn follow-up, general scheme questions, and vague questions.

## Recommendation

1. **Keep Plan-and-Execute as the default.** It is the most accurate (0.97) and the fastest (p95 2.8 s). It uses
   the fewest LLM calls (1.85 per question), never gave a wrong figure, and never read beyond need.
2. **Keep `verify=code` on.** It costs no extra LLM call and no measurable latency. On this set it doesn't change
   average accuracy. Its value is safety:
   - the fault suite: 100% of injected faults caught, 0 false alarms;
   - the live check that found the summed-balances bug (₹1,30,800 for ₹12,000);
   - refusing a paused source or a forbidden purpose before her consent card.

   The `summarise` fix for that bug applies to every flow, which is why `pe_off` scores as well here.
3. **Don't adopt the LLM add-ons for now.** The verifier (`pe_llm`), self-review and the plan reviewer added 0.7–1.0
   LLM calls and 0.6–1.7 s at p95 per question, and changed nothing on this set. Gemma's Plan-and-Execute answers are
   already right or wrong for reasons these reviewers don't see (below). Revisit them with real transcripts. Keep
   `code+llm` as a pilot setting.
4. **Hybrid is harmless but unused.** The planner always returned a valid plan, so ReAct never took over. Keep it
   in code as a safety net; it doesn't need to be the default.
5. **ReWOO and ReAct stay off the default path:**
   - **ReWOO (0.79):** its solver never states the Lakhpati verdict (21 misses), and it answers projection questions
     with her current balance (24).
   - **ReAct (0.20, p95 27.6 s):**
     - no answer, from errors or timeouts (18–22 per flow);
     - its "thought" text leaked into the answer (17);
     - it asked permission in words instead of calling consent (9–13);
     - figures were withheld by the critic (8–9).
   - **The harness doesn't rescue ReAct** (0.20 → 0.21). Its failures come before any tool call the gates could
     check.
6. **No GEPA round needed for the winner now.** Its planner prompt was already optimised (planner GEPA, 0.854 →
   0.993 on Nemotron, ≈1.0 on Gemma). Its only misses here are not prompt problems (below).

## What Plan-and-Execute still gets wrong (6 of 240 runs)

All six belong to D-008, Lakshmi, a Tamil speaker, in every repeat:

- **Lakhpati status:** the decision table's answer reaches her in **English** ("Lakshmi, Your Digital Aajeevika
  Register shows…"). `decision_answer` writes Hindi only when `llm_language == 'hi'` and English otherwise, and
  nothing translates it to Tamil.
- **Savings projection:** "Sorry, no answer right now", every time.
- **Other answers to her:** some come in Tamil but start "लட்சுमी जी" with a Hindi "जी", and Gate C used to append
  Hindi sentences to Tamil answers. That is fixed now: the obligation check is skipped for languages it can't read.

These were language-path bugs, not flow or prompt problems. **Fixed (29 Oct):**

- `runner.in_her_language` translates any English answer into her language, which covers code-worded answers and code
  fallbacks. Amounts and her name are kept out of NMT.
- For a language with its own script, it also translates a Hindi answer, such as the planner's follow-up.
- It drops a Hindi "जी" from answers in another script.
- It runs after the agent and again after Gate C.
- **Re-run:** all 10 D-008 cases, 3 times each (`eval/flows/d008-fix.json`). 30 of 30 verified-correct, all in Tamil,
  none with "जी".
- The rate is 0.97 → about 1.00 for `pe_code` on this set (the table above is from before the fix).
- The Lakhpati verdict is scored on its Tamil word ("நெருங்கு…", approaching).

## Caveats

- **Template-generated test set.** The cases come from templates over mock data. Real didis' speech (ASR errors,
  long or mixed questions) will be harder. The flows should be re-ranked on real transcripts when there are some.
- **One model.** Only Gemma was run. Native tool calling (needs Nemotron) wasn't part of this comparison.
- **Single runs for the add-ons.** Self-review, the plan reviewer, the hybrid and ReAct ran once per case. Their
  intervals are wide, but the gaps to Plan-and-Execute are far outside them, and the add-ons tie it exactly.
- **Scorer fixes before the final numbers.** Its first version counted the deposit she named and her SHG's meeting
  figures as "wrong", and knew only one phrasing for "could not check". Both were fixed, and every run was re-scored
  from the saved answers.
