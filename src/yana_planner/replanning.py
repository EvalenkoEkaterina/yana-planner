"""Compatibility import; implementation lives in calculations/constraints/events.py."""

import sys
from importlib import import_module

_implementation = import_module("yana_planner.calculations.constraints.events")
sys.modules[__name__] = _implementation
