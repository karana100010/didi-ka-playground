# Savings projections: dataset, designs and GEPA on Gemma (27 Oct 2026)

Offline prototype in `.dspy-venv`. No app code changed, nothing committed.

- Code: `scripts/dspy_projection.py` (dataset, programs, metric, GEPA) and `scripts/projection_report.py`
  (tables and charts).
- Charts: open [projection_charts.html](projection_charts.html) for the GEPA score after each round and six
  held-out sample charts (expected vs model).

## Dataset (`projection_dataset.json`, 208 questions)

- **Source.** Her real savings register (`projection_facts.json`, from `data/mock`): balance at 2026-09 and her
  regular monthly deposit. These run from ₹1,500 at ₹100 a month (D-006) to ₹20,500 at ₹250 a month (D-008).
- **Kinds, 26 per didi:**
  - ₹x more a month (40)
  - ₹y a month instead (24)
  - x less a month (8)
  - double (16)
  - % increase (16)
  - months to reach a target, at her current rate (16) or with more each month (16)
  - no period given, so the answer must say 12 months (24)
  - an annual interest rate she names (16)
  - a one-time deposit, alone or with more each month (16)
  - the amount is missing, so ask a follow-up (16)
- **Styles:** 105 with digits or ₹ amounts, 50 with numbers in words (डेढ़ सौ, दो हज़ार, डेढ़ साल, …), 53 romanised
  Hinglish.
- **Expected figures, all computed in code.** For each month starting 2026-10:
  - the "अभी की बचत से" series is balance + k × current deposit;
  - the "बढ़ी हुई बचत से" series is balance(k) = round(balance(k−1) × (1 + rate/1200) + new deposit), starting
    from balance + one-time deposit;
  - the projected balance is the last value of the new series, and the added savings is new minus current;
  - for a target question, the period is the months until the new series first reaches the target.
  SHG savings earn no interest unless the question names a rate.
- **Split, stratified by kind:** 104 train, 41 validation, 63 held-out.

## Two designs, three starting prompts each

- **full**: one Gemma call returns `{"params", "chart", "answer"}`, so the model does the arithmetic and writes the
  chart and the Hindi answer.
- **extract**: Gemma returns only the parameters `{follow_up, new_deposit, months, target, annual_rate, lump_sum}`.
  Code computes every figure, the chart and the Hindi answer from a template. This is the likely production design.

Starting prompts:
- **naive**: one sentence plus the JSON schema.
- **planner-style**: written like the app's `PLANNER`/`WRITER` prompts (schema, "use figures exactly", follow-up
  rule, Hindi only).
- **hand**: hand-written, with the number-word table, a rule for each kind of question, the exact formula and a
  worked example.

## Metric (code, with a written reason for every point lost)

The combined score weights four parts:

| Part | Weight | What it checks |
|---|---|---|
| Parameters | 0.30 | new deposit, period or target, rate and one-time deposit, each against the expected value |
| Figures | 0.30 | the answer states the projected balance and the added savings (±₹1); months and balance for targets; "12 महीने / एक साल" when no period was given |
| Chart | 0.25 | line type with two series; right length; each series matches the formula (±₹1) |
| Hindi | 0.15 | no English words in the answer |

- **Follow-up cases** score full marks only if a follow-up is asked, the answer is just that question with no
  figures, and there is no chart.
