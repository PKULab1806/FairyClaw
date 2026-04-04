# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Session-level global state management."""

import asyncio
from dataclasses import dataclass, field
from typing import Any


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@dataclass
class SubTaskRecord:
    """Represent one delegated subtask runtime record.

    Attributes:
        sub_session_id (str): Unique sub-session identifier.
        instruction (str): Delegation instruction sent to sub-agent.
        status (str): Runtime status string.
        start_time (float): Unix timestamp when task registration happened.
        summary (str): Terminal or latest summary text.
    """

    sub_session_id: str
    instruction: str
    status: str
    start_time: float
    summary: str = ""
    batch_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize record to a JSON-compatible dictionary.

        Returns:
            dict[str, Any]: Field mapping for persistence or transport.
        """
        return {
            "sub_session_id": self.sub_session_id,
            "instruction": self.instruction,
            "status": self.status,
            "start_time": self.start_time,
            "summary": self.summary,
            "batch_id": self.batch_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubTaskRecord":
        """Deserialize from untyped dictionary payload.

        Args:
            data (dict[str, Any]): Incoming record payload.

        Returns:
            SubTaskRecord: Normalized record with fallback defaults.
        """
        return cls(
            sub_session_id=str(data.get("sub_session_id") or ""),
            instruction=str(data.get("instruction") or ""),
            status=str(data.get("status") or "unknown"),
            start_time=float(data.get("start_time") or 0.0),
            summary=str(data.get("summary") or ""),
            batch_id=int(data.get("batch_id") or 0),
        )


@dataclass
class SessionSubTaskState:
    """Aggregate all subtasks for one main session.

    Attributes:
        records (dict[str, SubTaskRecord]): Subtask records indexed by sub-session ID.
        barrier_notified (bool): One-shot flag indicating terminal aggregation emitted.
    """

    records: dict[str, SubTaskRecord] = field(default_factory=dict)
    barrier_notified: bool = False
    current_batch_id: int = 0
    emitted_batch_ids: set[int] = field(default_factory=set)
    immediate_failure_notified: set[str] = field(default_factory=set)

    def register_task(self, sub_session_id: str, instruction: str, start_time: float) -> None:
        """Register a subtask and open a new aggregation batch when needed.

        Args:
            sub_session_id (str): Sub-session identifier.
            instruction (str): Delegation instruction.
            start_time (float): Registration timestamp.

        Returns:
            None
        """
        if self.records and self.all_terminal(batch_id=self.current_batch_id):
            self.current_batch_id += 1
        self.records[sub_session_id] = SubTaskRecord(
            sub_session_id=sub_session_id,
            instruction=instruction,
            status="running",
            start_time=start_time,
            batch_id=self.current_batch_id,
        )
        self.barrier_notified = False
        self.emitted_batch_ids.discard(self.current_batch_id)
        self.immediate_failure_notified.discard(sub_session_id)

    def is_terminal_status(self, status: str) -> bool:
        """Check whether status belongs to terminal set.

        Args:
            status (str): Candidate status.

        Returns:
            bool: True when status is completed, failed, or cancelled.
        """
        return status in TERMINAL_STATUSES

    def mark_terminal(self, sub_session_id: str, status: str, summary: str = "") -> bool:
        """Transition subtask into terminal state with idempotent behavior.

        Args:
            sub_session_id (str): Target sub-session ID.
            status (str): Requested terminal status.
            summary (str): Optional terminal summary.

        Returns:
            bool: True when transition succeeds; False for missing task or repeated terminal transition.
        """
        record = self.records.get(sub_session_id)
        if record is None:
            return False
        normalized = status.strip().lower() or "completed"
        if normalized not in TERMINAL_STATUSES:
            normalized = "completed"
        if self.is_terminal_status(record.status):
            return False
        record.status = normalized
        if summary:
            record.summary = summary
        return True

    def is_terminal(self, sub_session_id: str) -> bool:
        """Check whether one subtask is terminal.

        Args:
            sub_session_id (str): Target sub-session ID.

        Returns:
            bool: True when record exists and status is terminal.
        """
        record = self.records.get(sub_session_id)
        if record is None:
            return False
        return self.is_terminal_status(record.status)

    def find_matching_task_ids(self, sub_session_id_prefix: str) -> list[str]:
        """Find subtask IDs matching given prefix.

        Args:
            sub_session_id_prefix (str): Prefix string from user/tool input.

        Returns:
            list[str]: Matching sub-session IDs.
        """
        if not sub_session_id_prefix:
            return []
        all_ids = set(self.records.keys())
        return [task_id for task_id in all_ids if task_id.startswith(sub_session_id_prefix)]

    def resolve_task_id(self, sub_session_id_or_prefix: str) -> str | None:
        """Resolve complete subtask ID from exact value or unique prefix.

        Args:
            sub_session_id_or_prefix (str): Exact ID or prefix.

        Returns:
            str | None: Resolved ID when unambiguous, otherwise None.
        """
        if sub_session_id_or_prefix in self.records:
            return sub_session_id_or_prefix
        matches = self.find_matching_task_ids(sub_session_id_or_prefix)
        if len(matches) == 1:
            return matches[0]
        return None

    def get_record(self, sub_session_id: str) -> SubTaskRecord | None:
        """Fetch subtask record by exact ID.

        Args:
            sub_session_id (str): Exact sub-session ID.

        Returns:
            SubTaskRecord | None: Existing record or None.
        """
        return self.records.get(sub_session_id)

    def update_status(self, sub_session_id: str, status: str, summary: str = "") -> None:
        """Update subtask status and optional summary without terminal guard.

        Args:
            sub_session_id (str): Target sub-session ID.
            status (str): New status string.
            summary (str): Optional summary replacement.

        Returns:
            None
        """
        record = self.records.get(sub_session_id)
        if record:
            record.status = status
            if summary:
                record.summary = summary
            if not self.is_terminal_status(status):
                self.barrier_notified = False
                self.emitted_batch_ids.discard(record.batch_id)
                self.immediate_failure_notified.discard(sub_session_id)

    def reopen_task(self, sub_session_id: str, status: str = "running", summary: str = "") -> bool:
        """Reopen a terminal task into running-like state.

        Args:
            sub_session_id (str): Target sub-session ID.
            status (str): Reopened status.
            summary (str): Optional summary update.

        Returns:
            bool: True when task was terminal and got reopened; False otherwise.
        """
        record = self.records.get(sub_session_id)
        if record is None:
            return False
        if self.all_terminal(batch_id=self.current_batch_id):
            self.current_batch_id += 1
        if not self.is_terminal_status(record.status):
            record.status = status
            if summary:
                record.summary = summary
            record.batch_id = self.current_batch_id
            self.barrier_notified = False
            self.emitted_batch_ids.discard(record.batch_id)
            self.immediate_failure_notified.discard(sub_session_id)
            return False
        record.status = status
        record.summary = summary
        record.batch_id = self.current_batch_id
        self.barrier_notified = False
        self.emitted_batch_ids.discard(record.batch_id)
        self.immediate_failure_notified.discard(sub_session_id)
        return True

    def active_count(self) -> int:
        """Count active running subtasks.

        Returns:
            int: Number of tasks whose status starts with ``running``.
        """
        return sum(1 for record in self.records.values() if record.status.startswith("running"))

    def list_records(self, batch_id: int | None = None) -> list[SubTaskRecord]:
        """Return all subtask records as list snapshot.

        Returns:
            list[SubTaskRecord]: Current records in insertion-map order.
        """
        records = list(self.records.values())
        if batch_id is None:
            return records
        return [record for record in records if record.batch_id == batch_id]

    def all_terminal(self, batch_id: int | None = None) -> bool:
        """Check whether all registered subtasks are terminal.

        Returns:
            bool: False when no records; otherwise True only if every record is terminal.
        """
        records = self.list_records(batch_id=batch_id)
        if not records:
            return False
        return all(self.is_terminal_status(record.status) for record in records)

    def is_all_subtasks_terminal(self) -> bool:
        """Compatibility wrapper for all_terminal.

        Returns:
            bool: Same result as ``all_terminal``.
        """
        return self.all_terminal(batch_id=self.current_batch_id)

    def consume_aggregation_emitted(self) -> bool:
        """Consume one-shot barrier emission flag.

        Returns:
            bool: True when barrier was already emitted before this call.
        """
        if self.current_batch_id in self.emitted_batch_ids:
            return True
        self.barrier_notified = True
        self.emitted_batch_ids.add(self.current_batch_id)
        return False

    def has_aggregation_emitted(self) -> bool:
        """Check whether barrier aggregation has been emitted.

        Returns:
            bool: True when barrier has been emitted.
        """
        return self.current_batch_id in self.emitted_batch_ids

    def mark_aggregation_emitted(self) -> None:
        """Mark barrier aggregation as emitted.

        Returns:
            None
        """
        self.barrier_notified = True
        self.emitted_batch_ids.add(self.current_batch_id)

    def has_immediate_failure_notified(self, sub_session_id: str) -> bool:
        """Check whether immediate failure notification was sent.

        Args:
            sub_session_id (str): Target sub-session ID.

        Returns:
            bool: True when immediate failure notification has been sent.
        """
        return sub_session_id in self.immediate_failure_notified

    def mark_immediate_failure_notified(self, sub_session_id: str) -> None:
        """Mark immediate failure notification as sent for one subtask.

        Args:
            sub_session_id (str): Target sub-session ID.

        Returns:
            None
        """
        if sub_session_id:
            self.immediate_failure_notified.add(sub_session_id)

    def get_aggregated_subtask_results(self, batch_id: int | None = None) -> dict[str, Any]:
        """Build aggregate subtask counters and summaries.

        Returns:
            dict[str, Any]: Statistics payload including total/completed/failed/cancelled and summaries map.
        """
        target_batch = self.current_batch_id if batch_id is None else batch_id
        summaries: dict[str, str] = {}
        completed = 0
        failed = 0
        cancelled = 0
        records = self.list_records(batch_id=target_batch)
        for record in records:
            sub_session_id = record.sub_session_id
            summaries[sub_session_id] = record.summary
            if record.status == "completed":
                completed += 1
            elif record.status == "failed":
                failed += 1
            elif record.status == "cancelled":
                cancelled += 1
        return {
            "total": len(records),
            "completed": completed,
            "failed": failed,
            "cancelled": cancelled,
            "summaries": summaries,
        }

    def get_current_batch_records(self) -> list[SubTaskRecord]:
        """Return records belonging to current batch.

        Returns:
            list[SubTaskRecord]: Current-batch subtask records.
        """
        return self.list_records(batch_id=self.current_batch_id)

    def build_barrier_message(self) -> str | None:
        """Build one-shot barrier system message after all subtasks terminate.

        Returns:
            str | None: Aggregated user-visible system message, or None when barrier is not ready/already sent.
        """
        if self.barrier_notified:
            return None
        if not self.all_terminal(batch_id=self.current_batch_id):
            return None
        lines: list[str] = []
        for record in sorted(self.get_current_batch_records(), key=lambda item: item.start_time):
            summary = record.summary or "(no summary)"
            lines.append(f"[{record.status}] {record.sub_session_id}\n{summary}")
        self.barrier_notified = True
        self.emitted_batch_ids.add(self.current_batch_id)
        return "[System Notification] Background tasks completed:\n\n" + "\n\n---\n\n".join(lines)


_session_locks: dict[str, asyncio.Lock] = {}
_subtask_states: dict[str, SessionSubTaskState] = {}
_sub_session_to_main: dict[str, str] = {}
_sub_session_cancel_requested: set[str] = set()


def get_session_lock(session_id: str) -> asyncio.Lock:
    """Get or lazily create session mutex lock.

    Args:
        session_id (str): Session identifier.

    Returns:
        asyncio.Lock: Dedicated lock instance for this session.
    """
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


def get_or_create_subtask_state(main_session_id: str) -> SessionSubTaskState:
    """Get or lazily create subtask state for one main session.

    Args:
        main_session_id (str): Main session identifier.

    Returns:
        SessionSubTaskState: Mutable state container for session subtasks.
    """
    state = _subtask_states.get(main_session_id)
    if state is None:
        state = SessionSubTaskState()
        _subtask_states[main_session_id] = state
    return state


def bind_sub_session(main_session_id: str, sub_session_id: str) -> None:
    """Bind sub-session to owning main session.

    Args:
        main_session_id (str): Main session identifier.
        sub_session_id (str): Sub-session identifier.

    Returns:
        None
    """
    _sub_session_to_main[sub_session_id] = main_session_id


def get_main_session_by_sub_session(sub_session_id: str) -> str | None:
    """Look up main session ID from sub-session mapping.

    Args:
        sub_session_id (str): Sub-session identifier.

    Returns:
        str | None: Main session ID when mapping exists.
    """
    return _sub_session_to_main.get(sub_session_id)


def request_sub_session_cancel(sub_session_id: str) -> None:
    """Mark one sub-session as cancellation-requested.

    Args:
        sub_session_id (str): Target sub-session identifier.

    Returns:
        None
    """
    if sub_session_id:
        _sub_session_cancel_requested.add(sub_session_id)


def clear_sub_session_cancel(sub_session_id: str) -> None:
    """Clear cancellation-requested mark for one sub-session.

    Args:
        sub_session_id (str): Target sub-session identifier.

    Returns:
        None
    """
    if sub_session_id:
        _sub_session_cancel_requested.discard(sub_session_id)


def is_sub_session_cancel_requested(sub_session_id: str) -> bool:
    """Check whether one sub-session has pending cancellation request.

    Args:
        sub_session_id (str): Target sub-session identifier.

    Returns:
        bool: True when cancellation is requested.
    """
    if not sub_session_id:
        return False
    return sub_session_id in _sub_session_cancel_requested
