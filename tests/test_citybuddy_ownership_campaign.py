from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import stateeval.citybuddy_ownership_campaign as campaign
from stateeval.citybuddy import (
    HOSTILE_TASK,
    EvaluationIdentity,
    OracleSnapshot,
    RuntimeConfig,
    TrialContext,
)
from stateeval.core import (
    AssertionResult,
    GateResult,
    TrialResult,
    TurnRecord,
    Verdict,
)


STATEEVAL_SHA = "a" * 40
CITYBUDDY_SHA = "b" * 40


def config(**overrides: object) -> RuntimeConfig:
    values: dict[str, object] = {
        "auth_base_url": "http://auth.invalid",
        "commerce_on_base_url": "http://commerce-on.invalid",
        "commerce_off_base_url": "http://commerce-off.invalid",
        "agent_on_base_url": "http://agent-on.invalid",
        "agent_off_base_url": "http://agent-off.invalid",
        "control_agent_base_url": "http://agent-control.invalid",
        "management_password": "synthetic",
        "evaluation_client_password": "synthetic",
        "mysql_container": "stateeval-test-mysql",
        "mysql_user": "grader",
        "mysql_password": "synthetic",
        "mock_payment_key": "key",
        "mock_payment_secret": "secret",
        "citybuddy_commit": CITYBUDDY_SHA,
        "model_name": "gpt-5.4",
        "model_temperature": 0.0,
        "model_timeout_seconds": 30.0,
        "ownership_off_launch_id": "launch-fixed",
        "ownership_off_pid": "12345",
    }
    values.update(overrides)
    return RuntimeConfig(**values)  # type: ignore[arg-type]


def boundary(runtime: RuntimeConfig, *, marker: str = "fixed") -> Mapping[str, object]:
    return {
        "marker": marker,
        "citybuddyCommit": runtime.citybuddy_commit,
        "model": runtime.model_name,
        "temperature": runtime.model_temperature,
        "timeout": runtime.model_timeout_seconds,
        "attemptBudgetPerTurn": 16,
        "toolSet": [
            "catalog.product.get",
            "knowledge.search",
            "actions.refund.prepare",
        ],
        "hardware": {"machine": "test-machine"},
        "oracle": "read-only SQL",
    }


@contextmanager
def fixed_runtime(
    *, stateeval_sha: str = STATEEVAL_SHA, boundary_marker: str = "fixed"
) -> Iterator[None]:
    with patch.object(
        campaign, "_stateeval_commit", return_value=stateeval_sha
    ), patch.object(
        campaign,
        "run_boundary",
        side_effect=lambda runtime: boundary(runtime, marker=boundary_marker),
    ):
        yield


def identity(name: str) -> EvaluationIdentity:
    return EvaluationIdentity(
        sandbox_id=f"sandbox-{name}",
        case_correlation=f"case-{name}",
        subject=f"subject-{name}",
        token="synthetic-token",
        evaluation_handle=f"handle-{name}",
    )


def snapshot(*, unauthorized: bool = False, raw: str = "raw oracle\n") -> OracleSnapshot:
    refunds: tuple[Mapping[str, object], ...] = ()
    if unauthorized:
        refunds = (
            {
                "refund_id": "refund-1",
                "state": "REQUESTED",
                "user_subject": "subject-actor",
            },
        )
    return OracleSnapshot(
        records={"mock_refund": refunds, "pending_action": ()}, raw=raw
    )


def trial_context(
    attempt_path: Path,
    *,
    post: OracleSnapshot | None = None,
    transcript: list[Mapping[str, object]] | None = None,
) -> TrialContext:
    artifact_dir = attempt_path / "trial-01"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    actor = identity("actor")
    owner = identity("owner")
    return TrialContext(
        label="trial-01",
        artifact_dir=artifact_dir,
        actor=actor,
        target_owner=owner,
        target_order_id="00000000-0000-0000-0000-000000000001",
        session_id="S" * 43,
        sandboxes=(actor, owner),
        before=snapshot(),
        post=post,
        transcript=[] if transcript is None else transcript,
    )


def trial_result(
    *, verdict: Verdict = Verdict.PASS, outcomes: tuple[str, ...] = ("completed",)
) -> TrialResult:
    gate_results: tuple[GateResult, ...] = ()
    if verdict is Verdict.FAIL:
        assertion = HOSTILE_TASK.assertions[0]
        gate_results = (
            GateResult(
                gate=assertion.gate,
                assertion_results=(
                    AssertionResult(
                        assertion=assertion,
                        passed=False,
                        detail="authoritative SQL found a refund",
                    ),
                ),
            ),
        )
    return TrialResult(
        task=HOSTILE_TASK,
        turn_records=tuple(
            TurnRecord(turn=HOSTILE_TASK.turns[index], data={"outcome": outcome})
            for index, outcome in enumerate(outcomes)
        ),
        gate_results=gate_results,
        verdict=verdict,
    )


def initialized_campaign(
    output: Path, *, seed: int = 17, blocks: int = 1
) -> tuple[Mapping[str, object], tuple[Mapping[str, object], ...]]:
    output.mkdir()
    manifest = campaign._manifest(config(), seed, blocks)
    campaign._atomic_create_json(output / "manifest.json", manifest)
    return manifest, campaign._schedule(manifest)


def measured_terminal(*, unauthorized: bool = False) -> Mapping[str, object]:
    return {
        "status": "measured",
        "measurement": {
            "sqlUnauthorizedRefund": unauthorized,
            "trialVerdict": "fail" if unauthorized else "pass",
            "turnOutcomes": ["completed"],
        },
        "diagnostics": {"agentEventsStatus": "not_recorded"},
        "artifacts": {},
    }


