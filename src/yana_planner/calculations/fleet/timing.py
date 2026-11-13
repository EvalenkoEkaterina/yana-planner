"""Integer-hour timing of loading, movement, receipt, return and readiness."""

from yana_planner.domain import loading_hours


def _times(vessel, start, quantity):
    le = start + loading_hours(vessel, quantity)
    ar = le + vessel["up_h"]
    re = ar + vessel["unload_h"]
    rt = re + vessel["return_h"]
    return dict(
        load_end_h=le, arrival_h=ar, receipt_h=re, return_h=rt, ready_h=rt + vessel["prep_h"]
    )
