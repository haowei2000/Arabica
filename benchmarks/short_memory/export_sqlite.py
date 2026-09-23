"""Export a real session from the v1 Rust runtime's SQLite DB to a JSONL fixture.

The v1 runtime (``structure-local-runtime``) stored events in a different schema
than the Python executor's ``_events_to_messages`` expects.  This script
translates one into the other so real sessions can be replayed by the
short-memory benchmark.

Usage::

    uv run python -m benchmarks.short_memory.export_sqlite \\
        --db .structure/local/structure.db \\
        --run-id run_18544_1779849296343622000 \\
        --out benchmarks/short_memory/fixtures/real_session_18544.jsonl

    # auto-pick the run with the most events
    uv run python -m benchmarks.short_memory.export_sqlite \\
        --db .structure/local/structure.db \\
        --out benchmarks/short_memory/fixtures/real_session_largest.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from typing import Any

# ---------------------------------------------------------------------------
# Schema translation: v1 SQLite kind → Python executor EventType payload.
# ---------------------------------------------------------------------------

def _user_message(payload: dict[str, Any]) -> dict[str, Any]:
    return {"message": payload.get("content", "")}


def _agent_message_from_model_responded(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate a v1 ``model_responded`` (tool_planning) into an AGENT_MESSAGE.

    v1 stores tool calls as ``{"call_id", "name", "input"}``; the Python
    executor expects ``{"id", "name", "arguments"}``.
    """
    tc_out: list[dict[str, Any]] = []
    for tc in payload.get("tool_calls", []):
        tc_out.append({
            "id": tc.get("call_id") or tc.get("id", ""),
            "name": tc.get("name", ""),
            "arguments": tc.get("input") or tc.get("arguments") or {},
        })
    return {"content": payload.get("content", ""), "tool_calls": tc_out}


def _agent_message_from_chat(payload: dict[str, Any]) -> dict[str, Any]:
    return {"content": payload.get("content", "")}


def _tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_id": payload.get("call_id", ""),
        "tool_name": payload.get("name", ""),
        "result": payload.get("output"),
    }


def _tool_error(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_id": payload.get("call_id", ""),
        "tool_name": payload.get("name", ""),
        "error_message": payload.get("error") or "Unknown error",
    }


def _translate(rows: list[tuple[int, str, str]]) -> list[dict[str, Any]]:
    """Translate a list of (sequence, kind, payload_json) rows."""
    out: list[dict[str, Any]] = []
    # Track the latest agent response content so we can attach plain-text
    # chat_message_recorded (role=agent) as AGENT_MESSAGE.
    for seq, kind, payload_json in rows:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            continue

        if kind == "chat_message_recorded":
            role = payload.get("role", "user")
            if role == "user":
                out.append({
                    "sequence": seq,
                    "event_type": "user.message",
                    "payload": _user_message(payload),
                })
            else:
                out.append({
                    "sequence": seq,
                    "event_type": "agent.message",
                    "payload": _agent_message_from_chat(payload),
                })

        elif kind == "model_responded":
            phase = payload.get("phase", "")
            if phase == "tool_planning" and payload.get("tool_calls"):
                out.append({
                    "sequence": seq,
                    "event_type": "agent.message",
                    "payload": _agent_message_from_model_responded(payload),
                })
            elif phase == "response_synthesis":
                # Final text response — treat as agent message.
                out.append({
                    "sequence": seq,
                    "event_type": "agent.message",
                    "payload": {"content": payload.get("response") or payload.get("content", "")},
                })

        elif kind == "tool_call_completed":
            if payload.get("success", True):
                out.append({
                    "sequence": seq,
                    "event_type": "tool.result",
                    "payload": _tool_result(payload),
                })
            else:
                out.append({
                    "sequence": seq,
                    "event_type": "tool.error",
                    "payload": _tool_error(payload),
                })

        # All other kinds (workspace_opened, run_created, model_requested,
        # agent_step_planned, knowledge_retrieved, artifact_written,
        # code_change_*, run_finished, ...) are skipped — _events_to_messages
        # ignores them anyway.

    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the v1 SQLite DB.")
    parser.add_argument("--run-id", help="Run to export.  Omit for the largest run.")
    parser.add_argument("--out", required=True, help="Output .jsonl path.")
    parser.add_argument("--exclude-kinds", default="model_requested,agent_step_planned",
                        help="(unused) documentation of kinds that are skipped.")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    if args.run_id is None:
        # Pick the run with the most events.
        row = conn.execute(
            "SELECT run_id, count(*) AS cnt FROM events "
            "WHERE run_id IS NOT NULL GROUP BY run_id ORDER BY cnt DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise SystemExit("No runs found in the DB.")
        args.run_id = row["run_id"]
        print(f"auto-selected largest run: {args.run_id} ({row['cnt']} events)")

    rows = conn.execute(
        "SELECT sequence, kind, payload_json FROM events "
        "WHERE run_id = ? ORDER BY sequence",
        (args.run_id,),
    ).fetchall()

    translated = _translate([(r["sequence"], r["kind"], r["payload_json"]) for r in rows])

    with Path(args.out).open("w", encoding="utf-8") as fh:
        for event in translated:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    print(f"exported {len(translated)} replayable events (from {len(rows)} raw) → {args.out}")
    conn.close()


if __name__ == "__main__":
    main()
