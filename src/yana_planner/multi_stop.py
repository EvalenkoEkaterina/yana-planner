"""Compatibility import; implementation lives in calculations/routes/voyage.py."""

import sys
from importlib import import_module

_implementation = import_module("yana_planner.calculations.routes.voyage")
sys.modules[__name__] = _implementation
