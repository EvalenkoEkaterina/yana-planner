"""Compatibility import; implementation lives in calculations/finance/multi_stop.py."""

import sys
from importlib import import_module

_implementation = import_module("yana_planner.calculations.finance.multi_stop")
sys.modules[__name__] = _implementation
