import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from yana_planner.cli import main
from yana_planner.domain import InputError
from yana_planner.experiments import summarize_experiment
from yana_planner.validation import check_schedule

ROOT = Path(__file__).resolve().parents[1]


def verdict(*, tonnes=1650, trips=6, last=276, mean=136, busy=510, shortfall=0):
    return dict(
        status="valid_simulation",
        accepted_t=tonnes,
        shortfall_t=shortfall,
        trip_count=trips,
        last_receipt_h=last,
        weighted_mean_receipt_h=mean,
        busy_vessel_h=busy,
    )


def comparison():
    return dict(
        scenario_id="R03",
        deadline_h=328,
        baseline=verdict(),
        planner=verdict(trips=5, last=224, mean=146, busy=404),
    )


class SummaryTests(unittest.TestCase):
    def test_real_r03_marks_later_mean_as_tradeoff(self):
        cfg = json.loads((ROOT / "examples/r03.json").read_text())
        reference = json.loads((ROOT / "examples/r03.reference.json").read_text())
        result = dict(scenario_id="R03", deadline_h=328)
        for key in ["baseline", "planner"]:
            result[key] = check_schedule(cfg, reference[key]["trips"], 328)
        row = summarize_experiment(result)[0]
        self.assertEqual(row["delta_t"], 0)
        self.assertEqual(row["trips_saved"], 1)
        self.assertEqual(row["hours_saved"], 52)
        self.assertEqual(row["busy_vessel_hours_saved"], 106)
        self.assertAlmostEqual(row["mean_receipt_hours_saved"], -9.7272727272727)
        self.assertTrue(row["tradeoff"])
        self.assertTrue(row["READY_target_met"])

    def test_unequal_volume_does_not_claim_time_or_trip_savings(self):
        result = comparison()
        result["baseline"] = verdict(tonnes=1600, shortfall=50)
        row = summarize_experiment(result)[0]
        self.assertEqual(row["delta_t"], 50)
        self.assertFalse(row["equal_volume"])
        self.assertFalse(row["READY_target_met"])
        self.assertIsNone(row["trips_saved"])
        self.assertIsNone(row["hours_saved"])
        self.assertIsNone(row["tradeoff"])

    def test_invalid_diagnostic_metrics_are_not_accepted_results(self):
        result = comparison()
        result["planner"].update(status="invalid", accepted_t=1650)
        row = summarize_experiment(result)[0]
        self.assertEqual(row["MILP_status"], "invalid")
        for key in ["MILP_t", "MILP_trips", "MILP_last_receipt_h", "delta_t", "trips_saved"]:
            self.assertIsNone(row[key])

    def test_missing_result_is_distinct_from_zero(self):
        result = comparison()
        result["planner"] = None
        row = summarize_experiment(result)[0]
        self.assertEqual(row["MILP_status"], "missing_result")
        self.assertIsNone(row["MILP_t"])
        self.assertIsNone(row["equal_volume"])

    def test_known_zero_difference_is_preserved(self):
        result = comparison()
        result["planner"] = copy.deepcopy(result["baseline"])
        row = summarize_experiment(result)[0]
        self.assertEqual(row["hours_saved"], 0)
        self.assertFalse(row["tradeoff"])

    def test_fewer_trips_but_later_completion_is_tradeoff(self):
        result = comparison()
        result["planner"]["last_receipt_h"] = 300
        self.assertTrue(summarize_experiment(result)[0]["tradeoff"])

    def test_all_measured_metrics_improve_without_tradeoff(self):
        result = comparison()
        result["planner"]["weighted_mean_receipt_h"] = 120
        self.assertFalse(summarize_experiment(result)[0]["tradeoff"])

    def test_incomplete_metrics_do_not_prove_absence_of_tradeoff(self):
        result = comparison()
        del result["planner"]["weighted_mean_receipt_h"]
        row = summarize_experiment(result)[0]
        self.assertIsNone(row["mean_receipt_hours_saved"])
        self.assertIsNone(row["tradeoff"])

    def test_replanning_has_separate_before_and_after(self):
        initial = comparison()
        revised_a = verdict(last=284, mean=161)
        revised_b = verdict(trips=5, last=272, mean=159, busy=404)
        result = dict(
            scenario_id="R03",
            deadline_h=328,
            initial=initial,
            revised_baseline=dict(result=revised_a),
            revised_planner=dict(result=revised_b),
        )
        original = copy.deepcopy(result)
        rows = summarize_experiment(result)
        self.assertEqual([r["phase"] for r in rows], ["before", "after"])
        self.assertEqual([r["hours_saved"] for r in rows], [52, 12])
        self.assertTrue(rows[0]["tradeoff"])
        self.assertFalse(rows[1]["tradeoff"])
        self.assertEqual(result, original)

    def test_mismatched_before_after_deadlines_rejected(self):
        with self.assertRaises(InputError):
            summarize_experiment(dict(deadline_h=300, initial=comparison()))

    def test_unknown_delivery_does_not_become_equal_volume(self):
        result = comparison()
        result["planner"]["accepted_t"] = None
        row = summarize_experiment(result)[0]
        self.assertIsNone(row["delta_t"])
        self.assertIsNone(row["equal_volume"])
        self.assertIsNone(row["MILP_target_met"])

    def test_nonfinite_negative_and_boolean_metrics_rejected(self):
        for value in [float("nan"), float("inf"), -1, True]:
            with self.subTest(value=value), self.assertRaises(InputError):
                result = comparison()
                result["planner"]["accepted_t"] = value
                summarize_experiment(result)

    def test_money_and_real_admissibility_are_not_inferred(self):
        result = comparison()
        result["actual_savings_rub"] = 100000
        row = summarize_experiment(result)[0]
        self.assertIsNone(row["actual_savings_rub"])
        self.assertEqual(row["real_admissibility"], "requires_confirmation")

    def test_cli_exports_summary(self):
        with tempfile.TemporaryDirectory() as d:
            source, target = Path(d) / "result.json", Path(d) / "summary.json"
            source.write_text(json.dumps(comparison()))
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["summarize", str(source), "--out", str(target)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(target.read_text())[0]["trips_saved"], 1)

    def test_cli_invalid_result_exports_nulls_and_failure_exit(self):
        with tempfile.TemporaryDirectory() as d:
            result = comparison()
            result["planner"]["status"] = "invalid"
            source, target = Path(d) / "result.json", Path(d) / "summary.json"
            source.write_text(json.dumps(result))
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["summarize", str(source), "--out", str(target)])
            self.assertEqual(code, 2)
            self.assertIsNone(json.loads(target.read_text())[0]["trips_saved"])
