# DSPy GEPA on the Plan-and-Execute planner (prototype, 25 Oct 2026)

`scripts/dspy_planner.py` optimises the `PLANNER` prompt in `server/agents/plan_execute.py` with DSPy GEPA. DSPy lives
only in `.dspy-venv` (Python 3.11, dspy 3.4.1); the app never imports it. `server/agents/plan_execute.py` is unchanged:
the proposed diff is at the end.

## Setup

- **Prompt rendered exactly as production.** `contexts` (app venv) logs in as each of the 8 mock didis and captures
  the real planner system prompt and her registers. A custom DSPy adapter sends system = instructions + her registers,
  then the conversation, then her question, with one retry carrying the `_check` problem, as `plan_execute.run` does.
  So the baseline row is the production planner.
- **Dataset** (`eval/dspy/planner_dataset.json`): 152 Hindi (some romanised) questions over D-001…D-008 in 13 types:
  savings, loans, income, land, wages, enterprise, Lakhpati, bank readiness, business plans (with `plan_changes`),
  general scheme questions, vague requests, two-table combos, and answers to an earlier follow-up (2-turn). The
  expected plan comes from code: tables, required fields and allowed fields, decision, business plan type (her own
  enterprise for "my business plan"), plan_changes, and whether a follow-up is due. The split is stratified 70/30:
  106 train (GEPA learned on 70 and selected on 36), 46 held out.
- **Metric** (code, no judge): 0 if the app would reject the plan (bad JSON, unknown source, table or field), or if
  it asks a follow-up when none is due (or misses one that is). Otherwise tool-call F1 over what `execute` would do
  (fetch per table, decide, build_business_plan) × (0.7 + 0.3 × field score). The field score is required-field
  recall × (1 − share of fields beyond the allowed set), which is the data-minimisation penalty. A wrong
  `plan_changes` costs ×0.8. Reading any record for a general question scores 0. GEPA gets the reasons as text
  ("Not needed (privacy failure): fetch wages_vbg", "farmer_id is more than this question needs", …).
- **LMs**: task model Nemotron 3.5 Lightning 30B (NVIDIA, `enable_thinking: false`); reflection model Nemotron 3 Ultra
  550B; Bhashini Gemma 4 26B through a small `dspy.BaseLM` adapter for the Dhruva API. Temperature 0, 2 threads,
  back-off on 429/5xx.

## Results (held-out, n = 46)

| Planner prompt | Nemotron 3.5 Lightning | Gemma 4 26B (Bhashini) |
|---|---|---|
| Production `PLANNER` (baseline) | 0.854 | 0.988 |
| **GEPA run 1** (`program-run1.json`, val 0.723 → 0.973) | **0.993** | 0.989 |
| GEPA run 2 (`program-run2.json`, val 0.908) | 0.970 | 1.000 |
| Run 1 condensed by hand (`program-proposed.json`) | 0.954 (1 rejected) | 1.000 |

No run had invalid JSON or model errors, except the one rejected plan in the hand-condensed row.

Nemotron baseline failures by type: income 0.25 (it read `wages_vbg` or `enterprise` instead of the DAR), land
0.72 and wages 0.75 (it asked a needless follow-up), follow-up answers 0.64 (it ran the bank-readiness decision for
"कर्ज़ के बारे में"), and extra loan fields (`disbursed_on`, `rate_pct`, `tenure_months`) or `farmer_id`. GEPA run 1
fixes all of these. Its only held-out miss is "मेरी बचत और कर्ज़ दोनों बताओ", where it read savings only.

Gemma was already near the ceiling, so the prompt barely matters for it. Run 1 does not make it worse (0.988 →
0.989).

The two GEPA runs ran at once by accident (the first logged a 429 but kept going). Both used the same seed and data.
Their spread (0.970 vs 0.993 on Nemotron) is a fair sense of run-to-run noise.

## Caveats

- 46 held-out cases means one case is about 2 points. Each number is one temperature-0 pass, and repeats come from
  the LM cache, so it is not a variance estimate.
- The labels encode conventions that GEPA learned: income comes from the DAR (not wages), business costs from the
  enterprise register, and no ids. If you disagree with one, change `BANK` in the script and re-run; the metric
  follows.
- GEPA's text has flaws I would not ship as-is. For general questions it says to set `purpose` to a scheme topic,
  which `_check` silently turns into `other_question`. It gives a loans "minimal set" (`emi, overdue_days,
  outstanding`) that over-asks for single-field questions, and it repeats some rules. I condensed it by hand
  (`program-proposed.json`), but that scored worse on Nemotron (an extra follow-up, and a Lakhpati plan naming
  `savings` on the wrong source). So the proposal below keeps run 1's wording and fixes only the `purpose` line.
  That one-line change is untested.
