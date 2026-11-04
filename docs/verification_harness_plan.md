# Plan: a verification harness for tool calls and answers, and an experiment to pick the best agent flow

Status, 28 Oct 2026: phases 1–3 are built (`server/verify/`, `scripts/verify_faults.py`,
`tests/test_m13_verify.py`); phases 4–6 are still proposals. Results so far:

- **Fault suite:** every injected fault is caught, with 0 false alarms on 63 good answers and 89 good plans.
  - Gate C: figure off, wrong register, wrong period, flipped verdict, dropped "could not read", English sentence.
  - Gate A: extra register, ID field.
  - The good cases are templated, so the live check below is the real false-alarm test.
- **Live check (17 eval cases, Gemma, Plan-and-Execute):** no false alarms on real answers. Before the fix, the
  check showed that "मेरी कुल बचत कितनी है?" got ₹1,30,800 (the register says ₹12,000) and passed the critic.
  - Cause: the planner read `cumulative` without `month`, so `facts.summarise` added twelve running balances
    together.
  - Fix: `summarise` no longer adds up balances in a monthly register, and gate A's `needs_month` rule reads the
    month with a running balance.
  - After the fix, both savings answers were right.
- **Phase 4 (built):** the step-5 LLM verifier (`server/verify/llm_check.py`), behind `verify=code+llm` (a select
  in the chat's operator panel; the default stays `code`).
  - It runs only for writer-worded answers, and only when gate C has not already spent its one LLM call on a rewrite.
  - A failed verdict falls back to the run's code-worded answer; a reply that isn't JSON passes (it fails open).
  - Fault suite (`scripts/verify_faults.py --llm <model>`): the code checks catch 0 of 99 faults where every figure
    is real (46 answers to another question with that register read, 46 dodges, 7 half answers). With Gemma 4 26B
    as the verifier: 99/99 caught, 0 false alarms on 53 good answers. These templated faults are blatant, so
    recall on real, subtler slips will be lower. Nemotron Ultra timed out at NVIDIA and wasn't measured.
  - Live (17 cases, Gemma): no false alarms. Writer-worded turns go from 2 to 3 LLM calls, about +0.3 s
    (0.85–2 s per turn); code-worded answers skip it.
  - Recommendation: keep `code` as the default and turn on `code+llm` for pilots. Gather real-answer disagreements
    from the async judge before making it the default.
- **Phases 5–6 (done):** 9 flows, 80 hard cases, 1,360 runs on Gemma; see
  [eval/flows_report.md](../eval/flows_report.md).
  - Plan-and-Execute with `verify=code` stays the default: 0.97 verified-correct, p95 2.8 s, 1.85 LLM calls.
  - The verifier, self-review and plan-reviewer add-ons tie it, at extra cost.
  - ReWOO scores 0.79 and ReAct 0.20.
  - The remaining misses are D-008's Tamil language path.

## 1. Goal

Before the didi hears anything, the agent should be able to show, in code where possible, that:

1. every tool call was the right one: the right register, only the fields needed, under a consent that covers
   them, for a purpose the source allows;
2. every tool result was used correctly: totals recomputed, nothing read but ignored, and an empty or blocked read
   said honestly;
3. the answer is supported: each figure traces to a fetched or computed value from the register it names, a
   decision's verdict matches the decision table, and a plan's or projection's figures match the code result;
4. the answer is the right answer: it answers what she asked (or asks one needed follow-up), in Hindi she can
   hear, and does not reveal setup details.

Then we measure which agent design (Plan-and-Execute, ReAct, ReWOO, native tool calling, self-review, hybrids)
gives the most verified-correct answers per second, per consent prompt and per rupee, and keep that one.

## 2. What exists today, and the gaps

| Stage | Today | Gap |
|---|---|---|
| Plan / tool choice | `plan_execute._check` + `tools._items`: sources, tables and fields exist; decision, plan and projection routing; profile tables dropped | Only checks that the plan is well formed. Reading `wages_vbg` for an income question passes. No field-minimality rule. ReAct and native tools have no equivalent before calling `request_consent`. |
| Consent / fetch | `gateway.fetch`: consent, turn (or carried follow-up), purpose policy, paused source, scope, group data; injection-safe rows | Good. Not visible to the answer check (which register each number came from). |
| Results | `facts.summarise`: exact totals by code; `ctx.known` holds every fetched or computed number | `known` is a flat set of numbers with no provenance (table, field, period), so "₹12,000 from the loan register" passes if ₹12,000 is anyone's figure. |
| Answer | `writer.usable`: degenerate, too long, asks permission, leaks setup, speaks as her, English. `critic.check`: every figure in `known`, else one LLM rewrite, else withhold. Code-worded answers (decision, business plan, projection, earnings) are trusted. | Numbers only. Does not check the verdict ("आप लखपति दीदी हैं") against the decision outcome, the period ("पिछले 12 महीने"), the register named, units (acres vs ₹), or that the question was answered. |
| After delivery | `judge.py`: Nemotron Ultra scores grounded, answers-question, Hindi-only, minimal-data on the Langfuse trace, in the background | Too late to stop a bad answer. Never calibrated against labels. |
| Offline | `scripts/eval_agents.py` (Ragas: faithfulness, accuracy, tool F1, chart_ok); `scripts/dspy_planner.py` (exact plan metric, 152 cases); `scripts/dspy_projection.py` (208 cases) | Agents compared on 10–17 cases, one pass each. Verifiers are never tested for whether they catch faults. |

Agent results so far (Gemma 4 26B, `eval/cases.json` 10 cases):

| Agent | Tool F1 | Accuracy | Faithfulness | LLM calls | Seconds |
|---|---|---|---|---|---|
| Plan-and-Execute | 0.94 | 0.70 | 0.57 | 1.7 | 1.6 |
| ReWOO | 0.77 | 0.53 | 0.36 | 2.3 | 2.0 |
| ReAct | 0.45 | 0.15 | 0.27 | 5.7 | 11.7 |
| Direct (no tools) | 0.20 | 0.10 | 0.12 | 1.0 | 0.6 |

Plan-and-Execute wins today, but these runs had no step verification; ReAct in particular may gain the most from
it. The experiment in section 5 settles that.

## 3. The harness

One module, `server/verify/`, used by every agent through the run context. Each check is a small function that
returns a `Finding` (`name`, `stage`, `ok`, `severity` `block` | `repair` | `warn`, `detail_en`, `feedback` for
the LLM). Each stage returns a `Verdict`: `deliver` | `repair` | `fallback` | `withhold`, with its findings. Every
verdict is emitted as a `verification` event (trace pane and Langfuse), so a reviewer sees why an answer passed or
was changed.

### 3.1 Gate A: before a tool call (plan or action)

Runs on the Plan-and-Execute plan, on each ReAct or native `request_consent` / `fetch_records` call, and on each
ReWOO step, before any consent card is shown.

- **Well formed:** today's `_check` / `_items`, moved here so all agents share it.
- **Intent to register map (code, no labels needed at run time).** A table maps each purpose to the registers that
  can answer it: `income_question` → `dar`; `wages_question` → `wages_vbg`; `farm_question` → `agristack`;
  `savings_question` → `savings`; `loan_question` → `loans`; `enterprise_question` → `enterprise`. Decisions,
  plans and projections bring their own. A register outside the purpose's set is a `repair` finding: "reading
  wages_vbg for an income question; income comes from dar". The planner dataset labels already encode this map;
  write it once and test it against `eval/dspy/planner_dataset.json`.
- **Field minimality:** ids (`farmer_id`, `job_card_no`, `loan_id`, `enterprise_id`) are never needed. More than N
  fields from one table, or dates and tenure for a balance question, is a `repair` finding with the allowed list.
  These are the same reasons `dspy_planner.score()` gives GEPA, reused at run time without the gold label.
- **Purpose and policy pre-check:** the department's purpose policy and paused sources, checked before she is asked.
  Today this is found only at fetch time, after she has already said yes.
- **Follow-up sanity:** a follow-up when the scenario is complete (the projection guard), or no follow-up when the
  amount is missing.
- **Action:** `repair` sends the findings back as one retry (the `problem` suffix Plan-and-Execute already uses), then
  drops the offending items. `block` stops the call. Nothing reaches the consent card unverified.

### 3.2 Gate B: after a tool result

- **Provenance ledger.** `ctx.known` becomes `ctx.facts`: number → `{register, field, period, op, consent_id}`,
  filled by `facts.summarise`, `calculate`, `decide`, `build_plan`, `projection` and `earnings`. This one change
  makes Gate C precise.
- **Empty, blocked and partial reads** are recorded as obligations: the answer must say what could not be checked.
- **Unused reads:** a register fetched but never used in the answer is a `warn` (data minimisation, feeding the
  offline metric).

### 3.3 Gate C: before the answer is delivered

Ordered cheapest first; stop at the first `withhold`.

1. **Form (code):** today's writer guards: degenerate, length, permission-asking, setup leaks, English, speaking as her.
2. **Figures (code):** every number in the answer is in `ctx.facts` (today's critic). New: the register named
   near a number ("बचत रजिस्टर के अनुसार ₹12,000") must be that number's register; units must match (acres
   are not ₹); a period phrase ("पिछले 12 महीने", "इस महीने") must match the fact's period.
3. **Verdicts (code):** if a decision ran, the answer's verdict words must match `outcome_label` (no "आप लखपति
   दीदी हैं" when the outcome is ON_TRACK). If a plan, projection or earnings result exists, its headline figures
   must appear unchanged. Code-worded answers stay trusted but are still checked, so a wording bug is reported.
4. **Obligations (code):** every Gate B obligation (refused, blocked, no rows) is mentioned.
5. **Answers the question (LLM, only when needed).** A short verifier call, in the style of Chain-of-Verification
   and CRITIC, only for writer-written answers that passed 1–4. It sees her question, the facts used and the
   answer, and returns `{answers_question, wrong_claim, missing}` as JSON. It costs one more LLM call, so it runs
   only for writer-worded answers, never for code-worded ones; on Gemma that is about 1 s.
6. **Action:**
   - a `repair` finding gets one rewrite with the findings as feedback (as the critic does today);
   - if the rewrite still fails, use the code answer (`plain_answer`, `decision_answer`, the projection
     template) rather than withholding, whenever code can word it;
   - withhold only when nothing verified can be said.

### 3.4 Budgets and modes

- **Modes:** a new pref, `verify`, takes `off` | `code` | `code+llm`.
  - It defaults to `code`, which costs no LLM calls.
  - `code+llm` adds the Gate C step 5 verifier.
- **Compare view:** the existing compare view can run the same question under different modes side by side.
- **Repair budget:** one repair per gate per turn. Gate C may never add more than one LLM call; that keeps
  latency within the run budget.
- **The async judge stays,** retargeted to calibrate the code verifiers: every disagreement between the judge and
  Gate C is logged as a case to review.

## 4. Verifying the verifiers (fault injection)

A check is only useful if it catches real faults and passes good answers. Build
`scripts/verify_faults.py`, which takes known-good runs (stored traces from the evals) and injects faults:

- **Plan faults:**
  - swap the register;
  - add an id field;
  - add an extra table;
  - drop the decision;
  - set a follow-up on a complete projection.
- **Answer faults:**
  - change a figure by ±1–20%;
  - swap two figures;
  - attribute a figure to the wrong register;
  - wrong unit;
  - wrong period;
  - flip the decision verdict;
  - drop the "could not read loans" sentence;
  - add an English sentence;
  - answer a different question.

For each check, report catch rate (recall) on injected faults and false-alarm rate on untouched good answers.
Targets: at least 95% recall for code checks on figure and verdict faults, at most 2% false alarms; the LLM
verifier is kept only if it adds at least 10 points of recall on "answers a different question" faults at
under 5% false alarms. This test set runs in CI alongside pytest, with no LLM calls except for step 5.

## 5. Experiment: which agent flow is best

### 5.1 Flows to compare (all with the same tools, prompts tuned once, on Gemma 4 26B; Nemotron as a check)

| # | Flow | What it tests |
|---|---|---|
| F1 | Plan-and-Execute + harness (`code`) | the current default, verified |
| F2 | F1 + Gate C LLM verifier (`code+llm`) | is the extra call worth it |
| F3 | ReAct + Gate A on every action + Gate C | does per-step verification rescue ReAct |
| F4 | ReWOO + harness | plan once, no adaptation |
| F5 | Native tool calling + harness (Nemotron only; Gemma on Bhashini has no native tools) | the model's own tool API |
| F6 | Self-review (Self-Refine / Reflexion style): F1's answer → LLM critique → revise | LLM review vs code review |
| F7 | Plan → plan reviewer LLM → execute | LLM plan review vs Gate A code rules |
| F8 | Hybrid: F1, and on a Gate B surprise (no rows, blocked, partial) or Gate C failure, a short ReAct repair loop with at most 2 steps | adapt only when needed |
| F9 | Router: intent classifier → code-worded paths (decision, plan, projection, earnings) or F1 | how much can be code |

### 5.2 Test set (about 450 cases, three families, 30% held out)

- **The existing planner and projection datasets:** 152 + 208 cases with code-computed expectations
  (`eval/dspy/`).
- **`eval/cases.json`:** 17 cases with reference answers.
- **A new hard set, about 80 cases:**
  - multi-turn follow-ups (including consent carry-over);
  - consent refused or partial;
  - empty registers (D-006 has no enterprise, D-002 no farm record);
  - paused sources and purpose policy;
  - group data asked by a member;
  - prompt-injection rows;
  - vague questions;
  - questions mixing two registers;
  - Hinglish and number words.

### 5.3 Metrics (all per case, in code where possible)

- **Primary:** verified-correct rate. The answer is correct against the expectation, has zero ungrounded or
  misattributed figures, and stays inside the minimal data.
- **Safety:**
  - wrong figures delivered (must be 0);
  - withheld rate;
  - data read beyond need;
  - consent prompts per question.
- **Cost:**
  - LLM calls;
  - p50/p95 seconds;
  - tokens;
  - repair rate per gate.
- **Secondary:** Ragas faithfulness and accuracy, and judge scores. Report these, but don't let them decide.

### 5.4 Protocol

- **Repeats:** 3 runs per flow and case at the production temperature, so variance is visible. Paired comparison
  per case; 95% bootstrap intervals on the primary metric.
- **Rate limits:** free-tier NVIDIA and Bhashini with low concurrency and back-off, as `scripts/eval_agents.py`
  does. Planner-level flows can run offline through the DSPy harness (no consent server needed); end-to-end flows
  run through the playground with consent auto-answered from the case (yes, no or partial).
- **Decision rule:** keep the flows on the Pareto front of verified-correct rate vs p95 seconds and consent
  prompts. Among those, prefer fewer LLM calls. A flow must have zero wrong figures delivered to qualify.

### 5.5 Then optimise the winner

Use GEPA (`scripts/dspy_planner.py` with Opus 5.5 or Nemotron Ultra as teacher) on the winning flow's prompts:

- the planner;
- the writer;
- the Gate C verifier prompt, if F2 wins.

The harness's findings are the feedback text, which is what GEPA used well in the planner and projection runs.
Check that the optimised prompts don't overfit by re-running the held-out 30% and the fault-injection suite.

## 6. Phases

| Phase | Work | Done when |
|---|---|---|
| 1 | `server/verify/`, the Finding and Verdict types and the `verification` event; move `_check`, the writer guards and the critic behind it with no behaviour change | Suite passes; traces show verdicts |
| 2 | Provenance ledger (`ctx.facts`), Gate A intent and minimality rules, Gate C attribution, units, period, verdict and obligations | New unit tests; planner dataset shows Gate A agreeing with the labels |
| 3 | Fault-injection suite and its report | Recall and false-alarm targets met for code checks |
| 4 | Gate C LLM verifier behind `verify=code+llm`; the judge as calibrator | Measured on the fault suite |
| 5 | Flows F1–F9 runnable under one runner flag; the hard test set | `scripts/eval_flows.py` produces the comparison table and Pareto chart |
| 6 | Run the experiment, pick the flow, GEPA it, set the default | Report in `eval/flows_report.md`; default changed only if it wins |

Phases 1–3 are independent of the experiment and pay off on their own: the biggest risk today is a confident
wrong figure or a wrong verdict, and these phases catch both in code. Phase 5's flow plumbing can start alongside
phase 2.

## 7. Risks

- **False alarms annoy more than they protect:** a strict attribution check that withholds good answers is worse
  than today. The fault suite's false-alarm target and the fallback to code-worded answers guard against this.
- **Latency:** the LLM verifier and repair loops add seconds on a voice turn. Code checks first; one LLM call at
  most; measure p95.
- **Overfitting to our own labels:** the intent-to-register map and the datasets encode our conventions (income
  from the DAR). Review them with the NRLM team before they become rules that block answers.
- **Bhashini Gemma has no native tool calls,** so F5 runs only on Nemotron and isn't a like-for-like comparison.
