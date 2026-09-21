"""FastAPI app exposing the ticket insight system.

Endpoints:
  GET  /health     - liveness/readiness check
  POST /query       - natural language question -> data-grounded answer
  GET  /anomalies  - rule-based anomaly detection results
"""
from datetime import date

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app import llm
from app.anomaly import detect_anomalies
from app.db import DB_PATH, get_connection, load_csv_to_db
from app.query_builder import InvalidQuerySpec, build_sql

app = FastAPI(title="Ticket Insight AI", version="1.0")


@app.on_event("startup")
def startup() -> None:
    if not DB_PATH.exists():
        load_csv_to_db()


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    question: str
    answer: str
    result_rows: list[dict]
    query_spec: dict


@app.get("/health")
def health() -> dict:
    try:
        conn = get_connection()
        count = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        conn.close()
        return {"status": "ok", "tickets_loaded": count}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database not ready: {e}")


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    if not req.question or not req.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    try:
        spec = llm.extract_query_spec(req.question, today_iso=date.today().isoformat())
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"Could not interpret question: {e}")

    try:
        sql, params = build_sql(spec)
    except InvalidQuerySpec as e:
        raise HTTPException(status_code=422, detail=f"Invalid query spec from LLM: {e}")

    conn = get_connection()
    try:
        cursor = conn.execute(sql, params)
        columns = [d[0] for d in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query execution failed: {e}")
    finally:
        conn.close()

    answer = llm.phrase_answer(req.question, rows)

    return QueryResponse(
        question=req.question,
        answer=answer,
        result_rows=rows,
        query_spec=spec.__dict__,
    )


@app.get("/anomalies")
def anomalies() -> dict:
    conn = get_connection()
    try:
        return detect_anomalies(conn)
    finally:
        conn.close()