- No few-shot demos: source ids are random per workspace, so demos with hard-coded ids would mislead in production.
  GEPA optimises the instructions only.
- Not measured here: effect on the full agent (consent, answer quality). Re-run `scripts/eval_agents.py` after
  applying.

## Proposed diff to `server/agents/plan_execute.py`

Append to `PLANNER`, after the line `are general: answer them from scheme_topics, never from her registers.` and
before `Her registers (source id, table, fields):`. This is GEPA run 1's addition; the one changed line is marked.

```diff
 Questions about what something is or how a scheme works ("what is Panchsutra?", "what is a Lakhpati Didi?")
 are general: answer them from scheme_topics, never from her registers.
+
+When the question clearly specifies what data is needed (e.g., "monthly profit", "wages last year", "EMI amount"), do NOT ask a follow-up question. Instead, plan the exact tables and minimal fields required to answer. For income questions, use the dar table (income, expense fields). For wages questions, use the wages_vbg table (wages_paid, month fields). For loan EMI questions, use only the loans table fields directly asked for (emi, and at most loan_id, amount, outstanding, overdue_days, purpose, source) — do not include disbursed_on, rate_pct, or tenure_months unless she explicitly asks for them. For enterprise asset questions (cows/machines), use the enterprise table (units, unit_label).
+
+**Critical Rules for Specific Keywords:**
+- **"राम जी योजना" or "RAM G" or "wages"**: This refers to the VB-G RAM G wage scheme. Map to **purpose: "wages_question"**, **table: "wages_vbg"**, **fields: ["month", "wages_paid"]** (add "wages_pending" only if pending is implied). **Do NOT use savings or dar tables.**
+- **"किस्त" or "EMI" or "loan instalment"**: Map to **purpose: "loan_question"**, **table: "loans"**, **fields: ["emi", "overdue_days", "outstanding"]** (minimal set). Do NOT set decision to bank_linkage_readiness unless she explicitly asks "is my SHG ready for a bank loan?".
+- **"बचत" or "savings"**: Map to **purpose: "savings_question"**, **table: "savings"**, **fields: ["month", "amount"]** (add "cumulative" only if total savings asked).
+- **"आय" or "income" or "profit"**: Map to **purpose: "income_question"**, **table: "dar"**, **fields: ["month", "income", "expense"]**.
+- **"क्या मेरी कोई किस्त चूकी हुई है?" or "overdue"**: This is a loan_question needing loans table (overdue_days, outstanding, emi), NOT a bank_linkage_readiness decision.
+
+**Privacy Enforcement:** Only fetch fields strictly required to answer the specific question. Fetching wages_vbg for a savings question, or savings for a wages question, is a privacy failure.
+
+**General/Scheme Questions (CRITICAL):** If the question asks for a definition, explanation, or how a scheme works (e.g., "बैंक लिंकेज क्या होता है?", "पंचसूत्र क्या है?", "लखपति दीदी क्या है?"), this is a GENERAL QUESTION. You MUST:
+- Set `needs_records: false`
+- Set `items: []`
+- Set `purpose` to "other_question"          <- changed: GEPA wrote the scheme topic here, which is not a purpose
+- Set `scheme_topics` to include that topic
+- Set `decision: null` (NEVER set decision to bank_linkage_readiness or lakhpati_status for definition questions)
+- Answer from scheme_topics ONLY — reading her records for a definition question is a privacy failure.
+
+**Field Minimization Rules (from feedback):**
+- For farm questions (land/irrigation/crops), use agristack table with ONLY the fields needed: "irrigated" for irrigation status, add "crop_kharif", "crop_rabi", "land_acres" only if specifically asked. NEVER include "farmer_id" — it is never needed to answer her question.
+- For savings questions: use "month", "amount" only; add "cumulative" ONLY if she asks for total savings.
+- For wages questions: use "month", "wages_paid"; add "wages_pending" ONLY if pending/overdue is implied.
+- For loan questions: use "emi", "overdue_days", "outstanding" as minimal set; never include disbursed_on, rate_pct, tenure_months unless explicitly asked.
+- For income questions: use "month", "income", "expense" from dar table.
+
+**Enterprise Expense Questions:** When she asks about business expenses ("धंधे में खर्च", "business expenses", "monthly cost"), this is an **enterprise_question**. Use the **enterprise** table with fields **"monthly_cost"** (and "units", "unit_label" if needed for context). Do NOT use the dar table for expense-only questions — dar is for income/expense pairs when she asks about profit or both income and expense together. Fetching dar for an expense-only question is a privacy failure.
+
+**Decision Table Usage (CRITICAL):** Only set `decision` to `bank_linkage_readiness` or `lakhpati_status` when she EXPLICITLY asks:
+- "Is my SHG ready for a bank loan?" / "क्या मेरा समूह बैंक लिंकेज के लिए तैयार है?"
+- "Am I a Lakhpati Didi?" / "क्या मैं लखपति दीदी हूँ?"
+NEVER set decision for: loan balance questions, EMI questions, overdue questions, savings questions, or general scheme explanations. Those are `loan_question`, `savings_question`, etc. with their own tables.
 Her registers (source id, table, fields):
 %s"""
```

