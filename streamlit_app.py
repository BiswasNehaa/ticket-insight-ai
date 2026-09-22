"""Minimal Streamlit UI for the Ticket Insight AI system.

Uses the same app.* modules as the FastAPI service (not HTTP calls to it),
so this works standalone with `streamlit run streamlit_app.py` even if the
API process isn't running, and there's a single source of truth for logic.
"""
import pandas as pd
import streamlit as st

from app import llm
from app.anomaly import detect_anomalies
from app.db import get_connection, get_latest_ticket_date
from app.query_builder import InvalidQuerySpec, build_sql

st.set_page_config(page_title="Ticket Insight AI", layout="wide")
st.title("🎫 Ticket Insight AI")
st.caption("Ask questions about support tickets in plain English, or check for anomalies.")

tab_query, tab_anomalies = st.tabs(["Ask a question", "Anomalies"])

with tab_query:
    question = st.text_input(
        "Ask about the ticket data",
        placeholder="e.g. Which agent has the lowest average customer rating?",
    )
    if st.button("Ask", type="primary") and question.strip():
        with st.spinner("Thinking..."):
            try:
                conn = get_connection()
                try:
                    reference_date = get_latest_ticket_date(conn)
                    spec = llm.extract_query_spec(question, today_iso=reference_date)
                    sql, params = build_sql(spec)

                    cursor = conn.execute(sql, params)
                    columns = [d[0] for d in cursor.description]
                    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                finally:
                    conn.close()

                answer = llm.phrase_answer(question, rows)
            except (RuntimeError, ValueError, InvalidQuerySpec) as e:
                st.error(str(e))
            else:
                st.success(answer)
                if rows:
                    with st.expander(f"Result data ({len(rows)} row{'s' if len(rows) != 1 else ''})"):
                        st.dataframe(pd.DataFrame(rows))
                with st.expander("Query spec the LLM extracted (debug)"):
                    st.json(spec.__dict__)

with tab_anomalies:
    st.subheader("Detected anomalies")
    if st.button("Run anomaly scan"):
        conn = get_connection()
        try:
            result = detect_anomalies(conn)
        finally:
            conn.close()

        st.metric("Total anomalies", result["total_anomalies"])

        st.markdown("**Abnormally long resolution times**")
        long_res = result["long_resolution_time"]
        if long_res:
            st.dataframe(pd.DataFrame(long_res))
        else:
            st.write("None found.")

        st.markdown("**Unresolved high-priority tickets older than 24h**")
        stale = result["stale_high_priority"]
        if stale:
            st.dataframe(pd.DataFrame(stale))
        else:
            st.write("None found.")
