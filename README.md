# Port Dues

Upload a port tariff PDF, ask what a vessel call will cost, get an itemised and cited answer streamed back.

The system reads the tariff book once and turns it into two things: a navigable tree of clauses, facts and digests
indexed in Qdrant, and a **compiled rulebook**: every charge expressed as a small typed formula that a
deterministic engine executes. A question then costs two small LLM calls: one to read the vessel call out of the
question, one to explain the computed result. Every amount comes from the engine. Nothing about a specific port,
charge or rate is written in the code; it is all discovered from the document.

## Results

The brief's reference vessel (SUDESTADA at Durban):

| Charge | System | Brief | |
|---|---:|---:|---|
| Light dues | 60,062.04 | 60,062.04 | exact |
| Port dues | 199,371.35 | 199,549.22 | 0.09% (the brief seems to use a slightly longer stay) |
| Towage | 147,074.38 | 147,074.38 | exact |
| VTS | 33,345.00 | 33,315.75 | 0.09% (0.65 × 51,300 per the tariff) |
| Pilotage | 47,189.94 | 47,189.94 | exact |
| Running lines (= §3.8 berthing, see below) | 19,639.50 | 19,639.50 | exact |

Cargo dues (40,000 t iron ore export at 10.65/t = 426,000.00) are reported separately, as the exporter pays them.

Everything runs on `gpt-6-luna`, the cheapest model available here:

