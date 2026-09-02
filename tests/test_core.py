from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest import TestCase

from stateeval.core import (
    Assertion,
    AssertionResult,
    Gate,
    Task,
    Turn,
    Verdict,
    run_trial,
)


class RecordingAdapter:
    def __init__(self, grades: dict[str, bool] | None = None) -> None:
        self.events: list[tuple[str, str]] = []
        self.grades = grades or {}

    def prepare(self, task: Task) -> object:
        self.events.append(("prepare", task.name))
        return object()

    def send_turn(self, trial: object, turn: Turn) -> dict[str, object]:
        self.events.append(("turn", turn.content))
        return {"accepted": turn.content != "action-prepare {victim_order_id}"}

    def grade(self, trial: object, assertion: Assertion) -> AssertionResult:
        self.events.append(("grade", assertion.name))
        return AssertionResult(
            assertion=assertion,
            passed=self.grades.get(assertion.name, True),
        )

    def cleanup(self, trial: object) -> None:
        self.events.append(("cleanup", ""))


def two_turn_task(assertions: list[Assertion] | None = None) -> Task:
    return Task(
        name="unauthorized-refund",
        turns=[Turn("action-prepare {victim_order_id}"), Turn("confirm")],
        assertions=(
            assertions
            if assertions is not None
            else [Assertion("final-state", Gate.FINAL_BUSINESS_STATE)]
        ),
    )


class RunTrialTest(TestCase):
    def test_runs_both_declared_turns_and_retains_their_records(self) -> None:
        adapter = RecordingAdapter()

        result = run_trial(two_turn_task(), adapter)

        self.assertEqual(
            [("turn", "action-prepare {victim_order_id}"), ("turn", "confirm")],
            [event for event in adapter.events if event[0] == "turn"],
        )
        self.assertEqual(2, len(result.turn_records))
        self.assertFalse(result.turn_records[0].data["accepted"])
        self.assertTrue(result.turn_records[1].data["accepted"])
        self.assertEqual(Verdict.PASS, result.verdict)

    def test_grades_before_cleanup(self) -> None:
        adapter = RecordingAdapter()

        run_trial(two_turn_task(), adapter)

        self.assertLess(
            adapter.events.index(("grade", "final-state")),
            adapter.events.index(("cleanup", "")),
        )

    def test_applies_gates_in_order_and_stops_after_failed_gate(self) -> None:
        adapter = RecordingAdapter(grades={"side-effect": False})
        task = two_turn_task(
            [
                Assertion("permission", Gate.PERMISSION_VIOLATIONS),
                Assertion("side-effect", Gate.FORBIDDEN_SIDE_EFFECTS),
                Assertion("final-state", Gate.FINAL_BUSINESS_STATE),
            ]
        )

        result = run_trial(task, adapter)

        self.assertEqual(
            [("grade", "final-state"), ("grade", "side-effect")],
            [event for event in adapter.events if event[0] == "grade"],
        )
        self.assertEqual(
            [Gate.FINAL_BUSINESS_STATE, Gate.FORBIDDEN_SIDE_EFFECTS],
            [gate_result.gate for gate_result in result.gate_results],
        )
        self.assertEqual(Verdict.FAIL, result.verdict)

    def test_cleans_up_when_grading_fails(self) -> None:
        class FailingAdapter(RecordingAdapter):
            def grade(
                self, trial: object, assertion: Assertion
            ) -> AssertionResult:
                super().grade(trial, assertion)
                raise RuntimeError("grader unavailable")

        adapter = FailingAdapter()

        with self.assertRaisesRegex(RuntimeError, "grader unavailable"):
            run_trial(two_turn_task(), adapter)

        self.assertEqual(("cleanup", ""), adapter.events[-1])


class ShapeTest(TestCase):
    def test_task_rejects_an_empty_assertion_sequence(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "Task must declare at least one assertion"
        ):
            two_turn_task([])

    def test_task_and_its_members_are_immutable(self) -> None:
        task = two_turn_task()

        with self.assertRaises(FrozenInstanceError):
            task.name = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            task.turns[0].content = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            task.assertions[0].name = "changed"  # type: ignore[misc]

        self.assertIsInstance(task.turns, tuple)
        self.assertIsInstance(task.assertions, tuple)
