# Direct Hindi vs Bhashini pivot (29 Oct 2026)

**Keep direct Hindi.** On the same 17 eval cases, Gemma 4 working directly in Hindi was more accurate and about 1 second
faster than translating the question to English with Bhashini NMT and the answer back.

Setup: Plan-and-Execute, Gemma 4 26B on Bhashini as the main model, consent interpreter on Gemma, Ragas scored by
Claude Opus 5.5 through the Claude CLI. No NVIDIA model was used. Runs: `20261029-1221-plan_execute@direct-plan_execute@pivot.csv`.

| Mode | Answer accuracy | Faithfulness | Tool-call F1 | Chart correct | Avg time | Consent prompts |
|---|---|---|---|---|---|---|
| Direct Hindi | **0.68** | **0.81** | **0.85** | **0.83** | **2.2 s** | 0.82 |
| Pivot via English (Bhashini NMT) | 0.62 | 0.76 | 0.79 | 0.67 | 3.2 s | 0.88 |

## Where pivot lost, and why

| Case | What happened in pivot |
|---|---|
| sunita-proj-add | "₹300 और … 6 महीने बाद" was translated as "If I save ₹300 and 6 every month … after the month": the month count was glued to the amount, so the plan computed ₹306 for 1 month (accuracy 0, direct 0.75). |
| santosh-land | The English answer's 1.5 acres came back in Hindi as "एक एकड़" (one acre) (accuracy 0, direct 0.75). |
| sunita-proj-follow-up | Working in English, the planner read her savings before asking how much more she wants to save (tool F1 0, direct 1.0): a privacy loss as well as an extra step. |

Pivot scored higher on two projection cases (8% interest, no period given), within the noise of one run each.

## Caveats

- 17 cases, one run each: differences of ±0.1 on a single case are noise; the three failures above are not.
- Pivot was built for models weak in Hindi. With a model that handles Hindi well, it adds a translation step that can
  break numbers and order, and about one second each way.
- The tokens that protect amounts in `server/agents/pivot.py` did not stop the reordering in the first case, and
  numbers written as words in English are not protected on the way back.
