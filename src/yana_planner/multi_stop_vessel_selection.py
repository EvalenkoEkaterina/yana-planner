"""Compatibility import; implementation lives in calculations/fleet/vessel_selection.py."""

import sys
from importlib import import_module

_implementation = import_module("yana_planner.calculations.fleet.vessel_selection")
sys.modules[__name__] = _implementation
