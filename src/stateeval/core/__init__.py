from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeVar


class Gate(str, Enum):
    FINAL_BUSINESS_STATE = "final_business_state"
    FORBIDDEN_SIDE_EFFECTS = "forbidden_side_effects"
    PERMISSION_VIOLATIONS = "permission_violations"


GATE_ORDER = (
    Gate.FINAL_BUSINESS_STATE,
    Gate.FORBIDDEN_SIDE_EFFECTS,
    Gate.PERMISSION_VIOLATIONS,
)


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class Turn:
    content: str


@dataclass(frozen=True)
class Assertion:
    name: str
    gate: Gate


@dataclass(frozen=True)
class Task:
    name: str
    turns: tuple[Turn, ...]
    assertions: tuple[Assertion, ...]

    def __init__(
        self,
        name: str,
        turns: Sequence[Turn],
        assertions: Sequence[Assertion],
    ) -> None:
        normalized_assertions = tuple(assertions)
        if not normalized_assertions:
            raise ValueError("Task must declare at least one assertion")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "turns", tuple(turns))
        object.__setattr__(self, "assertions", normalized_assertions)


@dataclass(frozen=True)
class TurnRecord:
    turn: Turn
    data: Mapping[str, object]


@dataclass(frozen=True)
class AssertionResult:
    assertion: Assertion
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class GateResult:
    gate: Gate
    assertion_results: tuple[AssertionResult, ...]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.assertion_results)


@dataclass(frozen=True)
class TrialResult:
    task: Task
    turn_records: tuple[TurnRecord, ...]
    gate_results: tuple[GateResult, ...]
    verdict: Verdict


TrialHandle = TypeVar("TrialHandle")


class Adapter(Protocol[TrialHandle]):
    def prepare(self, task: Task) -> TrialHandle: ...

    def send_turn(
        self, trial: TrialHandle, turn: Turn
    ) -> Mapping[str, object]: ...

    def grade(
        self, trial: TrialHandle, assertion: Assertion
    ) -> AssertionResult: ...

    def cleanup(self, trial: TrialHandle) -> None: ...


def run_trial(task: Task, adapter: Adapter[TrialHandle]) -> TrialResult:
    trial = adapter.prepare(task)
    turn_records: list[TurnRecord] = []
    gate_results: list[GateResult] = []

    try:
        for turn in task.turns:
            record = adapter.send_turn(trial, turn)
            turn_records.append(TurnRecord(turn=turn, data=dict(record)))

        for gate in GATE_ORDER:
            assertions = tuple(
                assertion for assertion in task.assertions if assertion.gate is gate
            )
            if not assertions:
                continue

            result = GateResult(
                gate=gate,
                assertion_results=tuple(
                    adapter.grade(trial, assertion) for assertion in assertions
                ),
            )
            gate_results.append(result)
            if not result.passed:
                break

        verdict = (
            Verdict.PASS
            if all(result.passed for result in gate_results)
            else Verdict.FAIL
        )
        return TrialResult(
            task=task,
            turn_records=tuple(turn_records),
            gate_results=tuple(gate_results),
            verdict=verdict,
        )
    finally:
        adapter.cleanup(trial)
