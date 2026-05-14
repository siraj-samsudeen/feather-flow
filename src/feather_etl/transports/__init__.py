"""Pluggable batch-stream transports — #61."""

from feather_etl.transports.base import Transport
from feather_etl.transports.registry import get_transport_class

__all__ = [
    "Transport",
    "get_transport_class",
]
