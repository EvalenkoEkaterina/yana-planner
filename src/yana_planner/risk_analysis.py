"""Compatibility import; implementation lives in calculations/constraints/risk.py."""

import sys
from importlib import import_module

_implementation = import_module("yana_planner.calculations.constraints.risk")
sys.modules[__name__] = _implementation
