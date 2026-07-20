"""Shared error types across backends.

Every backend raises its own subclass; the server catches BackendError so a
codex failure flows through the same taxonomy/failover machinery as a claude
one. ClaudeError stays importable from claude.py (unchanged call sites).
"""


class BackendError(RuntimeError):
    """A user-facing failure from a local backend CLI run."""
