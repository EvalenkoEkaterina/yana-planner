"""Compatibility import; implementation lives in calculations/finance/costs.py."""

import sys
from importlib import import_module

_implementation = import_module("yana_planner.calculations.finance.costs")
sys.modules[__name__] = _implementation
