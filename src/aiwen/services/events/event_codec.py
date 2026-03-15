"""Event type codec: bidirectional mapping between EventType and single-char codes.

Each EventType is encoded as a single character so routing decisions in the
worker can use compact regex patterns on a run's event-history string instead
of repeatedly comparing full event_type name strings.

Code ranges
-----------
Digits 0–9, 'a'/'b'  Tier-3 sequence-tracked (runtime-gated, written to DB)
Uppercase A–F         Tier-1 observation-only (never trigger executor logic)
Lowercase g–k         Tier-2 task events
Lowercase l–o         Tier-2 artifact events
Lowercase p–u         Tier-2 workspace / context events

Unknown event types encode as ``"_"``.

Public API
----------
encode(event_type)          str  → single-char code
decode(code)                str  → EventType string  (or "" if unknown)
encode_sequence(rows)       iterable of (event_type,) rows → compact string
"""

from __future__ import annotations

import re
from typing import Iterable

from aiwen.core.enums import EventType

# ── Forward map: EventType string → single-char code ─────────────────────────
EVENT_CODE: dict[str, str] = {
    # ── Tier-3: sequence-tracked ──────────────────────────────────────────
    EventType.USER_MESSAGE:                 "0",
    EventType.RUN_STATE_CHANGE:             "1",
    EventType.RUN_COMPLETED:                "2",
    EventType.RUN_FAILED:                   "3",
    EventType.RUN_CANCELLED:                "4",
    EventType.TOOL_CALL:                    "5",
    EventType.TOOL_RESULT:                  "6",
    EventType.TOOL_ERROR:                   "7",
    EventType.TOOL_PENDING:                 "8",
    EventType.TOOL_CLIENT_REQUEST:          "9",
    EventType.USER_FEEDBACK:                "a",
    # Internal routing event (worker-only, not visible to executors)
    EventType.TO_EXECUTOR:                  "b",
    # ── Tier-1: observation-only ──────────────────────────────────────────
    EventType.AGENT_TOKEN:                  "A",
    EventType.AGENT_MESSAGE:                "B",
    EventType.AGENT_THINKING:               "C",
    EventType.AGENT_PLAN_STEP:              "D",
    EventType.AGENT_HEARTBEAT:              "E",
    EventType.RUN_CREATED:                  "F",
    # ── Tier-2: task events ───────────────────────────────────────────────
    EventType.TASK_CREATE:                  "g",
    EventType.TASK_UPDATE:                  "h",
    EventType.TASK_DELETE:                  "i",
    EventType.TASK_COMPLETE:                "j",
    EventType.TASK_ASSIGN:                  "k",
    # ── Tier-2: artifact events ───────────────────────────────────────────
    EventType.ARTIFACT_CREATE:              "l",
    EventType.ARTIFACT_UPDATE:              "m",
    EventType.ARTIFACT_DELETE:              "n",
    EventType.ARTIFACT_VERSION:             "o",
    # ── Tier-2: workspace / context events ───────────────────────────────
    EventType.WORKSPACE_CREATED:            "p",
    EventType.WORKSPACE_UPDATED:            "q",
    EventType.WORKSPACE_MEMBER_JOIN:        "r",
    EventType.WORKSPACE_MEMBER_LEAVE:       "s",
    EventType.WORKSPACE_MEMBER_ROLE_CHANGE: "t",
    EventType.USING_CONTEXT:                "u",
}

# ── Reverse map: single-char code → EventType string ─────────────────────────
_CODE_EVENT: dict[str, str] = {v: k for k, v in EVENT_CODE.items()}

_UNKNOWN = "_"


def encode(event_type: str) -> str:
    """Return the single-char code for *event_type*, or ``"_"`` if unknown."""
    return EVENT_CODE.get(event_type, _UNKNOWN)


def decode(code: str) -> str:
    """Return the EventType string for *code*, or ``""`` if unknown."""
    return _CODE_EVENT.get(code, "")


def encode_sequence(rows: Iterable[tuple[str, ...]]) -> str:
    """Convert an ordered sequence of ``(event_type, ...)`` rows to a compact string.

    Each row's first element is encoded via :func:`encode`.  The resulting
    string can be matched with simple regexes without any escaping, e.g.::

        RE_STARTED.search(encode_sequence(rows))   # checks for "1"
    """
    return "".join(encode(row[0]) for row in rows)


# ── Sequence-string patterns (applied to a full run history string) ───────────

# run.state.change (1) present → run has been started at least once.
RE_STARTED = re.compile(r"1")
# run.completed (2) / run.failed (3) / run.cancelled (4) → run is terminal.
RE_TERMINAL = re.compile(r"[234]")
# user.message (0) present → at least one user turn has been received.
RE_USER_MSG = re.compile(r"0")

# ── Single-code routing patterns (applied to one event's code) ────────────────

# Tier-1: no-op codes — agent outputs (A–F) and run state bookkeeping (1–3).
RE_CODE_NOOP = re.compile(r"^[ABCDEF123]$")
# Tier-2: task / artifact / workspace-context codes.
RE_CODE_TASK = re.compile(r"^[ghijk]$")
RE_CODE_ARTIFACT = re.compile(r"^[lmno]$")
RE_CODE_WORKSPACE = re.compile(r"^[pqrstu]$")
# Tier-3: codes forwarded directly to the executor.
RE_CODE_FORWARD = re.compile(r"^[06789a]$")
# Tier-3: trigger-sourced codes that must be skipped (already executed).
RE_CODE_SKIP_SRC = re.compile(r"^[567]$")
