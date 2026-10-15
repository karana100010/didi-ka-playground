# Didi ka Playground

A voice chatbot that simulates the GramStack shared layer for rural livelihoods: a didi asks in her own
language, the assistant asks her consent before reading her records, fetches only those fields from a
connected sheet or database, answers, and discards the data. The design is in
[docs/design.html](docs/design.html).

## What is built (M1–M6)

| Milestone | What works |
|---|---|
| M1 Foundation | Mock registers, connectors (Excel, Google Sheet link, Postgres, SQLite), landing / connect / log-in screens |
| M2 Voice chat | Chat with an 84 px mic (Bhashini ASR with the didi's ASR language), spoken answers (TTS), NVIDIA model list and tool-support probe, 429 back-off, demo cache |
| M3 Consent | Itemised consent cards in her language; answers by button, voice or text; custom yes/no words; read-back; default deny; tool gateway; department view with access log and policy |
| M4 Agents | Plan-and-Execute (default), native tool calling, ReAct, ReWOO, Direct; self-check of figures; pivot mode through Bhashini NMT; compare view |
| M5 Decisions | `lakhpati_status` and `bank_linkage_readiness` decision tables, inputs derived by code, `/decisions` page |
| M6 Business plans | Excel plans (dairy, kirana, tailoring) with live formulas, mock JEEViKA unit costs, provenance sheet, download |

Not built yet: M7 (the evaluation set and comparison report), direct browser-to-Blob uploads over 4 MB, and
the Vercel deployment itself.

## Run it locally

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements-dev.txt
```

Copy `.env.example` to `.env`. Set `NVIDIA_API_KEY`, and either `BHASHINI_API_KEY` or `BHASHINI_CONF` pointing at
bhashini_kit's `config/bhashini.conf`. Leave `DATABASE_URL` and `BLOB_READ_WRITE_TOKEN` empty to use SQLite and
local disk under `data/local/`.

```bash
.venv/bin/uvicorn main:app --app-dir server --reload --port 8800
```

Open <http://localhost:8800>, click **Use sample registers**, log in as a didi, and ask
"क्या मैं लखपति दीदी बन गई हूँ?" or "मेरी डेयरी बढ़ाने का बिज़नेस प्लान बना दो". API docs are at `/v1/docs`.

**Without an NVIDIA key**, start the server with a scripted stand-in model to click through the screens. It
asks consent, reads registers, runs the Lakhpati table and builds plans for savings, income, Lakhpati and
business-plan questions. It never runs on Vercel.

```bash
PLAYGROUND_FAKE_LLM=1 .venv/bin/uvicorn main:app --app-dir server --reload --port 8801
```

## Voice conversation

On `/chat`, tap **Start conversation** once and allow microphone access. Speak naturally; after a short
pause, your words are transcribed and sent. Capture pauses while the model works and while the reply
or a consent prompt is spoken, then listening resumes automatically. Spoken consent answers and
read-back confirmations use the same conversation. Tap **Stop** at any point to end voice mode;
typed chat still works. Leaving or hiding the page also stops voice mode. Silence alone is never
sent for transcription. Device or transcription errors stop voice mode and show a retry message.

Voice lifecycle regression checks (Node.js):

```bash
node --test tests/voice_flow.test.mjs
```

## Pages

| Page | For |
|---|---|
| `/` | Landing |
| `/connect` | Sources, their setup, consent wording per register, the didi's own yes/no words |
| `/login` | Log in as a didi (stands in for Aadhaar) |
| `/chat` | The didi's chat; the operator panel holds model, agent, compare, language, self-check, steps view, consents, trace |
| `/department` | Each owner's access log and policy (which purposes may read which tables; pause) |
| `/decisions` | The decision tables, with a form to try them |

## How a question runs

1. The chat posts the question; the server creates a turn with one run per agent (more in compare mode).
2. Opening `GET /v1/turns/{id}/stream` drives the runs and streams events (SSE).
3. A consent request pauses the run. The didi answers on the card (`POST /v1/consents/{id}/answer`); the
   browser reopens the stream and the run continues.
4. Runs survive pauses and Vercel's time limit by record and replay: every LLM call, tool call and consent
   result is recorded; a resumed run replays its own code from the top using the recorded results. The record
   is deleted when the run ends, so fetched values do not outlive the answer.
5. Every read goes through `server/gateway.py`: consent for this question, the owner's policy, the didi's own
   rows only (the filter comes from her login), office bearers only for group data. Each read is logged.

## Tests

```bash
.venv/bin/python -m pytest tests -q
```

The same suite against a Postgres core store, and with one live Bhashini call:

```bash
DATABASE_URL=postgresql://localhost/didi_playground_test BHASHINI_LIVE=1 .venv/bin/python -m pytest tests -q
```

66 tests, all with a scripted fake LLM: consent and the gateway's checks, record and replay, every agent type,
pivot translation, the self-check, the decision outcome for all eight didis, and the business-plan workbook
(its formulas are evaluated with the `formulas` package and must equal the Python figures).

## Mock data

`data/mock/` holds invented registers for eight didis (seven Hindi speakers, three of them dialect speakers,
and one Tamil speaker). Sunita Devi's net household income over the last 12 months is exactly ₹1,24,800, her
Lakhpati status is ON_TRACK (rule 2), and her dairy plan gives the design doc's figures (₹9,840 → ₹14,760 a
month, instalment ₹2,824, payback in 15 months). One cell in `dar` (Santosh Kanwar, February 2026) holds an
instruction aimed at the assistant, for prompt-injection tests. `data/reference/jeevika_unit_costs.csv` holds
**mock** unit costs in JEEViKA's format; every plan says so.

```bash
.venv/bin/python scripts/make_mock_data.py
```

```bash
.venv/bin/python scripts/load_mock_postgres.py postgresql://user:password@host/mock_sources
```

## Deploy to Vercel

1. Link the folder to a new Vercel project: `vercel link`.
2. In the project's **Storage** tab, add a **Neon** Postgres database (sets `DATABASE_URL`) and a **Blob**
   store (sets `BLOB_READ_WRITE_TOKEN`). Create the Blob store as private, or set `BLOB_ACCESS=public`.
3. Add `BHASHINI_API_KEY`, `NVIDIA_API_KEY`, `PLAYGROUND_ACCESS_CODE`, and optionally `GOOGLE_SERVICE_ACCOUNT`.
4. Deploy: `vercel deploy --prod`. Functions run in Mumbai (`bom1`).

## Layout

```
api/index.py              Vercel entry point
server/main.py            FastAPI app: /v1 API, /api/bhashini/*, pages
server/runner.py          turns and runs; pivot translation and the self-check around each agent
server/agents/            context.py (record/replay, consent, fetch, decide, plans), plan_execute, native_tools,
                          react, rewoo, direct, critic, pivot, facts, prompts
server/consent/           catalog.py (purposes, labels, vocabulary), ledger.py (requests and answers)
server/gateway.py         the only path to a source's data
server/decisions/         engine.py, derive.py, tables/*.json
server/plans/             templates.py, builder.py (openpyxl), service.py
server/tools.py           agent tool schemas and the safe calculator
server/department.py      the department view
server/connectors/        excel.py, gsheet.py, sql.py (read-only)
server/core/              store.py (core store), files.py (Blob or local)
server/llm/nvidia.py      NVIDIA API client, model list, probe, local fake model
server/bhashini/          vendored from bhashini_kit
web/                      pages and ES modules
data/mock/, data/reference/
tests/                    test_m1 … test_m6
```

## Known limits

- Live NVIDIA calls are untested until a key is added; the shortlist's tool support is measured by the probe.
- Voice input could not be tried in the preview browser (no microphone there); it uses bhashini_kit's recorder.
- Uploads are capped at 4 MB until direct browser-to-Blob uploads are added.
- Pivot mode protects amounts and names with placeholder tokens; how well Bhashini NMT keeps them needs a live check.