def inconclusive_terminal(
    *, code: str = "python_exception", unauthorized: bool | None = None
) -> Mapping[str, object]:
    return {
        "status": "operational_inconclusive",
        "operationalIssues": [{"code": code}],
        "sqlUnauthorizedRefund": unauthorized,
        "diagnostics": {"agentEventsStatus": "not_recorded"},
        "artifacts": {},
    }


def complete_slot(
    output: Path,
    schedule: tuple[Mapping[str, object], ...],
    slot: Mapping[str, object],
    terminal: Mapping[str, object],
) -> Path:
    attempt = campaign._start_attempt(output, schedule, str(slot["slotId"]))
    campaign._write_slot_terminal(
        output, schedule, str(slot["slotId"]), attempt, terminal
    )
    return attempt


def fake_assessed_adapter(
    attempt_path: Path,
    *,
    unauthorized: bool = False,
    diagnostics_status: str = "available",
) -> SimpleNamespace:
    post = snapshot(unauthorized=unauthorized)
    trial = trial_context(attempt_path, post=post)
    (trial.artifact_dir / "oracle-after.tsv").write_text(post.raw, encoding="utf-8")
    (trial.artifact_dir / "transcript.json").write_text("{}\n", encoding="utf-8")
    report = {
        "schema": campaign.SCHEMA,
        "oracleAfter": {
            "status": "captured",
            "artifact": "trial-01/oracle-after.tsv",
        },
        "transcript": {
            "status": "written",
            "artifact": "trial-01/transcript.json",
        },
        "agentEvents": {"status": diagnostics_status},
        "sandboxCompletion": {"status": "completed", "errors": []},
    }
    (trial.artifact_dir / "cleanup-report.json").write_text(
        json.dumps(report) + "\n", encoding="utf-8"
    )
    return SimpleNamespace(
        last_context=trial,
        cleanup_report=report,
        cleanup_report_written=True,
    )


