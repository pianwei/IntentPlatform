"""Synthetic business catalog for the local intent-planning demonstration.

Nothing in this package describes a real bank customer, function, or endpoint.
"""

from .catalog import action_definitions, domain_definition, function_packages
from .engine import execute, initial_state, reconcile
from .skills import business_skill_templates

__all__ = [
    "action_definitions", "business_skill_templates", "domain_definition",
    "execute", "function_packages", "initial_state", "reconcile",
]
