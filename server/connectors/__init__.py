"""Connectors to source systems. Each one exposes tables() and rows(); only the playground server calls them.

Every connector is read-only. Table and column names are checked against the source's own schema before
any query, and filter values are always bound as parameters.
"""
from .base import Connector, ConnectorError  # noqa: F401
