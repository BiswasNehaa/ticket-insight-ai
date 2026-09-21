"""Groq LLM integration: NL question -> structured QuerySpec JSON, and
final result rows -> a plain-English answer.

The LLM's job is strictly to (1) extract structured filters and (2) phrase
a human-readable answer from data we already computed. It never generates
or executes SQL itself — see query_builder.py for why.
"""
import json
import os

from dotenv import load_dotenv
from groq import Groq

from app.query_builder import ALLOWED_GROUP_BY, ALLOWED_METRICS, CATEGORICAL_COLUMNS, NUMERIC_COLUMNS, QuerySpec

load_dotenv()

MODEL = "openai/gpt-oss-120b"

_client: Groq | None = None


def get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set. Copy .env.example to .env and add your key.")
        _client = Groq(api_key=api_key)
    return _client


EXTRACTION_SYSTEM_PROMPT = f"""You convert a natural-language question about a customer support ticket \
dataset into a strict JSON filter spec. You NEVER write SQL. You only fill in the fields below.

Table columns: ticket_id, created_at (datetime), category ({{Billing, Technical, General}}), \
priority ({{Low, Medium, High, Critical}}), status ({{Open, Resolved, Escalated}}), \
response_time_hrs (float), resolution_time_hrs (float, null if unresolved), \
agent_id (string like AGT-03), customer_rating (int 1-5, null if unresolved), issue_summary (text).

Today's date context is provided in the user message if relevant.

Respond with ONLY a JSON object with this exact shape (omit fields you don't need, use null where unused):
{{
  "metric": one of {sorted(ALLOWED_METRICS)},
  "metric_column": one of {sorted(NUMERIC_COLUMNS)} or null (required unless metric is "count" or "list"),
  "group_by": one of {sorted(ALLOWED_GROUP_BY)} or null,
  "order": "asc" | "desc" | null (use when the question asks for "lowest"/"highest"/"most"/"least"),
  "limit": integer or null (use 1 for "which agent has the ..." questions),
  "filters": {{
    "status": "Open" | "Resolved" | "Escalated" | null,
    "priority": "Low" | "Medium" | "High" | "Critical" | null,
    "category": "Billing" | "Technical" | "General" | null,
    "agent_id": string or null,
    "created_after": "YYYY-MM-DD" or null,
    "created_before": "YYYY-MM-DD" or null,
    "response_time_hrs_gt": number or null,
    "response_time_hrs_lt": number or null,
    "resolution_time_hrs_gt": number or null,
    "resolution_time_hrs_lt": number or null,
    "customer_rating_gt": number or null,
    "customer_rating_lt": number or null,
    "unresolved_older_than_hrs": number or null (use for "not resolved within N hours" style questions)
  }}
}}

Use metric "list" when the question asks to "show"/"list" tickets rather than asking for a count/average/etc.
Use metric "count" with group_by set and order "desc"/limit 1 for "which agent/category has the most ..." questions.
Use metric "avg"/"min"/"max"/"sum" with the relevant metric_column for aggregate questions.
"""


def extract_query_spec(question: str, today_iso: str) -> QuerySpec:
    client = get_client()
    completion = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": f"Today's date: {today_iso}\nQuestion: {question}"},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    raw = completion.choices[0].message.content
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON: {raw}") from e

    return QuerySpec(
        metric=data.get("metric", "count"),
        metric_column=data.get("metric_column"),
        group_by=data.get("group_by"),
        order=data.get("order"),
        limit=data.get("limit"),
        filters={k: v for k, v in (data.get("filters") or {}).items() if v is not None},
    )


def phrase_answer(question: str, rows: list[dict]) -> str:
    client = get_client()
    completion = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "You answer a user's question about support ticket data using ONLY the "
                "JSON result rows provided. Be concise (1-3 sentences). Do not invent numbers "
                "that aren't in the data. If the rows are empty, say no matching tickets were found.",
            },
            {
                "role": "user",
                "content": f"Question: {question}\nResult data (JSON): {json.dumps(rows, default=str)}",
            },
        ],
        temperature=0,
    )
    return completion.choices[0].message.content.strip()
