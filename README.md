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

Open <http://localhost:8800>, click **Use sample registers**, choose a didi on the same setup page, and ask
"क्या मैं लखपति दीदी बन गई हूँ?" or "मेरी डेयरी बढ़ाने का बिज़नेस प्लान बना दो". API docs are at `/v1/docs`.

**Without an NVIDIA key**, start the server with a scripted stand-in model to click through the screens. It
asks consent, reads registers, runs the Lakhpati table and builds plans for savings, income, Lakhpati and
business-plan questions. It never runs on Vercel.

```bash
PLAYGROUND_FAKE_LLM=1 .venv/bin/uvicorn main:app --app-dir server --reload --port 8801
```

## Laya planner (local only)

The operator panel's **Planning model** can hand Plan-and-Execute's planning step to
[Laya](https://huggingface.co/convaiinnovations/laya), a 322M-parameter multilingual classifier, running on this
computer. Laya labels her message (what she wants, which register, which enterprise); code turns the labels into
the plan, so consent, reads, decision tables and plans are unchanged and the chat model only writes the answer.
A decision or plan is taken only when Laya is very sure; a vague request gets a fixed follow-up question. Laya
needs PyTorch, so it runs as a sidecar in its own environment and is shown as unavailable on Vercel.

```bash
/opt/homebrew/bin/python3.11 -m venv .laya-venv
```

```bash
.laya-venv/bin/pip install 'laya[serve]'
```

```bash
.laya-venv/bin/python scripts/laya_server.py
```

The playground finds it at `LAYA_URL` (default `http://localhost:8790`). The first run downloads the model.

## Tracing and evaluation (local only)

**Langfuse traces.** With `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL` in `.env` and Langfuse
running locally (`cd ~/langfuse && docker compose up -d`, after `colima start --cpu 4 --memory 8`), every run is
one trace: each LLM call with its prompt and reply, each tool call with its result, consent and self-check events,
and scores for consent prompts, LLM calls, fields read and run time (`server/tracing.py`). An NVIDIA judge
(Nemotron 3 Ultra, `server/judge.py`) adds `judge_*` scores with a reason. Off on Vercel and in tests, because
traces carry fetched records.

**Ragas evaluation.** `eval/cases.json` holds Hindi questions with reference answers taken from the mock
registers and the tool calls a good agent makes. `scripts/eval_agents.py` asks each case through the chosen agents,
reads each run back from Langfuse, scores it with Ragas (faithfulness to what the run read, accuracy against the
reference, tool-call F1) using an NVIDIA model as evaluator, writes the scores onto the traces, and links the
runs to the Langfuse dataset `didi-agent-eval` so the agents compare side by side. Ragas pins an older LangChain,
so it has its own environment:

```bash
/opt/homebrew/bin/python3.11 -m venv .eval-venv
```

```bash
.eval-venv/bin/pip install "ragas==0.4.3" "langchain-community<0.4" "langchain-core<1" "langchain-openai<1" "langchain<1" openai requests
```

```bash
.eval-venv/bin/python scripts/eval_agents.py --agents plan_execute react rewoo direct
```

Results also land in `eval/results/` (a CSV per run and the raw runs as JSON; `--from-runs` rescores them).

## Error tracking (local only)

Server exceptions and uncaught browser errors go to [GlitchTip](https://glitchtip.com), an open-source (MIT) server
that accepts the official Sentry SDKs, so moving to Sentry later only means changing the DSN. The full self-hosted
Sentry needs 16 GB of RAM for its Docker VM, more than this laptop can spare next to Langfuse.

- GlitchTip runs from `~/glitchtip/compose.yml` (Postgres plus one web container, about 250 MB):
  `cd ~/glitchtip && docker compose up -d`, then open http://localhost:8020. The login is in `~/glitchtip/credentials.txt`.
- Set `SENTRY_DSN` in `.env` (project "Didi ka Playground", organization GramStack). `server/errors.py` starts the
  SDK; `web/api.js` sends page errors to `/v1/client-error`, which forwards them tagged `source=browser`.
- Off on Vercel and in tests: events can carry register values, so use mock data only.

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
| `/connect` | One setup page for uploads, source configuration, didi selection and login; consent wording and yes/no words are available below |
| `/login` | Redirects to the didi selector on `/connect` for older links |
| `/chat` | The didi's chat; the operator panel holds model, agent, compare, language, self-check, steps view, consents, trace |
| `/department` | Each owner's access log and policy (which purposes may read which tables; pause) |
| `/decisions` | The decision tables, with a form to try them |
| `/verify` | The verifier ("बैंक" by default, `?as=block_office` for the other mock verifier): credentials didis sent, checked and showing only disclosed claims |

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

## Wallet (mock, voice-first)

Each didi has a custodial wallet of W3C Verifiable Credentials 2.0, secured as SD-JWT (VC-JOSE-COSE, `typ: vc+sd-jwt`)
with selective disclosure. Everything is open source: [sd-jwt-python](https://github.com/openwallet-foundation-labs/sd-jwt-python)
(OpenWallet Foundation Labs, Apache 2.0) on jwcrypto (LGPL 3.0).

- **Issuer** (`server/wallet/`): one did:web identity, `did:web:localhost%3A8800` (set `PUBLIC_URL` or `WALLET_DID`
  elsewhere), document at `/.well-known/did.json`. The EC P-256 key is generated on first use into
  `~/.didi_playground/wallet/issuer.jwk.json` (`WALLET_KEY_DIR`), outside the repository; a hosted deployment must set
  `WALLET_ISSUER_JWK` (a private EC P-256 JWK) and a public `PUBLIC_URL`, or the wallet refuses to issue or verify
  (503) rather than sign with a key the next cold start would lose. `/healthz` shows the key's
  thumbprint. Every `credentialSubject` claim is a disclosure, with decoy digests; the envelope (type, issuer,
  validity, status, holder key `cnf`) is visible.
- **Credentials**: SHG membership (LokOS signs its own users and shg rows), Lakhpati Didi status (the decision
  table's outcome, rule and version after it ran on her DAR with her consent; no income figure is in it),
  business plan (SHA-256 of the built workbook, headline figures and her approval), a mock RSETI training
  certificate, and a consent receipt for every decided consent in the ledger.
- **Storage**: `wallet_credentials`, `wallet_holders` (her holder key) and `wallet_shares` in the core store.
- **Voice** (Plan-and-Execute, code reads the intent and words the answer, no LLM call): "मेरे प्रमाणपत्र दिखाओ" lists
  them in a wallet card; "लखपति दीदी का प्रमाणपत्र बैंक को भेजो" issues the credential if needed (consent to read her
  DAR), then asks aloud which claims go to the bank (a ledger consent, purpose `credential_share`; "कुछ ही दिखाइए"
  picks fewer) and sends a presentation with only those disclosures and a key-binding JWT for the bank's nonce;
  "मेरा बिज़नेस प्लान वॉलेट में रखो" asks her to approve the latest plan and issues it.
- **Verifier**: checks the presentation alone: did:web issuer, ES256 signature, disclosure digests, key binding
  (audience and nonce), expiry, and revocation through a W3C Bitstring Status List (`/v1/wallet/status`). A
  superseded Lakhpati or plan credential and the receipt of a withdrawn consent are revoked;
  `POST /v1/wallet/credentials/{id}/revoke` is the mock issuer's revoke.

### Phase 2: MOSIP Inji

The mock keeps the same shapes Inji uses, so the swap is at the edges:

1. **Issuer → Inji Certify.** Replace `server/wallet/service._issue` with a call to Inji Certify's OpenID4VCI
   credential endpoint. Each credential type becomes a Certify credential configuration (SD-JWT VC, or
   `ldp_vc` where a verifier needs JSON-LD), and the data the issue functions assemble (LokOS rows, the decision
   result, the plan hash) moves behind Certify's data-provider plugin, one per department register. Keys move into
   Certify's HSM-backed key manager and the DID document is served by Certify; revocation uses its status list.
2. **Wallet.** Keep the custodial wallet for the voice flow, receiving credentials over OpenID4VCI as Inji Wallet
   (Inji Mobile or Inji Web) does, or hand them to her own Inji Wallet when she has a phone. The voice intents stay.
3. **Verifier → Inji Verify.** The bank uses Inji Verify (SDK or web) instead of `/verify`; the share becomes an
   OpenID4VP presentation request (the verifier sends the nonce and the claims it needs, which replaces
   `service.VERIFIERS`), and the didi's spoken consent answers it.
4. **Trust.** `keys.resolve` trusts one did:web; Phase 2 resolves did:web over HTTPS and checks issuers against a
   trust registry.

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
                          react, rewoo, direct, critic, pivot, facts, prompts, writer, laya_planner
server/consent/           catalog.py (purposes, labels, vocabulary), ledger.py (requests and answers)
server/gateway.py         the only path to a source's data
server/decisions/         engine.py, derive.py, tables/*.json
server/plans/             templates.py, builder.py (openpyxl), service.py
server/tools.py           agent tool schemas and the safe calculator
server/department.py      the department view
server/wallet/            keys.py (did:web issuer, holder keys), vc.py (SD-JWT VC issue/present/verify, status list),
                          service.py (credential types, issuing, sharing, the verifier's inbox); agents/wallet.py: voice
server/connectors/        excel.py, gsheet.py, sql.py (read-only)
server/core/              store.py (core store), files.py (Blob or local)
server/llm/nvidia.py      NVIDIA API client, model list, probe, local fake model
server/llm/laya.py        client for the local Laya sidecar (scripts/laya_server.py)
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
