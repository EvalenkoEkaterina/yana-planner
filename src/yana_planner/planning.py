"""Compatibility import; implementation lives in calculations/fleet/optimizer.py."""

import sys
from importlib import import_module

_implementation = import_module("yana_planner.calculations.fleet.optimizer")
sys.modules[__name__] = _implementation
