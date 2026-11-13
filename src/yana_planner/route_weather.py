"""Compatibility import; implementation lives in calculations/constraints/route_weather.py."""

import sys
from importlib import import_module

_implementation = import_module("yana_planner.calculations.constraints.route_weather")
sys.modules[__name__] = _implementation
