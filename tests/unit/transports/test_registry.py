"""Lazy transport registry — mirrors sources/registry.py."""

from __future__ import annotations

import pytest

from feather_etl.transports.registry import get_transport_class, TRANSPORT_CLASSES


def test_known_names_resolve() -> None:
    for name in ("pyodbc", "arrow-odbc", "connectorx"):
        cls = get_transport_class(name)
        assert cls.name == name


def test_unknown_name_raises_with_listing() -> None:
    with pytest.raises(ValueError) as exc:
        get_transport_class("nope")
    msg = str(exc.value)
    assert "nope" in msg
    for known in ("pyodbc", "arrow-odbc", "connectorx"):
        assert known in msg


def test_registry_is_string_based_for_lazy_import() -> None:
    for v in TRANSPORT_CLASSES.values():
        assert isinstance(v, str)
