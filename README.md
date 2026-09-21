# Ticket Insight AI

An AI-powered system over a customer support ticket dataset: ask questions in plain English,
get data-grounded answers, and surface rule-based anomalies. Built for the DOTMappers AI
Engineer take-home assessment.

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# edit .env and set GROQ_API_KEY (free key from https://console.groq.com/keys)
```

Run the REST API:

```bash
uvicorn app.main:app --reload
```

Run the UI (in a separate terminal, same venv):

```bash
streamlit run streamlit_app.py
```

Either one works standalone — the UI imports the same `app/*` modules the API uses rather
than calling the API over HTTP, so you don't need both running at once.

The SQLite database (`tickets.db`) is built automatically from `data/support_tickets.csv`
the first time either entrypoint runs.

## Architecture

```
data/support_tickets.csv
        |
        v  app/db.py  (pandas -> SQLite, indexed on status/priority/agent_id)
   tickets.db
        |
        |<--------------------------------------------------.
        v                                                     |
NL question --> app/llm.py: extract_query_spec()              |
        (Groq LLM, JSON mode)                                 |
        v                                                     |
  QuerySpec (structured: metric, group_by, filters, ...)      |
        v                                                     |
app/query_builder.py: build_sql()                              |
  (whitelisted columns/operators -> parameterized SQL) --------
        v
   result rows
        v
app/llm.py: phrase_answer()  (Groq LLM, grounded in result rows only)
        v
   plain-English answer

app/anomaly.py: detect_anomalies()  -- independent rule-based pass over tickets.db
```

`app/main.py` (FastAPI) and `streamlit_app.py` are two thin front ends over the same
core modules (`db`, `query_builder`, `llm`, `anomaly`).

### Why the LLM never writes SQL directly

The LLM's only jobs are (1) turning a question into a small structured JSON object
(metric, optional group-by column, optional filters — all drawn from a fixed whitelist
of columns and operators defined in `query_builder.py`), and (2) turning result rows back
into English. It never generates or executes SQL text itself. `build_sql()` is the only
thing that constructs SQL, and it only ever uses parameterized queries against whitelisted
columns — so a malformed or adversarial LLM response can't result in arbitrary SQL
execution, only a rejected/invalid spec (`InvalidQuerySpec` -> HTTP 422).

This costs a little flexibility versus letting the LLM write raw SQL (it can only ask
questions the JSON schema can express), but it's a materially safer trust boundary between
LLM output and the database, and the schema was designed to cover the full sample query
set (see below).

### Anomaly detection rules

Rule-based rather than ML-based — 500 rows and a 24h window don't justify training
anything, and explicit thresholds are far easier to explain and audit:

1. **Long resolution time**: resolved tickets whose `resolution_time_hrs` exceeds
   `mean + 2*std` among resolved tickets (computed live from the current data, not
   hardcoded).
2. **Stale high-priority**: tickets with `status != Resolved`, `priority` in
   `(High, Critical)`, and more than 24 hours elapsed since `created_at`.

## Model & tools used

- **LLM**: Groq free tier, model `openai/gpt-oss-120b`. (Originally planned around
  `llama-3.3-70b-versatile`, which returned a 404 — no longer served on Groq's API —
  swapped after checking `client.models.list()` for what's currently available.)
- **Data layer**: pandas for CSV ingestion, SQLite for storage/querying.
- **API**: FastAPI + Pydantic.
- **UI**: Streamlit.

## Example queries and outputs

Captured from real runs against the provided dataset:

**"How many tickets are currently open?"**
> There are **111** tickets currently open.

**"Which agent has the lowest average customer rating?"**
> Agent **AGT-08** has the lowest average customer rating, with a rating of **3.48**.

**"What is the average customer rating for Technical category tickets?"**
> The average customer rating for Technical category tickets is **3.74**.

**"Which agent resolved the most tickets?"**
> The agent with the highest number of resolved tickets is **AGT-12**, with 37 tickets resolved.

**"Show me all Critical tickets not resolved within 12 hours."**
> Returns 31 matching tickets (priority=Critical, status != Resolved, more than 12h since
> creation), with the full row data available in `result_rows`.

## API reference

- `GET /health` -> `{"status": "ok", "tickets_loaded": 500}`
- `POST /query` with `{"question": "..."}` -> `{"question", "answer", "result_rows", "query_spec"}`
- `GET /anomalies` -> `{"long_resolution_time": [...], "stale_high_priority": [...], "total_anomalies": N}`

## Known limitations

- The NL->query layer can only express what the `QuerySpec` schema covers (single metric,
  single group-by, a fixed set of filter fields). Genuinely open-ended questions ("summarize
  the biggest problems this quarter") aren't answerable — that would need a different,
  less-constrained architecture (e.g. LLM-written SQL with sandboxing, or RAG over ticket text).
- The dataset is static (Jan-Mar 2024). Anomaly rule 2 (stale high-priority) compares
  `created_at` against the real current time, so on this historical dataset most
  still-open tickets read as "stale" — correct behavior for live data, just an artifact
  of testing against an old fixed snapshot.
- `issue_summary` is free text but isn't used for semantic/similarity search — filtering is
  only on the structured columns (category, priority, status, agent, dates, numeric fields).
- No auth on the API; fine for a local evaluation run, not production-ready as-is.
- Single-process SQLite; would move to a proper server-based DB before any concurrent/
  multi-user use.

## What I'd improve with more time

- Add a lightweight retry/repair loop when the LLM returns an invalid `QuerySpec`, instead
  of failing the request outright.
- Cache repeated questions (same question -> same spec) to cut LLM calls.
- Add semantic search over `issue_summary` for "find tickets about X" style questions.
- Real automated tests (pytest) instead of the manual verification runs logged in
  `DEV_LOG.md`.
