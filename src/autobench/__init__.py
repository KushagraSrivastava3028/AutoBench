"""Autobench — a containerised, parallel evaluation platform for LLM agents.

The single design commitment everything else follows from:

    A trial is a pure function of (task version, agent version, model version, seed)
    and it leaves nothing behind.
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__"]