- **A wrongly asked follow-up** scores 0 on parameters, figures and chart.
- **GEPA's feedback** is that reason text, for example: `Series "बढ़ी हुई बचत से" is wrong from month 3: expected
  12815 (formula …)`.

## Results on the 63 held-out questions (Gemma 4 26B on Bhashini)

| Design | Prompt | Score | Parameters | Figures | Chart | Hindi |
|---|---|---|---|---|---|---|
| full | naive | 0.174 | 0.08 | 0.00 | 0.00 | 1.00 |
| full | planner-style | 0.772 | 0.88 | 0.67 | 0.66 | 0.95 |
| full | hand | 0.921 | 0.99 | 0.89 | 0.84 | 0.98 |
| full | GEPA ← naive (Opus) | 0.919 | 0.95 | 0.86 | 0.91 | 1.00 |
| full | GEPA ← planner-style (Opus) | **0.943** | 0.99 | 0.87 | 0.94 | 0.98 |
| full | GEPA ← planner-style (Nemotron Ultra) | 0.930 | 1.00 | 0.86 | 0.88 | 1.00 |
| full | GEPA ← hand (Opus) | 0.935 | 1.00 | 0.91 | 0.85 | 1.00 |
| extract | naive | 0.457 | 0.40 | 0.36 | 0.38 | 0.91 |
| extract | planner-style | 0.856 | 0.87 | 0.80 | 0.84 | 0.95 |
| extract | hand | 0.909 | 0.91 | 0.91 | 0.91 | 0.94 |
| extract | GEPA ← naive (Opus) | **1.000** | 1.00 | 1.00 | 1.00 | 1.00 |
| extract | GEPA ← planner-style (Opus) | **1.000** | 1.00 | 1.00 | 1.00 | 1.00 |
| extract | GEPA ← hand (Opus) | **1.000** | 1.00 | 1.00 | 1.00 | 1.00 |

Interest questions (n = 5 held-out), across every full-design prompt before and after GEPA: 0.58–0.64. Every
extract-design prompt after GEPA: 1.00.

| GEPA run (350 rollouts, minibatch 5) | Validation start → best | Candidates | Teacher cost |
|---|---|---|---|
| full ← naive, Opus 5.5 | 0.179 → 0.955 | 8 | $0.91 |
| full ← planner-style, Opus 5.5 | 0.699 → 0.914 | 7 | $0.66 |
| full ← planner-style, Nemotron Ultra | 0.709 → 0.912 | 7 | $0 (free tier) |
| full ← hand, Opus 5.5 | 0.830 → 0.941 | 7 | $1.13 |
| extract ← naive, Opus 5.5 | 0.419 → 1.000 | 3 | $0.16 |
| extract ← planner-style, Opus 5.5 | 0.917 → 1.000 | 2 | $0.06 |
| extract ← hand, Opus 5.5 | 0.893 → 1.000 | 2 | $0.08 |

### How the score moves over GEPA rounds (see the first chart in projection_charts.html)

- **Most of the gain comes in the first accepted rewrite**, after about 50 rollouts. For example, extract ← naive
  went 0.42 → 0.92 at 51 rollouts and → 1.00 at 107; full ← naive went 0.18 → 0.83 in the first round.
- **The full design plateaus at 0.91–0.96 on validation** and on held-out. Later rounds in the full runs found
  candidates that scored lower (0.82–0.93) and were not kept. GEPA keeps the Pareto best, so the plateau is the
  model's ceiling, not the optimiser's.
- **The extract runs hit 1.00 and then stop**: every minibatch was perfect, so there was nothing left to fix.
- **Teacher choice barely matters here.** Nemotron Ultra (free) reached the same level as Opus 5.5 from the same
  start (0.912 vs 0.914 on validation, 0.930 vs 0.943 held-out). Opus wrote longer, more structured prompts.

### What GEPA changed in the prompt text

Prompt length, before → after:

| Run | Start | After GEPA |
|---|---|---|
| full ← naive | 511 | 7,019 |
| full ← planner-style | 1,134 | 6,213 |
| full ← hand | 3,085 | 9,166 |
| extract ← naive | 298 | 5,860 |
| extract ← planner-style | 700 | 4,135 |
| extract ← hand | 1,564 | 4,697 |

The added text is consistent across runs:

1. **A follow-up rule.**
   - Ask only when no amount, multiple, %, one-time deposit or target is given.
   - A missing period is never a reason to ask: use 12 months.
   - The rule lists the needless follow-ups it must not ask ("क्या आप ₹200 ही जमा करती रहेंगी?", "कितने समय में?")
     and warns that asking one "scores zero".
2. **A full Hindi number-word table and phrase rules.**
   - Number words from एक to हज़ार.
   - "X और / ज़्यादा / X महीना बढ़ा दूँ" means current + X; "हर महीने X जमा करूँ" without और means X.
   - Romanised forms.
3. **The formula, with worked examples taken from the feedback's real register figures.** For example "B=15450,
   D=150, 7% interest, 3 months: 15690, 15932, 16175", compounding step by step with rounding.
4. **Chart rules.**
   - Exactly two named series, even when they are identical.
   - x starts at the register's next month and stops at the month a target is reached.
   - Month arithmetic examples ("k=23 is 2028-08").
5. **Answer rules.**
   - Devanagari even when she typed romanised Hindi.
   - Write her Tamil name in Devanagari.
   - State the last value and the difference; say "एक साल" when it took the default.

Two of GEPA's rules are not quite right:
- The full ← naive prompt tells the model to put `months: null` when no period was given, while still using 12
  for the chart. That costs it parameter points.
- Its target formula `ceil((X − B − lump)/new)` ignores interest.

### Main failure types (held-out)

- **Compound interest**, in every full-design prompt: 5 of 5 rate questions fail, before and after GEPA. Even
  with step-by-step worked examples, Gemma drifts by ₹10–₹300 over 12–24 months of month-by-month compounding.
  The extract design gets all of them right because code does the arithmetic.
- **Chart length and missing charts on target questions** (full design): 15 vs 14 points, or no chart at all.
  GEPA cut these but did not remove them.
- **Needless follow-ups** are the biggest failure of the un-optimised prompts. The naive full prompt asked one on
  58 of 63 questions; the extract prompts ask one even after filling every parameter. GEPA's follow-up rule fixes
  this completely in the extract design.
- **Follow-up questions in English letters** ("Aap har mahine kitna…") when she typed Hinglish (extract
  baselines). GEPA added "Devanagari even if she typed romanised Hindi".

## Recommendation: how to add projections to the app (proposal only, not built)

1. **Use the extract design.** One LLM call extracts the scenario as JSON, with the GEPA ← planner-style
   instructions from `projection_program-extract-planner-opus.json`, which are about 4 kB.
   - Code computes the series and figures with the formula above, reading her savings register through the same
     consent and gateway path as the planner. The fields needed are `savings: month, amount, cumulative`.
   - The answer comes from a code template. It scored 1.00 here and cannot get a figure wrong, unlike the writer
     slips seen in the agent eval.
   - Optionally, an LLM can rephrase the text, with the critic checking that the figures are unchanged.
2. **Add a code guard:** if the model asks a follow-up but every parameter is filled and differs from her current
   deposit, ignore the follow-up and compute. On the stored baseline outputs this alone lifts extract/hand from
   0.909 to 0.956 and extract/naive from 0.457 to 0.782, before any prompt optimisation.
3. **Route projections from the planner.** Give the planner a new decision such as `"projection"`, or a
   `scheme_topics`-like flag, and send the question to the extractor. Keep the plan's items to the savings
   register only.
4. **Show a chart card in the chat.**
   - The server sends a `chart` event carrying the spec format used here (`{"type": "line", "x": [...],
     "series": [{"name": "अभी की बचत से", ...}, {"name": "बढ़ी हुई बचत से", ...}], "y_label": "₹"}`).
   - `web/chat.js` draws it with a small inline SVG or Chart.js, under the spoken answer.
   - The voice reply speaks only the template sentence.
5. **Say the assumptions.**
   - No interest unless she names a rate.
   - The default period is 12 months, and the answer says so.
   - Months with no deposit (D-003 and D-005 each missed some) are not modelled. Show "अगर हर महीने जमा करती
     रहें" in the card.

## Caveats

- **Sample size.** Held-out n = 63, so one question is about 1.6 points. Interest has only 5 held-out questions.
  Each score is a single temperature-0 pass.
- **The extract design's 1.00 partly reflects the design itself.** Once the parameters are right, the figures, the
  chart and the Hindi answer are right by construction. Its score measures parameter extraction and follow-up
  judgement.
- **The questions are template-generated.** Real didis' phrasing will be messier; add real transcripts before
  trusting 1.00.
- **The Nemotron student was not run**, because Gemma is the primary model. Only Nemotron Ultra was tried as a
  teacher.

## Reproduce

```bash
.venv/bin/python scripts/dspy_projection.py facts
.dspy-venv/bin/python scripts/dspy_projection.py dataset
.dspy-venv/bin/python scripts/dspy_projection.py eval --design extract --prompt hand
.dspy-venv/bin/python scripts/dspy_projection.py optimize --design extract --prompt planner --reflection claude-cli/claude-opus-5-5
.dspy-venv/bin/python scripts/dspy_projection.py eval --design extract --program eval/dspy/projection_program-extract-planner-opus.json
.dspy-venv/bin/python scripts/dspy_projection.py report
```

- `projection_results.json` has every case's output, part scores and feedback.
- GEPA's logs are in `projection_gepa/`.
- The optimised prompts, with every candidate and its validation score, are in `projection_program-*.json`.