- **Rulebook quality** (`portdues check-rules`, compiled rules evaluated directly on the 30 golden calls, no LLM):
  29/30 cases exact; the remaining case (a bunkers-only call) prices correctly end to end, see
  [Evaluation](#evaluation).
- **Agreement with a rulebook compiled by `gpt-6.1-sol`** (`portdues compare-rules`, 2,264 charges over a grid of
  ports, tonnages, situations and commodities): identical everywhere except where the Luna rules apply the
  document's SAPS/SANDF exemptions for pilotage and berth dues, which the Sol rules missed, and the bunkers-only
  case above.
- **End to end** (latest 30-question run, earlier rulebook): 96.2% of charges within ±1%, 28/30 cases exact,
  about 7 s and $0.0008 per answer, **$0.028 for the whole run including judging**.
- **Cost of a document**: ~$0.06 to read and index it, ~$0.55 to compile the rulebook (once).

## How it works

```
                          INGESTION (once per document)
PDF ──► logical pages ──► clause tree ──► pass 1: facts ──► pass 2..n: digests ──► Qdrant + Postgres
        text layer, or      numbering         per clause        bottom-up, leaf to root
        vision model for    drives the
        tables and figures  hierarchy                                     │
                                                                          ▼
              catalogue of charges ──► per charge: ReAct compiler agent (read, search, test_rule)
                                         ├─ validator + dry run
                                         ├─ independent acceptance tests
                                         ├─ port-coverage and variant-order probes
                                         └─ self-consistency across samples ──► rulebook (Postgres)

                          QUERY (per question, streamed over SSE)
question ──► vessel profile (structured output; schema generated from the rulebook's variables)
         ──► deterministic engine evaluates every rule ──► line items with formula, inputs, assumptions
         ──► streamed explanation (the model never recomputes the numbers)
```

### Ingestion

1. **Pages.** Two-up spreads are split into logical pages. Pages with tables (rows of several separated numeric
   cells) or figures are rendered and transcribed to markdown by the vision model, with the PDF text layer passed
   along so it copies digits rather than reading them off pixels; plain text pages skip the model (29 of 54
   pages in the sample).
2. **Clause tree** from the clause numbering (`SECTION 4` › `4.1` › `4.1.1`), not font sizes. Running headers and
   footers are dropped by frequency, the contents page is skipped.
3. **Facts (pass 1).** Each clause becomes atomic, self-contained facts (charge, port, basis, amount, condition).
4. **Digests (pass 2..n).** Bottom-up: leaf digests keep every figure, nodes with children become overviews that
   point down the tree, up to a root that catalogues every charge.
5. **Index.** Verbatim chunks, facts and digests are embedded (`text-embedding-3-small`) into one Qdrant collection.
6. **Compile** (next section).

Restarted ingestions resume from checkpoints of the vision transcriptions and extracted facts.

### Is RAPTOR the right fit?

Partly, and that shaped the design. Tariff questions are computations, not summaries. A RAPTOR-style digest is good
for *finding* the clauses that matter, but a figure that has been summarised twice shouldn't be trusted for
arithmetic. So the hierarchy follows the document's own numbering instead of embedding clusters, digests are used
for navigation only, and every number comes from verbatim clause text through a compiled rule.

### The rule language

Most charges are "rate × units, picked by port, size band or item, adjusted by a few conditions". A `ChargeRule`
holds port variants, numeric bands (`band(table, gt, 'base')`), keyed tables for named items such as commodities
(`lookup(table, cargo_commodity, 'exports')`, with an `other` row for unlisted items), constants with their source
clause, minimums, conditional surcharges and reductions, exemptions, and `on_application` for cases the document
prices by agreement. Formulas are parsed with `ast` and walked by hand, never `eval`ed. A fact the call doesn't
state is never guessed: an unnamed port or a missing commodity makes the charge *needs input*, and the answer asks
for it.

### The compiler

From the root digest one call catalogues the charges a vessel call can incur, the ports the tariff covers, and
splits a charge with several rate tables into one rule per table. Then, for each routine charge, a ReAct agent
(LangChain `create_agent`) reads the clauses and writes the rule. A small model makes mistakes, and each run makes
*different* ones, so most of the work is in catching them:

| guard | what it catches |
|---|---|
| **`test_rule` tool** | the agent dry-runs its draft on rows of the document's own tables before answering |
| **facts checklist** | the brief lists every rate, condition and extra fee extracted from the clauses; each must be modelled or explained |
| **validator + dry run** | syntax, unknown variables or tables, port names the tariff doesn't contain, identical `if/else` branches, tables no formula reads |
| **independent acceptance tests** | a separate call writes 4–6 test calls from the verbatim text without seeing the rule (always including a typical cargo ship); mismatches go back to the agent |
| **port-coverage probe** | a rule that prices most ports at a tonnage but nothing for one (a dropped table cell) |
| **variant-order probe** | a variant that never applies because an earlier one always wins (e.g. reductions that don't stack, in the wrong order) |
| **self-consistency** | two independent samples per charge; if they disagree on the probe grid, a third breaks the tie, penalising samples that still fail acceptance tests |
| **rule variables** | a qualifier the vocabulary can't express ("certified tankers") is declared as a variable with a default, never dropped |

Each guard came out of a real failure while bringing Luna up to the Sol rulebook's accuracy. A charge that still
can't be compiled is recorded as uncovered and shown in the rulebook view. One rule can be recompiled on its own
(`portdues ingest --compile-only <doc> --rules <rule_id>` or the button in the rulebook view).

An alternative compiler built on a task-list agent (plan reads, review, then write the rule in one shot) was
measured against ReAct on the same document and removed: 75% on the golden calls against 98–99% for ReAct, at
higher cost. Query-time agents (a task-list agent with and without the compiled rules) were also measured and
removed: with the rules they matched the compiled pipeline's accuracy at ~8× the latency and ~4× the tokens;
without them they reached 70%.

### Models and tokens

| role | default | used for |
|---|---|---|
| `compiler` | `gpt-6-luna` | catalogue, rule compilation, acceptance tests |
| `fast` | `gpt-6-luna` | page transcription, facts, digests, profile extraction, explanation |
| `judge` | `gpt-6-luna` | grading eval answers |

All configurable in `.env` (`COMPILER_MODEL`, `FAST_MODEL`, `JUDGE_MODEL`, `COMPILE_SAMPLES`). Luna was checked
against `gpt-6.1-sol` on a table page (all 54 figures recovered, none invented) and on query parsing before
switching. Static system prompts come first so OpenAI's prompt cache hits (70–85% of compile input is cached).

Usage is measured, not estimated: each ingestion stage, compile, chat turn and eval case records input, output,
cached and reasoning tokens and the cost in USD (`MODEL_PRICES`), shown on the Documents page, under each answer,
in the rulebook view and in experiments.

## Evaluation

`backend/portdues/evals/golden.jsonl`: 30 questions about the Transnet tariff, 10 each **easy** (one charge,
facts stated plainly), **medium** (band lookups, minimum fees, full estimates at different ports, derived stay
length) and **hard** (out-of-hours surcharges, navy vessel exemptions, bunkers-only and short-stay reductions,
coaster status, messy agent emails, and the brief's reference vessel including its cargo dues).

Labels come from `scripts/golden/za_reference.py`, an independent hand transcription of the relevant clauses,
used **only** for labelling; the application never imports it. Notes:

- the brief's "Running Lines" figure (19,639.50) is §3.8 *Berthing Services* (2 × (2 801.91 + 13.68 × 513)), so
  it is scored as berthing;
- where the tariff is genuinely ambiguous (pilotage for a navy vessel is exempt "except if performed on
  request"), the charge is left out of the expected set rather than guessed;
- questions were phrased by the LLM from the scenario specs and rejected if they dropped any number;
  `build_golden.py --relabel` recomputes labels without re-phrasing.

Three ways to score, from cheapest to most complete:

- `portdues check-rules <doc_id>`: the compiled rules on the golden calls. Free and deterministic; run it after
  every recompile. It fills vocabulary variables only, so a rule that declares its own flag (e.g.
  `bunkers_stores_water_only`) can show a miss here that the real path handles (the profile extractor fills
  that flag from the question; checked for the bunkers case: 65,711.94 as expected).
- `portdues compare-rules <doc_id> other_rulebook.json`: price ~300 calls with two rulebooks and list every
  disagreement. Free; this is how the Luna rules were checked against the Sol rules.
- The **Experiments page** (or `portdues eval`): the full pipeline on the golden questions, scored by a numeric
  check (each charge within ±1%), an LLM judge (rubric with the reference figures) and
  [Laya](https://huggingface.co/convaiinnovations/laya), an open-source System-1 decision model that answers typed
  yes/no checks about coverage and presentation (in my tests it could not tell a right figure from a wrong one, so
  correctness is left to the other two). A run streams live: KPIs, accuracy by difficulty, hit rate per charge, and
  a case table where each row opens expected vs predicted, the judge's rationale and the Langfuse trace.

## Running it

```bash
cp .env.example .env          # set OPENAI_API_KEY and the Langfuse admin login
docker compose up --build -d  # app on http://localhost:8080, Langfuse on http://localhost:3000
```

Upload `Port Tariff.pdf` on the Documents page; ingestion takes about fifteen minutes and happens once (the same
file uploaded again is reused). The Laya scorer pulls a CPU build of PyTorch into the API image and ~800 MB of
weights on first use; `WITH_LAYA=false docker compose build` leaves it out.

Local development:

```bash
make infra                                          # postgres on :5434, qdrant on :6333
make langfuse                                       # langfuse on :3000
cd backend && uv venv && uv pip install -e ".[dev,laya]"
.venv/bin/portdues ingest "../Port Tariff.pdf"
make api                                            # :8000
make web                                            # :5173, proxies /api
make test
```

Settings are read from `.env` (the project `.env` wins over variables exported in the shell); see `.env.example`.

## Observability

`docker compose up` starts a self-hosted Langfuse (web, worker, Postgres, ClickHouse, Redis, MinIO) on
http://localhost:3000. It is provisioned on first start from `.env`: `LANGFUSE_ADMIN_EMAIL` /
`LANGFUSE_ADMIN_PASSWORD` for the login, `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` for the project the API
writes to. Sign-up is disabled. The admin user is created on Langfuse's first start only, so changing the
password later needs a reset of the `langfuse_pg_data` volume.

- Every LLM call, tool call and agent step is traced, tagged by stage (`ingest`, `compile`, `verify`, `query`,
  `eval`). One chat turn is one trace; each answer and each experiment case links to it. Ingestion and compiler
  runs are grouped as a session per document.
- **Cost:** at startup the API registers a price for each model in `MODEL_PRICES`, covering every usage key the
  handler reports (input, cache reads, cache writes, output, reasoning), so Langfuse's costs match the app's own
  trace for trace. Langfuse prices observations when they are ingested, so a price change applies to new traces.

## API

| method | path | |
|---|---|---|
| `POST` | `/api/documents` | upload a PDF (multipart); starts ingestion, deduplicated by hash |
| `GET` | `/api/documents`, `/api/documents/{id}` | status, stage, progress, stats, tokens and cost |
| `POST` | `/api/documents/{id}/reingest?compile_only=` | rebuild everything, or only the rulebook |
| `POST` | `/api/documents/{id}/rules/{rule_id}/recompile` | recompile one rule |
| `GET` | `/api/documents/{id}/tree`, `/sections/{node}` | clause tree with digests, facts and verbatim text |
| `GET` | `/api/documents/{id}/rulebook` | the compiled rules and how they were produced |
| `POST` | `/api/chat` | SSE stream of a turn: `{document_id, message, conversation_id?}` |
| `GET` | `/api/documents/{id}/conversations`, `/api/conversations/{id}/messages` | chat history |
| `GET` | `/api/eval/cases` | the golden set |
| `POST` | `/api/experiments` | start a run `{document_id, scorers, difficulty, limit, concurrency}` |
| `GET` | `/api/experiments/{id}/stream` | SSE: per-case scores and the rolling summary (replays history to late joiners) |

## Layout

```
backend/portdues
  ingestion/   pages.py (PDF + vision), sections.py (clause tree), tree.py (facts + digests), pipeline.py
  rules/       schema.py (rule language), engine.py (evaluator + validator), compiler.py (compiler + guards)
  agents/      tools.py (tree tools), profile.py (question → call profile), strategies.py (answering pipeline)
  evals/       golden.jsonl, scorers.py, runner.py, rulebook_check.py (check-rules, compare-rules)
  storage/     db.py (Postgres), vectors.py (Qdrant)
  api/         FastAPI routers
backend/scripts/golden   dataset builder and the labelling reference
frontend/src             React app (documents, chat, experiments)
```

## Limitations and next steps

- A small model's compile still varies from run to run; the guards above bring it to the Sol rulebook's results
  on this tariff, but a human review of the rulebook (approve or edit a rule in the rulebook view) is the natural
  next feature before relying on it for invoices.
- The core vocabulary (GT, LOA, stay, services, common flags) is generic shipping terminology; a tariff that
  hinges on something exotic gets rule-declared variables, which the profile extractor must then fill from the
  question.
- Questions about situational services that are not compiled (dry-docking, fire services, equipment hire) are
  answered as "not priced" rather than researched.
- Multi-document ports (a tariff plus a notice amending it) would need rule precedence by effective date.
- VAT is reported, not added, matching how the reference values are stated.
