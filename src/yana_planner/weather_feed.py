"""Compatibility import; implementation lives in calculations/constraints/weather.py."""

import sys
from importlib import import_module

_implementation = import_module("yana_planner.calculations.constraints.weather")
sys.modules[__name__] = _implementation
