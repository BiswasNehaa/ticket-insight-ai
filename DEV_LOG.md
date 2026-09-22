# Dev Log

Running log of each piece built, how it was tested, and when it was committed.

## 1. Project scaffold
- `.gitignore`, `.env.example`, `requirements.txt`, `app/` and `data/` folders.
- Dataset copied from the provided assessment files into `data/support_tickets.csv` (500 rows + header, verified with `wc -l`).

## 2. CSV -> SQLite ingestion (`app/db.py`)
- Loads CSV via pandas, parses `created_at` as datetime, writes to SQLite table `tickets`, adds indexes on `status`, `priority`, `agent_id`.
- Test: ran `python -m app.db` -> confirmed `tickets.db` created and populated without errors.

## 3. Safe query builder (`app/query_builder.py`)
- Converts a structured `QuerySpec` (metric, metric_column, group_by, order, limit, filters) into parameterized SQL. Only whitelisted columns/operators are ever used; no string ever flows from LLM output directly into SQL text.
- Test: manually built two specs and ran them against the real DB:
  - `count` with `status=Open` filter -> correct parameterized query, executed successfully.
  - `avg(customer_rating)` grouped by `agent_id`, ordered ascending, limit 1 (answers "which agent has the lowest average rating") -> correct query, executed successfully.

## 4. Anomaly detection (`app/anomaly.py`)
- Rule 1: resolved tickets with `resolution_time_hrs` > mean + 2*std among resolved tickets.
- Rule 2: tickets with status != Resolved, priority in (High, Critical), and more than 24h elapsed since `created_at`.
- Test: ran `detect_anomalies()` against the real DB -> 17 long-resolution anomalies, 80 stale high-priority anomalies. Spot-checked one result row for correctness of the reason string.
- Note: the dataset is from Jan-Mar 2024; since rule 2 compares against the real current time, most still-unresolved old tickets read as "stale" when tested today. This is expected/correct behavior for live data — flagged as a known limitation in the README.

## 5. LLM integration (`app/llm.py`)
- Groq client, two calls: (a) NL question -> structured JSON filter spec (never raw SQL), (b) result rows -> plain-English answer.
- Initially targeted `llama-3.3-70b-versatile`, which returned a 404 (model no longer served on Groq). Switched to `openai/gpt-oss-120b` after checking `client.models.list()` for what's currently live on the free tier.
- Test: ran 5 of the brief's sample questions end-to-end (NL -> spec -> SQL -> rows -> phrased answer):
  - "How many tickets are currently open?" -> 111, correct.
  - "Which agent has the lowest average customer rating?" -> AGT-08 (3.48), correct.
  - "What is the average customer rating for Technical category tickets?" -> 3.74, correct.
  - "Show me all Critical tickets not resolved within 12 hours." -> list mode, 31 matching rows, correct filter logic (priority=Critical, status!=Resolved, hours since created > 12).
  - "Which agent resolved the most tickets?" -> AGT-12 (37 resolved), correct.

## 6. FastAPI app (`app/main.py`)
- `/health`, `POST /query`, `GET /anomalies`.
- Test: `/health` -> 200, tickets_loaded=500. `/anomalies` -> 200, total_anomalies=97. `/query` with "How many tickets are currently open?" -> 200, correct answer and result_rows.

## 8. Fix: relative-date questions ("this month") resolved against real time, not data time
- Found via live UI test: "Which agent resolved the most tickets this month?" returned
  "No matching tickets were found." Root cause: `today_iso` passed to the LLM was
  `date.today()` (real wall-clock date), but the dataset is a static historical snapshot
  (Jan-Mar 2024) - "this month" resolved to the real current month, which has zero
  overlap with the data.
- Fix: added `get_latest_ticket_date()` in `app/db.py`, returning `MAX(created_at)` from
  the table. Both `app/main.py` and `streamlit_app.py` now pass that as the reference
  date instead of the real date. Correct on this historical dataset, and a no-op
  difference on live data (latest row would already be close to real "now").
- Test: re-ran the failing question -> now returns "AGT-01 resolved the most tickets
  this month, with 16 tickets" (correctly resolving to March 2024). Re-verified an
  unrelated query ("How many tickets are currently open?") still returns 111, unchanged.
  `/health` and `/anomalies` unaffected (no date-relative LLM calls in those paths).
- Also hit Groq's free-tier rate limit (8000 TPM) mid-testing from rapid repeated calls -
  not a code issue, just a real constraint of the free tier worth knowing about live.

## 7. Streamlit UI (`streamlit_app.py`)
- Two tabs: ask a question, run anomaly scan. Imports `app.*` modules directly rather than calling the API over HTTP, so it works standalone.
- Logic shared with `/query` and `/anomalies` (same underlying functions), so it inherits the same test coverage above; not re-tested separately through the Streamlit UI itself yet.
