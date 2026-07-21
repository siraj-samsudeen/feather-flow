"""Shared domain exceptions for feather-flow core modules."""

from __future__ import annotations


class StateDBMissingError(Exception):
    """Raised when an operation requires the state DB but it does not exist on disk."""
