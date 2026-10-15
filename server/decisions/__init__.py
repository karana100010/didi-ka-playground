"""Decision tables (DMN-style) the agents call as tools. See docs/design.html §6."""
from .engine import DecisionError, all_tables, describe, evaluate, inputs_from_rows, table  # noqa: F401
