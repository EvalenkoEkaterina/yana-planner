import unittest
from datetime import timedelta

from yana_planner.navigation import (
    COLUMNS,
    SECTION,
    NavigationError,
    check_voyage,
    estimate_history,
    moment,
    read_history,
)


def history(*rows):
    return read_history(
        ",".join(COLUMNS)
        + "\n"
        + "\n".join(
            f"{year},{day},{section},{event},source-{year}" for year, day, section, event in rows
        )
    )


class NavigationTests(unittest.TestCase):
    def test_estimate_filters_year_section_and_event_and_deduplicates(self):
        rows = history(
            (2024, "2024-09-20", SECTION, "navigation_aids_removed"),
            (2025, "2025-10-10", SECTION, "navigation_aids_removed"),
            (2025, "2025-10-10", SECTION, "navigation_aids_removed"),
            (2026, "2026-01-01", SECTION, "navigation_aids_removed"),
            (2027, "2027-01-01", SECTION, "navigation_aids_removed"),
            (2023, "2023-01-01", "Другой участок", "navigation_aids_removed"),
            (2022, "2022-01-01", SECTION, "traffic_ended"),
        )
        estimate = estimate_history(rows, 2026, "navigation_aids_removed")
        self.assertEqual(estimate["years"], [2024, 2025])
        self.assertEqual(estimate["n"], 2)
        self.assertEqual(estimate["median"], moment("2026-09-30"))
        self.assertEqual(estimate["p20"], moment("2026-09-24"))
        self.assertEqual(estimate["excluded"], dict(section=1, event=1, year=2, duplicates=1))
        self.assertEqual(estimate_history(rows, 2026, "traffic_ended")["years"], [2022])

    def test_leap_years_do_not_shift_same_autumn_date(self):
        rows = history(*[(y, f"{y}-09-30", SECTION, "traffic_ended") for y in (2020, 2021, 2024)])
        estimate = estimate_history(rows, 2026, "traffic_ended")
        self.assertEqual(estimate["median"], moment("2026-09-30"))
        self.assertEqual(estimate["p20"], moment("2026-09-30"))

    def test_interpolation_retains_time_and_feb29_rule_is_explicit(self):
        rows = history(
            (2024, "2024-09-29", SECTION, "traffic_ended"),
            (2025, "2025-09-30", SECTION, "traffic_ended"),
        )
        self.assertEqual(
            estimate_history(rows, 2026, "traffic_ended")["median"], moment("2026-09-29T12:00")
        )
        leap = history((2024, "2024-02-29", SECTION, "traffic_ended"))
        self.assertEqual(estimate_history(leap, 2026, "traffic_ended")["p20"], moment("2026-02-28"))

    def test_empty_single_and_conflicting_observations(self):
        empty = estimate_history([], 2026, "traffic_ended")
        self.assertIsNone(empty["median"])
        self.assertIsNone(empty["p20"])
        rows = history((2025, "2025-09-30", SECTION, "traffic_ended"))
        one = estimate_history(rows, 2026, "traffic_ended")
        self.assertEqual(one["median"], one["p20"])
        self.assertTrue(one["small_sample"])
        rows += history((2025, "2025-10-01", SECTION, "traffic_ended"))
        with self.assertRaises(NavigationError):
            estimate_history(rows, 2026, "traffic_ended")

    def test_csv_bom_semicolon_and_bad_rows(self):
        self.assertEqual(read_history("\ufeff" + ";".join(COLUMNS) + "\n"), [])
        for text in (
            "year,date\n2024,2024-09-30",
            ",".join(COLUMNS) + f"\n2024,2025-09-30,{SECTION},traffic_ended,x",
            ",".join(COLUMNS) + f"\n2024,2024-09-30,{SECTION},unknown,x",
            ",".join(COLUMNS) + f"\n2024,2024-09-30,{SECTION},traffic_ended,",
            ",".join(COLUMNS) + f"\n2024,2024-02-30,{SECTION},traffic_ended,x",
        ):
            with self.subTest(text=text), self.assertRaises(NavigationError):
                read_history(text)

    def test_boundary_equality_latest_departure_and_lateness(self):
        boundary = moment("2026-09-30")
        departure = moment("2026-09-29T00:00")
        result = check_voyage(departure, boundary, "8", "2", "10", "4")
        self.assertTrue(result["fits"])
        self.assertEqual(result["return_at"], moment("2026-09-29T20:00"))
        self.assertEqual(result["with_reserve_at"], boundary)
        self.assertEqual(result["latest_departure"], departure)
        self.assertEqual(result["late_hours"], 0)
        late = check_voyage(departure + timedelta(minutes=30), boundary, 8, 2, 10, 4)
        self.assertFalse(late["fits"])
        self.assertEqual(late["late_hours"], 0.5)

    def test_calendar_crosses_new_year_and_respects_timezone(self):
        result = check_voyage(moment("2025-12-31T18:00"), moment("2026-01-02"), 10, 2, 10, 2)
        self.assertEqual(result["return_at"], moment("2026-01-01T16:00"))
        self.assertTrue(result["fits"])
        self.assertEqual(moment("2026-09-29T14:00:00Z"), moment("2026-09-30"))
        self.assertEqual(
            check_voyage(moment("2026-01-01"), moment("2026-01-01"), 0, 0, 0, 0)[
                "latest_departure"
            ],
            moment("2026-01-01"),
        )

    def test_required_durations_do_not_silently_become_zero(self):
        for value in ("", None, True, "NaN", "Infinity", "-1", "87601"):
            with self.subTest(value=value), self.assertRaises(NavigationError):
                check_voyage(moment("2026-09-01"), moment("2026-09-30"), value, 1, 1, 1)
