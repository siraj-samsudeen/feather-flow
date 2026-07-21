"""Lazy transport-name → class map. Mirrors sources/registry.py."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from feather_flow.transports.base import Transport


TRANSPORT_CLASSES: dict[str, str] = {
    "pyodbc": "feather_flow.transports.pyodbc_transport.PyodbcTransport",
    "arrow-odbc": "feather_flow.transports.arrow_odbc_transport.ArrowOdbcTransport",
    "connectorx": "feather_flow.transports.connectorx_transport.ConnectorxTransport",
}


def get_transport_class(name: str) -> type["Transport"]:
    if name not in TRANSPORT_CLASSES:
        raise ValueError(
            f"Unknown transport '{name}'. Available: {sorted(TRANSPORT_CLASSES)}"
        )
    module_path, cls_name = TRANSPORT_CLASSES[name].rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)
