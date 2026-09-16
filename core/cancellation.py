"""M52 — Cooperative Cancellation Token for Project AURA.

Provides thread-safe cooperative cancellation semantics for in-flight tasks and workflows.
"""

from __future__ import annotations

import threading


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._is_cancelled = threading.Event()

    def cancel(self) -> None:
        """Signal cooperative cancellation."""
        self._is_cancelled.set()

    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self._is_cancelled.is_set()

    def __call__(self) -> bool:
        """Allow token to be evaluated as a callable directly."""
        return self.is_cancelled()