(The `%s` placeholders and the `PLANNER %` call are unchanged; the added text has no `%` signs.)

## Applied (25 Oct 2026, 19:32) and checked end to end

Applied to `server/agents/plan_execute.py` with the `purpose` fix. `pytest`: 113 passed, 6 skipped.
`scripts/eval_agents.py --agents plan_execute` (10 cases in eval/cases.json, Ragas with Nemotron Ultra):

| Model | Run | Faithfulness | Accuracy | Tool F1 | Done |
|---|---|---|---|---|---|
| Gemma 4 26B | before (16:51) | 0.57 | 0.70 | 0.94 | 10/10 |
| Gemma 4 26B | after (19:53) | 0.81 | 0.68 | 0.94 | 10/10 |
| Nemotron 3.5 Lightning | after (19:43), no earlier run | 0.85 | 0.50 | 0.84 | 9/10 |

Gemma made the same tool calls before and after, so its faithfulness and accuracy changes come from the writer and
the judge, not the planner. Tool F1 is below 1 only for:
- the dairy plan, which scores 0.4 for every agent and model because the eval counts the plan's own register reads
  as extra calls;
- Nemotron's Lakhpati run, which timed out at NVIDIA ("did not answer in time"). Its other nine plans were right.

The 10-case eval is too small to show the planner gain; the 46-case held-out set above is the planner measurement.
The writer still slips: Gemma said savings were ₹1,30,800 (the register says ₹12,000), and Nemotron's loans answer
said it needed information instead of answering.

## Opus 5.5 as the teacher, through the Claude Code CLI (26 Oct 2026)

`--reflection claude-cli/<model>` makes GEPA's teacher (the reflection LM) any Claude model the local `claude` CLI is
logged in to. It calls `claude -p --model <model> --tools '' --output-format json --no-session-persistence
--strict-mcp-config` in an empty temp folder, with this session's `CLAUDE_CODE_*` variables stripped. It backs off on
rate limits, stops with "run `claude auth login`" if the CLI is logged out, and reports the cost at the end.
`CLAUDE_CLI=/path/to/claude` points it at another binary.

- **Gemma 4 26B as the student** (it is now the primary model): GEPA kept the production prompt. Gemma scores 0.972
  on the 36 validation questions with it, so about 85 of 86 iterations sampled a minibatch with no mistakes and
  GEPA skipped the rewrite. The one rewrite Opus produced was no better. Teacher cost $0.59. On the held-out set,
  production 0.988, run 1 0.989, run 2 and the hand-condensed prompt 1.000: for Gemma the planner prompt is already
  at the ceiling of this dataset. More gains need harder or more varied questions, not a new prompt.
- **Nemotron as the student**: stopped early (NVIDIA was answering in 10–17 s per call), after one rewrite; no result.

## Reproduce

```bash
.venv/bin/python scripts/dspy_planner.py contexts
.dspy-venv/bin/python scripts/dspy_planner.py dataset
.dspy-venv/bin/python scripts/dspy_planner.py eval --model nvidia/nemotron-3.5-lightning-30b-a3b
.dspy-venv/bin/python scripts/dspy_planner.py optimize --budget 400
.dspy-venv/bin/python scripts/dspy_planner.py eval --model bhashini/google/gemma-4-26B-A4B-it --program eval/dspy/program-run1.json
```

Each `eval` run appends to `eval/dspy/results.json` with per-case scores and feedback. GEPA's logs and candidates
are in `eval/dspy/gepa/`. A 400-call GEPA run took about 30 minutes on the free tier.