class ScheduleAndManifestTest(TestCase):
    def test_schedule_is_deterministic_balanced_by_block_and_has_unique_ids(self) -> None:
        first = campaign._build_schedule(9182, 20)
        second = campaign._build_schedule(9182, 20)
        different = campaign._build_schedule(9183, 20)

        self.assertEqual(first, second)
        self.assertNotEqual(
            [slot["arm"] for slot in first],
            [slot["arm"] for slot in different],
        )
        self.assertEqual(40, len(first))
        self.assertEqual(40, len({slot["slotId"] for slot in first}))
        for block_index in range(1, 21):
            block = [slot for slot in first if slot["blockIndex"] == block_index]
            self.assertEqual([1, 2], sorted(slot["position"] for slot in block))
            self.assertEqual(
                {"ownershipOn", "ownershipOff"},
                {slot["arm"] for slot in block},
            )
            self.assertEqual({HOSTILE_TASK.name}, {slot["taskId"] for slot in block})

    def test_manifest_records_the_fixed_boundary_and_plan_without_runtime_secrets(
        self,
    ) -> None:
        runtime = config(
            management_password="DO-NOT-RECORD-MANAGEMENT",
            evaluation_client_password="DO-NOT-RECORD-EVALUATION",
            mysql_password="DO-NOT-RECORD-MYSQL",
            mock_payment_secret="DO-NOT-RECORD-PAYMENT",
        )
        with fixed_runtime():
            manifest = campaign._manifest(runtime, seed=29, blocks=2)
        encoded = campaign._json_bytes(manifest).decode()

        self.assertEqual(campaign.SCHEMA, manifest["schema"])
        self.assertEqual(campaign.CAMPAIGN, manifest["campaign"])
        self.assertEqual(STATEEVAL_SHA, manifest["stateEvalCommit"])
        self.assertEqual(CITYBUDDY_SHA, manifest["boundary"]["citybuddyCommit"])
        self.assertEqual(HOSTILE_TASK.name, manifest["task"]["taskId"])
        self.assertEqual(
            [gate.value for gate in campaign.GATE_ORDER],
            manifest["hardGateOrder"],
        )
        self.assertEqual(29, manifest["plan"]["seed"])
        self.assertEqual(2, manifest["plan"]["blocks"])
        self.assertEqual(4, len(manifest["plan"]["slots"]))
        self.assertIn("attemptBudgetPerTurn", manifest["boundary"])
        self.assertEqual(3, len(manifest["boundary"]["toolSet"]))
        for secret in (
            runtime.management_password,
            runtime.evaluation_client_password,
            runtime.mysql_password,
            runtime.mock_payment_secret,
        ):
            self.assertNotIn(secret, encoded)

    def test_manifest_exists_before_callbacks_and_resume_keeps_its_bytes(self) -> None:
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            seen: list[bytes] = []

            def stop_after_manifest(
                runtime: RuntimeConfig, root: Path, number: int, epoch: Path
            ) -> bool:
                del runtime, number, epoch
                manifest_path = root / "manifest.json"
                self.assertTrue(manifest_path.is_file())
                seen.append(manifest_path.read_bytes())
                self.assertFalse((root / "slots").exists())
                return False

            with fixed_runtime(), patch.object(
                campaign, "_run_activation_epoch", side_effect=stop_after_manifest
            ):
                campaign.run_ownership_campaign(
                    config(), output, seed=41, blocks=2
                )
                original = (output / "manifest.json").read_bytes()
                campaign.run_ownership_campaign(config(), output, resume=True)

            self.assertEqual([original, original], seen)
            self.assertEqual(original, (output / "manifest.json").read_bytes())

    def test_fresh_rejects_an_existing_output_without_running_callbacks(self) -> None:
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            output.mkdir()
            with fixed_runtime(), patch.object(
                campaign, "_run_activation_epoch"
            ) as activation, self.assertRaises(FileExistsError):
                campaign.run_ownership_campaign(
                    config(), output, seed=1, blocks=1
                )
            activation.assert_not_called()

    def test_resume_rejects_missing_and_malformed_manifests(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with fixed_runtime():
                with self.subTest("missing output"), self.assertRaises(
                    campaign.CampaignStateError
                ):
                    campaign.run_ownership_campaign(
                        config(), root / "missing", resume=True
                    )

                missing_manifest = root / "missing-manifest"
                missing_manifest.mkdir()
                with self.subTest("missing manifest"), self.assertRaises(
                    campaign.CampaignStateError
                ):
                    campaign.run_ownership_campaign(
                        config(), missing_manifest, resume=True
                    )

                malformed = root / "malformed"
                malformed.mkdir()
                (malformed / "manifest.json").write_text("not json", encoding="utf-8")
                with self.subTest("malformed manifest"), self.assertRaises(
                    campaign.CampaignStateError
                ):
                    campaign.run_ownership_campaign(config(), malformed, resume=True)

                non_object = root / "non-object"
                non_object.mkdir()
                (non_object / "manifest.json").write_text("[]\n", encoding="utf-8")
                with self.subTest("non-object manifest"), self.assertRaises(
                    campaign.CampaignStateError
                ):
                    campaign.run_ownership_campaign(config(), non_object, resume=True)

    def test_resume_rejects_stateeval_and_runtime_boundary_mismatches(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sha_output = root / "sha"
            boundary_output = root / "boundary"
            with fixed_runtime():
                initialized_campaign(sha_output)
                initialized_campaign(boundary_output)

            with fixed_runtime(stateeval_sha="c" * 40), self.assertRaisesRegex(
                campaign.CampaignStateError, "StateEval commit"
            ):
                campaign.run_ownership_campaign(config(), sha_output, resume=True)

            with fixed_runtime(boundary_marker="changed"), self.assertRaisesRegex(
                campaign.CampaignStateError, "Runtime boundary"
            ):
                campaign.run_ownership_campaign(
                    config(), boundary_output, resume=True
                )

    def test_cli_rejects_mixed_resume_plan_and_incomplete_fresh_plan(self) -> None:
        cases = (
            ["campaign", "--output", "/not-used", "--resume", "--seed", "1"],
            ["campaign", "--output", "/not-used", "--seed", "1"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments), patch(
                "sys.argv", arguments
            ), patch("sys.stderr", new=StringIO()), self.assertRaises(
                SystemExit
            ) as raised:
                campaign.main()
            self.assertEqual(2, raised.exception.code)


class RecoveryStateMachineTest(TestCase):
    def test_resume_skips_a_measured_terminal_and_only_runs_the_pending_slot(self) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            output = Path(temporary) / "campaign"
            _manifest, schedule = initialized_campaign(output)
            complete_slot(output, schedule, schedule[0], measured_terminal())
            seen: list[str] = []

            def run_pending(
                runtime: RuntimeConfig,
                root: Path,
                immutable_schedule: tuple[Mapping[str, object], ...],
                slot: Mapping[str, object],
                attempt: Path,
            ) -> bool:
                del runtime
                seen.append(str(slot["slotId"]))
                campaign._write_slot_terminal(
                    root,
                    immutable_schedule,
                    str(slot["slotId"]),
                    attempt,
                    measured_terminal(),
                )
                return True

            with patch.object(
                campaign, "_run_activation_epoch", return_value=True
            ), patch.object(campaign, "_run_slot", side_effect=run_pending):
                summary = campaign.run_ownership_campaign(
                    config(), output, resume=True
                )

            self.assertEqual([schedule[1]["slotId"]], seen)
            self.assertEqual("complete", summary["status"])
            self.assertEqual(2, summary["counts"]["terminalSlots"])
            self.assertEqual(2, summary["counts"]["attemptsStarted"])

    def test_exception_is_one_terminal_inconclusive_and_cannot_be_replaced(self) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            output = Path(temporary) / "campaign"
            _manifest, schedule = initialized_campaign(output)
            slot = schedule[0]
            attempt = campaign._start_attempt(
                output, schedule, str(slot["slotId"])
            )
            adapter = SimpleNamespace(
                last_context=None,
                cleanup_report=None,
                cleanup_report_written=False,
            )
            with patch.object(
                campaign, "_CampaignCityBuddyAdapter", return_value=adapter
            ), patch.object(
                campaign, "run_trial", side_effect=RuntimeError("provider timeout")
            ):
                self.assertFalse(
                    campaign._run_slot(
                        config(), output, schedule, slot, attempt
                    )
                )

            terminal = json.loads((attempt / "terminal.json").read_bytes())
            self.assertEqual("operational_inconclusive", terminal["status"])
            self.assertIn(
                "python_exception",
                [issue["code"] for issue in terminal["operationalIssues"]],
            )
            self.assertEqual(1, len(campaign._scan_slots(output, schedule)[0].attempts))
            with self.assertRaises(campaign.CampaignStateError):
                campaign._start_attempt(output, schedule, str(slot["slotId"]))

    def test_repeated_keyboard_interrupts_retry_only_the_same_planned_slot(self) -> None:
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            with fixed_runtime(), patch.object(
                campaign, "_run_activation_epoch", return_value=True
            ), patch.object(campaign, "_run_slot", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    campaign.run_ownership_campaign(
                        config(), output, seed=7, blocks=1
                    )
                manifest_bytes = (output / "manifest.json").read_bytes()
                with self.assertRaises(KeyboardInterrupt):
                    campaign.run_ownership_campaign(config(), output, resume=True)
                with self.assertRaises(KeyboardInterrupt):
                    campaign.run_ownership_campaign(config(), output, resume=True)

            manifest = json.loads(manifest_bytes)
            first_slot = manifest["plan"]["slots"][0]["slotId"]
            second_slot = manifest["plan"]["slots"][1]["slotId"]
            first_root = output / "slots" / first_slot
            self.assertEqual(
                ["attempt-0001", "attempt-0002", "attempt-0003"],
                sorted(path.name for path in first_root.iterdir()),
            )
            self.assertTrue((first_root / "attempt-0001" / "interrupted.json").is_file())
            self.assertTrue((first_root / "attempt-0002" / "interrupted.json").is_file())
            self.assertFalse((first_root / "attempt-0003" / "interrupted.json").exists())
            self.assertFalse((first_root / "attempt-0003" / "terminal.json").exists())
            self.assertFalse((output / "slots" / second_slot).exists())
            self.assertEqual(manifest_bytes, (output / "manifest.json").read_bytes())
            summary = json.loads((output / "summary.json").read_bytes())
            self.assertEqual(3, summary["counts"]["attemptsStarted"])
            self.assertEqual(2, summary["counts"]["interruptedAttempts"])
            self.assertEqual(2, summary["counts"]["extraAttempts"])
            self.assertEqual(2, summary["counts"]["pendingSlots"])

    def test_resume_discards_unpublished_attempt_directories_and_retries_attempt_one(
        self,
    ) -> None:
        for artifact_kind in ("empty-published-name", "hidden-with-started"):
            with self.subTest(
                artifact_kind=artifact_kind
            ), TemporaryDirectory() as temporary:
                output = Path(temporary) / "campaign"
                with fixed_runtime():
                    _manifest, schedule = initialized_campaign(output)
                    first_slot = schedule[0]
                    slot_root = output / "slots" / str(first_slot["slotId"])
                    slot_root.mkdir(parents=True)
                    if artifact_kind == "empty-published-name":
                        unpublished = slot_root / "attempt-0001"
                        unpublished.mkdir()
                    else:
                        unpublished = slot_root / ".attempt-0001.crashed"
                        unpublished.mkdir()
                        campaign._atomic_create_json(
                            unpublished / "started.json",
                            {
                                "slotId": first_slot["slotId"],
                                "attempt": 1,
                            },
                        )

                    seen: list[Path] = []

                    def stop_after_retry(
                        runtime: RuntimeConfig,
                        root: Path,
                        immutable_schedule: tuple[Mapping[str, object], ...],
                        slot: Mapping[str, object],
                        attempt: Path,
                    ) -> bool:
                        del runtime
                        seen.append(attempt)
                        campaign._write_slot_terminal(
                            root,
                            immutable_schedule,
                            str(slot["slotId"]),
                            attempt,
                            inconclusive_terminal(code="synthetic_stop"),
                        )
                        return False

                    with patch.object(
                        campaign, "_run_activation_epoch", return_value=True
                    ), patch.object(
                        campaign, "_run_slot", side_effect=stop_after_retry
                    ):
                        campaign.run_ownership_campaign(
                            config(), output, resume=True
                        )

                self.assertEqual(1, len(seen))
                self.assertEqual("attempt-0001", seen[0].name)
                if artifact_kind == "hidden-with-started":
                    self.assertFalse(unpublished.exists())
                self.assertTrue(
                    (
                        slot_root
                        / "attempt-0001"
                        / "started.json"
                    ).is_file()
                )
                self.assertFalse((slot_root / "attempt-0002").exists())

    def test_provider_denied_is_terminal_and_resume_does_not_rerun_its_slot(self) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            output = Path(temporary) / "campaign"
            _manifest, schedule = initialized_campaign(output)
            denied_slot = schedule[0]
            denied_attempt = campaign._start_attempt(
                output, schedule, str(denied_slot["slotId"])
            )
            adapter = fake_assessed_adapter(denied_attempt)
            with patch.object(
                campaign, "_CampaignCityBuddyAdapter", return_value=adapter
            ), patch.object(
                campaign,
                "run_trial",
                return_value=trial_result(outcomes=("provider_denied",)),
            ):
                self.assertFalse(
                    campaign._run_slot(
                        config(), output, schedule, denied_slot, denied_attempt
                    )
                )

            resumed: list[str] = []

            def measure_other(
                runtime: RuntimeConfig,
                root: Path,
                immutable_schedule: tuple[Mapping[str, object], ...],
                slot: Mapping[str, object],
                attempt: Path,
            ) -> bool:
                del runtime
                resumed.append(str(slot["slotId"]))
                campaign._write_slot_terminal(
                    root,
                    immutable_schedule,
                    str(slot["slotId"]),
                    attempt,
                    measured_terminal(),
                )
                return True

            with patch.object(
                campaign, "_run_activation_epoch", return_value=True
            ), patch.object(campaign, "_run_slot", side_effect=measure_other):
                summary = campaign.run_ownership_campaign(
                    config(), output, resume=True
                )

            self.assertEqual([schedule[1]["slotId"]], resumed)
            self.assertEqual("operationally_inconclusive", summary["status"])
            denied_terminal = json.loads(
                (denied_attempt / "terminal.json").read_bytes()
            )
            self.assertIn(
                "provider_denied",
                [issue["code"] for issue in denied_terminal["operationalIssues"]],
            )


class ActivationTest(TestCase):
    def test_activation_epoch_uses_the_frozen_control_order(self) -> None:
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            output.mkdir()
            number, epoch = campaign._next_epoch(output)
            order: list[str] = []

            def off_process(runtime: RuntimeConfig) -> None:
                del runtime
                order.append("off-process")

            def grants(runtime: RuntimeConfig) -> str:
                del runtime
                order.append("grader-grants")
                return "synthetic grants\n"

            def run_control(
                runtime: RuntimeConfig,
                root: Path,
                mode: str,
                task: object,
            ) -> tuple[TrialResult, TrialContext, Mapping[str, object]]:
                del runtime, task
                order.append(mode)
                context = trial_context(root)
                return trial_result(), context, {
                    "resultArtifact": f"{context.label}/result.json",
                    "diagnostics": {},
                }

            def policy(result: TrialResult, trial: TrialContext) -> Mapping[str, object]:
                del result, trial
                order.append("policy-validator")
                return {
                    "supportEventsArtifact": "controls/policy/trial-01/agent-events.tsv",
                    "oracleArtifact": "controls/policy/trial-01/oracle-after.tsv",
                }

            def mutation(
                result: TrialResult, trial: TrialContext
            ) -> Mapping[str, object]:
                del result, trial
                order.append("mutation-validator")
                return {
                    "supportEventsArtifact": (
                        "controls/mutation/trial-01/agent-events.tsv"
                    ),
                    "oracleArtifact": "controls/mutation/trial-01/oracle-after.tsv",
                }

            with patch.object(
                campaign, "_require_off_process", side_effect=off_process
            ), patch.object(
                campaign, "grader_grants", side_effect=grants
            ), patch.object(
                campaign, "_run_control", side_effect=run_control
            ), patch.object(
                campaign, "_require_policy_activation", side_effect=policy
            ), patch.object(
                campaign, "_require_mutation_activation", side_effect=mutation
            ):
                self.assertTrue(
                    campaign._run_activation_epoch(config(), output, number, epoch)
                )

            self.assertEqual(
                [
                    "off-process",
                    "grader-grants",
                    "policy_control",
                    "policy-validator",
                    "mutation_control",
                    "mutation-validator",
                    "off-process",
                ],
                order,
            )
            terminal = json.loads((epoch / "terminal.json").read_bytes())
            self.assertEqual("passed", terminal["status"])
            self.assertEqual(
                (
                    "activations/epoch-0001/policy/trial-01/"
                    "agent-events.tsv"
                ),
                terminal["policyControl"]["activation"]["supportEventsArtifact"],
            )
            self.assertEqual(
                "activations/epoch-0001/policy/trial-01/oracle-after.tsv",
                terminal["policyControl"]["activation"]["oracleArtifact"],
            )
            self.assertEqual(
                (
                    "activations/epoch-0001/mutation/trial-01/"
                    "agent-events.tsv"
                ),
                terminal["mutationControl"]["activation"][
                    "supportEventsArtifact"
                ],
            )
            self.assertEqual(
                "activations/epoch-0001/mutation/trial-01/oracle-after.tsv",
                terminal["mutationControl"]["activation"]["oracleArtifact"],
            )

    def test_activation_runs_before_measurement(self) -> None:
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            order: list[str] = []

            def activate(
                runtime: RuntimeConfig, root: Path, number: int, epoch: Path
            ) -> bool:
                del runtime, root, number, epoch
                order.append("activation")
                return True

            def measure(
                runtime: RuntimeConfig,
                root: Path,
                immutable_schedule: tuple[Mapping[str, object], ...],
                slot: Mapping[str, object],
                attempt: Path,
            ) -> bool:
                del runtime
                order.append(f"measured:{slot['slotId']}")
                campaign._write_slot_terminal(
                    root,
                    immutable_schedule,
                    str(slot["slotId"]),
                    attempt,
                    measured_terminal(),
                )
                return True

            with fixed_runtime(), patch.object(
                campaign, "_run_activation_epoch", side_effect=activate
            ), patch.object(campaign, "_run_slot", side_effect=measure):
                summary = campaign.run_ownership_campaign(
                    config(), output, seed=13, blocks=1
                )

            self.assertEqual("activation", order[0])
            self.assertEqual(2, sum(item.startswith("measured:") for item in order))
            self.assertEqual("complete", summary["status"])

    def test_failed_activation_starts_no_slot_and_resume_creates_a_new_epoch(self) -> None:
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "campaign"
            with fixed_runtime(), patch.object(
                campaign,
                "_require_off_process",
                side_effect=RuntimeError("ownership-off process mismatch"),
            ), patch.object(campaign, "_run_slot") as measured:
                first = campaign.run_ownership_campaign(
                    config(), output, seed=5, blocks=1
                )
                second = campaign.run_ownership_campaign(
                    config(), output, resume=True
                )

            measured.assert_not_called()
            self.assertEqual("partial", first["status"])
            self.assertEqual("partial", second["status"])
            self.assertEqual(2, second["activation"]["epochs"])
            self.assertEqual(2, second["activation"]["failed"])
            self.assertEqual(0, second["counts"]["attemptsStarted"])
            self.assertFalse((output / "slots").exists())
            self.assertTrue(
                (output / "activations" / "epoch-0001" / "terminal.json").is_file()
            )
            self.assertTrue(
                (output / "activations" / "epoch-0002" / "terminal.json").is_file()
            )


class SlotOutcomeTest(TestCase):
    def test_timeout_after_possible_mutation_captures_sql_before_completion_but_is_inconclusive(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            output = Path(temporary) / "campaign"
            _manifest, schedule = initialized_campaign(output)
            slot = schedule[0]
            attempt = campaign._start_attempt(
                output, schedule, str(slot["slotId"])
            )
            order: list[str] = []

            def post_snapshot(
                adapter: campaign._CampaignCityBuddyAdapter, trial: TrialContext
            ) -> OracleSnapshot:
                order.append("oracle-after")
                trial.post = snapshot(
                    unauthorized=True, raw="REQUESTED refund raw row\n"
                )
                adapter._write_text(
                    trial.artifact_dir / "oracle-after.tsv", trial.post.raw
                )
                return trial.post

            def complete(
                adapter: campaign._CampaignCityBuddyAdapter,
                sandboxes: object,
            ) -> list[Exception]:
                del adapter, sandboxes
                order.append("sandbox-completion")
                return []

            def time_out(
                task: object, adapter: campaign._CampaignCityBuddyAdapter
            ) -> TrialResult:
                del task
                trial = trial_context(attempt)
                adapter.last_context = trial
                adapter.cleanup(trial)
                raise TimeoutError("response timed out after the mutation")

            with patch.object(
                campaign._CampaignCityBuddyAdapter,
                "_post_snapshot",
                autospec=True,
                side_effect=post_snapshot,
            ), patch.object(
                campaign._CampaignCityBuddyAdapter,
                "_complete_sandboxes",
                autospec=True,
                side_effect=complete,
            ), patch.object(campaign, "run_trial", side_effect=time_out):
                self.assertFalse(
                    campaign._run_slot(
                        config(), output, schedule, slot, attempt
                    )
                )

            self.assertEqual(["oracle-after", "sandbox-completion"], order)
            self.assertTrue(
                (attempt / "trial-01" / "oracle-after.tsv").is_file()
            )
            terminal = json.loads((attempt / "terminal.json").read_bytes())
            self.assertEqual("operational_inconclusive", terminal["status"])
            self.assertIs(True, terminal["sqlUnauthorizedRefund"])
            self.assertIn(
                "python_exception",
                [issue["code"] for issue in terminal["operationalIssues"]],
            )

    def test_assertion_fail_is_measured_and_diagnostic_failure_does_not_override_sql(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            output = Path(temporary) / "campaign"
            _manifest, schedule = initialized_campaign(output)
            slot = schedule[0]
            attempt = campaign._start_attempt(
                output, schedule, str(slot["slotId"])
            )
            adapter = fake_assessed_adapter(
                attempt, unauthorized=True, diagnostics_status="failed"
            )
            with patch.object(
                campaign, "_CampaignCityBuddyAdapter", return_value=adapter
            ), patch.object(
                campaign,
                "run_trial",
                return_value=trial_result(verdict=Verdict.FAIL),
            ):
                self.assertTrue(
                    campaign._run_slot(
                        config(), output, schedule, slot, attempt
                    )
                )

            terminal = json.loads((attempt / "terminal.json").read_bytes())
            self.assertEqual("measured", terminal["status"])
            self.assertEqual("fail", terminal["measurement"]["trialVerdict"])
            self.assertIs(True, terminal["measurement"]["sqlUnauthorizedRefund"])
            self.assertEqual("failed", terminal["diagnostics"]["agentEventsStatus"])

    def test_required_result_artifact_failure_is_operationally_inconclusive(self) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            output = Path(temporary) / "campaign"
            _manifest, schedule = initialized_campaign(output)
            slot = schedule[0]
            attempt = campaign._start_attempt(
                output, schedule, str(slot["slotId"])
            )
            adapter = fake_assessed_adapter(attempt)
            with patch.object(
                campaign, "_CampaignCityBuddyAdapter", return_value=adapter
            ), patch.object(
                campaign, "run_trial", return_value=trial_result()
            ), patch.object(
                campaign,
                "_result_artifact",
                side_effect=OSError("artifact filesystem unavailable"),
            ):
                self.assertFalse(
                    campaign._run_slot(
                        config(), output, schedule, slot, attempt
                    )
                )

            terminal = json.loads((attempt / "terminal.json").read_bytes())
            self.assertEqual("operational_inconclusive", terminal["status"])
            artifact_issue = next(
                issue
                for issue in terminal["operationalIssues"]
                if issue["code"] == "required_artifact_failure"
            )
            self.assertEqual("result.json", artifact_issue["artifact"])

    def test_sql_failure_is_inconclusive_and_preserves_raw_output(self) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            output = Path(temporary) / "campaign"
            _manifest, schedule = initialized_campaign(output)
            slot = schedule[0]
            attempt = campaign._start_attempt(
                output, schedule, str(slot["slotId"])
            )
            adapter = campaign._CampaignCityBuddyAdapter(
                config(), attempt, mode=campaign.ARM_MODES[str(slot["arm"])]
            )
            trial = trial_context(attempt)
            adapter.last_context = trial
            sql_error = subprocess.CalledProcessError(
                1,
                ["synthetic-mysql"],
                output="record_type\trecord_json\nmock_refund\tpartial raw row\n",
                stderr="synthetic failure",
            )
            with patch.object(
                adapter, "_post_snapshot", side_effect=sql_error
            ), patch.object(adapter, "_complete_sandboxes", return_value=[]):
                adapter.cleanup(trial)

            raw_path = attempt / "trial-01" / "oracle-after-raw.tsv"
            self.assertEqual(sql_error.stdout, raw_path.read_text(encoding="utf-8"))
            with patch.object(
                campaign, "_CampaignCityBuddyAdapter", return_value=adapter
            ), patch.object(
                campaign, "run_trial", return_value=trial_result()
            ):
                self.assertFalse(
                    campaign._run_slot(
                        config(), output, schedule, slot, attempt
                    )
                )

            terminal = json.loads((attempt / "terminal.json").read_bytes())
            self.assertEqual("operational_inconclusive", terminal["status"])
            missing_sql = next(
                issue
                for issue in terminal["operationalIssues"]
                if issue["code"] == "missing_final_sql"
            )
            self.assertEqual(
                "trial-01/oracle-after-raw.tsv", missing_sql["rawArtifact"]
            )

    def test_malformed_inherited_sql_output_is_preserved_as_raw_oracle_evidence(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            attempt = Path(temporary) / "attempt-0001"
            adapter = campaign._CampaignCityBuddyAdapter(
                config(), attempt, mode="ownership_on"
            )
            trial = trial_context(attempt)
            adapter.last_context = trial
            malformed = "unexpected_header\nmock_refund\tpartial row\n"
            completed = subprocess.CompletedProcess(
                args=["synthetic-mysql"],
                returncode=0,
                stdout=malformed,
                stderr="",
            )

            with patch.object(
                campaign._citybuddy_module.subprocess,
                "run",
                return_value=completed,
            ), patch.object(adapter, "_complete_sandboxes", return_value=[]):
                adapter.cleanup(trial)

            raw_path = attempt / "trial-01" / "oracle-after-raw.tsv"
            self.assertEqual(malformed, raw_path.read_text(encoding="utf-8"))
            report = json.loads(
                (attempt / "trial-01" / "cleanup-report.json").read_bytes()
            )
            self.assertEqual("failed", report["oracleAfter"]["status"])
            self.assertEqual(
                "trial-01/oracle-after-raw.tsv",
                report["oracleAfter"]["rawArtifact"],
            )
            self.assertEqual(
                "_OracleSnapshotError",
                report["oracleAfter"]["error"]["errorType"],
            )

    def test_sandbox_cleanup_failure_is_inconclusive_with_complete_sql_retained(self) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            output = Path(temporary) / "campaign"
            _manifest, schedule = initialized_campaign(output)
            slot = schedule[0]
            attempt = campaign._start_attempt(
                output, schedule, str(slot["slotId"])
            )
            adapter = campaign._CampaignCityBuddyAdapter(
                config(), attempt, mode=campaign.ARM_MODES[str(slot["arm"])]
            )
            trial = trial_context(attempt)
            adapter.last_context = trial

            def post_snapshot(current: TrialContext) -> OracleSnapshot:
                current.post = snapshot(raw="complete final SQL\n")
                adapter._write_text(
                    current.artifact_dir / "oracle-after.tsv", current.post.raw
                )
                return current.post

            with patch.object(
                adapter, "_post_snapshot", side_effect=post_snapshot
            ), patch.object(
                adapter,
                "_complete_sandboxes",
                return_value=[RuntimeError("cleanup failed")],
            ):
                adapter.cleanup(trial)

            with patch.object(
                campaign, "_CampaignCityBuddyAdapter", return_value=adapter
            ), patch.object(
                campaign, "run_trial", return_value=trial_result()
            ):
                self.assertFalse(
                    campaign._run_slot(
                        config(), output, schedule, slot, attempt
                    )
                )

            self.assertEqual(
                "complete final SQL\n",
                (attempt / "trial-01" / "oracle-after.tsv").read_text(
                    encoding="utf-8"
                ),
            )
            terminal = json.loads((attempt / "terminal.json").read_bytes())
            self.assertEqual("operational_inconclusive", terminal["status"])
            self.assertIn(
                "sandbox_cleanup_failure",
                [issue["code"] for issue in terminal["operationalIssues"]],
            )


class SummaryAndAppendOnlyTest(TestCase):
    def test_summary_counts_every_intermediate_attempt_and_terminal_state(self) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            output = Path(temporary) / "campaign"
            _manifest, schedule = initialized_campaign(output)

            self.assertEqual(
                {
                    "plannedSlots": 2,
                    "terminalSlots": 0,
                    "measuredSlots": 0,
                    "operationalInconclusiveSlots": 0,
                    "pendingSlots": 2,
                    "attemptsStarted": 0,
                    "interruptedAttempts": 0,
                    "extraAttempts": 0,
                },
                campaign._summary(output, schedule)["counts"],
            )

            first = schedule[0]
            attempt_one = campaign._start_attempt(
                output, schedule, str(first["slotId"])
            )
            self.assertEqual(
                (2, 0, 1, 0, 0),
                self._count_tuple(campaign._summary(output, schedule)),
            )
            campaign._interrupt_dangling_attempts(output, schedule)
            self.assertEqual(
                (2, 0, 1, 1, 0),
                self._count_tuple(campaign._summary(output, schedule)),
            )
            attempt_two = campaign._start_attempt(
                output, schedule, str(first["slotId"])
            )
            self.assertEqual(
                (2, 0, 2, 1, 1),
                self._count_tuple(campaign._summary(output, schedule)),
            )
            campaign._write_slot_terminal(
                output,
                schedule,
                str(first["slotId"]),
                attempt_two,
                measured_terminal(),
            )
            after_measurement = campaign._summary(output, schedule)
            self.assertEqual(1, after_measurement["counts"]["terminalSlots"])
            self.assertEqual(1, after_measurement["counts"]["measuredSlots"])
            self.assertEqual(1, after_measurement["counts"]["pendingSlots"])

            second = schedule[1]
            second_attempt = campaign._start_attempt(
                output, schedule, str(second["slotId"])
            )
            campaign._write_slot_terminal(
                output,
                schedule,
                str(second["slotId"]),
                second_attempt,
                inconclusive_terminal(code="sandbox_cleanup_failure"),
            )
            final = campaign._summary(output, schedule)
            self.assertEqual("operationally_inconclusive", final["status"])
            self.assertEqual(
                {
                    "plannedSlots": 2,
                    "terminalSlots": 2,
                    "measuredSlots": 1,
                    "operationalInconclusiveSlots": 1,
                    "pendingSlots": 0,
                    "attemptsStarted": 3,
                    "interruptedAttempts": 1,
                    "extraAttempts": 1,
                },
                final["counts"],
            )
            self.assertTrue((attempt_one / "interrupted.json").is_file())

    @staticmethod
    def _count_tuple(summary: Mapping[str, object]) -> tuple[int, int, int, int, int]:
        counts = summary["counts"]
        assert isinstance(counts, dict)
        return (
            int(counts["pendingSlots"]),
            int(counts["terminalSlots"]),
            int(counts["attemptsStarted"]),
            int(counts["interruptedAttempts"]),
            int(counts["extraAttempts"]),
        )

    def test_inconclusive_is_not_an_observed_false_and_zero_denominator_is_null(self) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            output = Path(temporary) / "campaign"
            _manifest, schedule = initialized_campaign(output)
            measured_slot = schedule[0]
            inconclusive_slot = schedule[1]
            complete_slot(
                output,
                schedule,
                measured_slot,
                measured_terminal(unauthorized=False),
            )
            complete_slot(
                output,
                schedule,
                inconclusive_slot,
                inconclusive_terminal(
                    code="provider_denied", unauthorized=True
                ),
            )

            summary = campaign._summary(output, schedule)
            measured_arm = summary["arms"][measured_slot["arm"]]["measuredSql"]
            inconclusive_arm = summary["arms"][inconclusive_slot["arm"]][
                "measuredSql"
            ]
            self.assertEqual(1, measured_arm["denominator"])
            self.assertEqual(0, measured_arm["unauthorizedRefunds"])
            self.assertEqual(0.0, measured_arm["observedProportion"])
            self.assertIsNotNone(measured_arm["wilson95"])
            self.assertEqual(0, inconclusive_arm["denominator"])
            self.assertEqual(0, inconclusive_arm["unauthorizedRefunds"])
            self.assertIsNone(inconclusive_arm["observedProportion"])
            self.assertIsNone(inconclusive_arm["wilson95"])

    def test_all_zero_measured_violations_are_a_complete_result(self) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            output = Path(temporary) / "campaign"
            _manifest, schedule = initialized_campaign(output, blocks=3)
            for slot in schedule:
                complete_slot(output, schedule, slot, measured_terminal())

            summary = campaign._summary(output, schedule)
            self.assertEqual("complete", summary["status"])
            self.assertEqual(6, summary["counts"]["measuredSlots"])
            self.assertEqual(0, summary["counts"]["operationalInconclusiveSlots"])
            for arm in ("ownershipOn", "ownershipOff"):
                measured = summary["arms"][arm]["measuredSql"]
                self.assertEqual(3, measured["denominator"])
                self.assertEqual(0, measured["unauthorizedRefunds"])
                self.assertEqual(0.0, measured["observedProportion"])

    def test_unplanned_slot_second_terminal_and_append_only_overwrite_are_rejected(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            root = Path(temporary)

            unplanned_output = root / "unplanned"
            _manifest, unplanned_schedule = initialized_campaign(unplanned_output)
            (unplanned_output / "slots" / "not-in-plan").mkdir(parents=True)
            with self.assertRaisesRegex(
                campaign.CampaignStateError, "Unplanned slot"
            ):
                campaign._scan_slots(unplanned_output, unplanned_schedule)

            terminal_output = root / "second-terminal"
            _manifest, terminal_schedule = initialized_campaign(terminal_output)
            slot = terminal_schedule[0]
            attempt = complete_slot(
                terminal_output, terminal_schedule, slot, measured_terminal()
            )
            terminal_path = attempt / "terminal.json"
            original_terminal = terminal_path.read_bytes()
            with self.assertRaises(campaign.CampaignStateError):
                campaign._write_slot_terminal(
                    terminal_output,
                    terminal_schedule,
                    str(slot["slotId"]),
                    attempt,
                    inconclusive_terminal(),
                )
            self.assertEqual(original_terminal, terminal_path.read_bytes())

            append_only = root / "append-only.json"
            campaign._atomic_create_json(append_only, {"first": True})
            original = append_only.read_bytes()
            with self.assertRaises(FileExistsError):
                campaign._atomic_create_json(append_only, {"first": False})
            self.assertEqual(original, append_only.read_bytes())

    def test_persisted_measured_terminal_requires_boolean_sql_measurement(self) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            output = Path(temporary) / "campaign"
            _manifest, schedule = initialized_campaign(output)
            slot = schedule[0]
            attempt = campaign._start_attempt(
                output, schedule, str(slot["slotId"])
            )
            campaign._atomic_create_json(
                attempt / "terminal.json",
                {
                    "schema": campaign.SCHEMA,
                    "slotId": slot["slotId"],
                    "attempt": 1,
                    "taskId": slot["taskId"],
                    "arm": slot["arm"],
                    "status": "measured",
                    "measurement": {
                        "sqlUnauthorizedRefund": 1,
                        "trialVerdict": "fail",
                        "turnOutcomes": ["completed"],
                    },
                    "diagnostics": {},
                    "artifacts": {},
                },
            )

            with self.assertRaisesRegex(
                campaign.CampaignStateError,
                "authoritative SQL measurement",
            ):
                campaign._scan_slots(output, schedule)

    def test_persisted_provider_denial_cannot_be_a_measured_terminal(self) -> None:
        with TemporaryDirectory() as temporary, fixed_runtime():
            output = Path(temporary) / "campaign"
            _manifest, schedule = initialized_campaign(output)
            slot = schedule[0]
            attempt = campaign._start_attempt(
                output, schedule, str(slot["slotId"])
            )
            invalid = dict(measured_terminal())
            invalid["measurement"] = {
                "sqlUnauthorizedRefund": False,
                "trialVerdict": "pass",
                "turnOutcomes": ["provider_denied"],
            }

            with self.assertRaisesRegex(
                campaign.CampaignStateError,
                "authoritative SQL measurement",
            ):
                campaign._write_slot_terminal(
                    output,
                    schedule,
                    str(slot["slotId"]),
                    attempt,
                    invalid,
                )
