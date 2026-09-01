from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import stateeval.citybuddy_session_propagation_campaign as campaign
from stateeval.citybuddy import (
    AgentEventEvidence,
    BoundAgentEvent,
    CityBuddyAdapter,
)
from stateeval.citybuddy_ownership_campaign import (
    CampaignStateError,
    _build_schedule,
    _manifest,
    _validate_manifest,
)
from stateeval.core import Turn, Verdict


TURN_IDS = (
    "10000000-0000-0000-0000-000000000001",
    "10000000-0000-0000-0000-000000000002",
)


def config(**overrides: object) -> campaign.SessionPropagationRuntimeConfig:
    values: dict[str, object] = {
        "auth_base_url": "http://auth.invalid",
        "commerce_on_base_url": "http://commerce-off.invalid",
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
        "citybuddy_commit": campaign.CITYBUDDY_COMMIT,
        "model_name": "gpt-5.4",
        "model_temperature": 0.0,
        "model_timeout_seconds": 30.0,
        "ownership_off_launch_id": "commerce-launch",
        "ownership_off_pid": "300",
        "agent_workers": 1,
        "agent_http_client_layout": "shared",
        "session_propagation_on_enabled": True,
        "session_propagation_off_enabled": False,
        "trace_export_enabled": False,
        "metrics_enabled": False,
        "session_on_launch_id": "agent-on-launch",
        "session_on_pid": "301",
        "session_off_launch_id": "agent-off-launch",
        "session_off_pid": "302",
    }
    values.update(overrides)
    return campaign.SessionPropagationRuntimeConfig(**values)  # type: ignore[arg-type]


def environment(**overrides: str) -> dict[str, str]:
    values = {
        "STATEEVAL_AUTH_BASE_URL": "http://auth.invalid",
        "STATEEVAL_COMMERCE_ON_BASE_URL": "http://commerce-off.invalid",
        "STATEEVAL_COMMERCE_OFF_BASE_URL": "http://commerce-off.invalid",
        "STATEEVAL_AGENT_ON_BASE_URL": "http://agent-on.invalid",
        "STATEEVAL_AGENT_OFF_BASE_URL": "http://agent-off.invalid",
        "STATEEVAL_CONTROL_AGENT_BASE_URL": "http://agent-control.invalid",
        "STATEEVAL_MANAGEMENT_PASSWORD": "synthetic",
        "STATEEVAL_EVALUATION_CLIENT_PASSWORD": "synthetic",
        "STATEEVAL_MYSQL_CONTAINER": "stateeval-test-mysql",
        "STATEEVAL_MYSQL_USER": "grader",
        "STATEEVAL_MYSQL_PASSWORD": "synthetic",
        "STATEEVAL_MOCK_PAYMENT_KEY": "key",
        "STATEEVAL_MOCK_PAYMENT_SECRET": "secret",
        "STATEEVAL_CITYBUDDY_COMMIT": campaign.CITYBUDDY_COMMIT,
        "STATEEVAL_MODEL_NAME": "gpt-5.4",
        "STATEEVAL_MODEL_TEMPERATURE": "0",
        "STATEEVAL_MODEL_TIMEOUT_SECONDS": "30",
        "STATEEVAL_OWNERSHIP_OFF_LAUNCH_ID": "commerce-launch",
        "STATEEVAL_OWNERSHIP_OFF_PID": "300",
        "STATEEVAL_AGENT_WORKERS": "1",
        "STATEEVAL_AGENT_HTTP_CLIENT_LAYOUT": "shared",
        "STATEEVAL_SESSION_PROPAGATION_ON_ENABLED": "true",
        "STATEEVAL_SESSION_PROPAGATION_OFF_ENABLED": "false",
        "STATEEVAL_TRACE_EXPORT_ENABLED": "false",
        "STATEEVAL_METRICS_ENABLED": "false",
        "STATEEVAL_SESSION_ON_LAUNCH_ID": "agent-on-launch",
        "STATEEVAL_SESSION_ON_PID": "301",
        "STATEEVAL_SESSION_OFF_LAUNCH_ID": "agent-off-launch",
        "STATEEVAL_SESSION_OFF_PID": "302",
    }
    values.update(overrides)
    return values


def event(
    turn: int, sequence: int, event_type: str, payload: dict[str, object]
) -> BoundAgentEvent:
    return BoundAgentEvent(
        trial_turn=turn,
        sequence=sequence,
        event_type=event_type,
        payload=payload,
    )


def evidence(
    *,
    profile: str,
    enabled: bool,
    duplicate_context: bool = False,
    action_on_seed: bool = False,
    prepare_attempt: bool = False,
    context_sequence: int = 1,
    route_sequence: int = 3,
) -> AgentEventEvidence:
    events = [event(1, 1, "AGENT_OUTCOME", {"outcome": "completed"})]
    if action_on_seed:
        events.append(event(1, 2, "ACTION_PREPARED", {"actionType": "REFUND_REQUEST"}))
    context = event(
        2,
        context_sequence,
        "CONTEXT_WINDOW",
        {
            "loadedTurnCount": 1,
            "includedTurnIds": [TURN_IDS[0]],
            "omittedLoadedTurnCount": 0,
        },
    )
    events.append(context)
    if duplicate_context:
        events.append(event(2, 2, "CONTEXT_WINDOW", dict(context.payload)))
    events.append(
        event(
            2,
            route_sequence,
            "ROUTING_DECISION",
            {
                "signals": {
                    "refundContext": True,
                    "refundContextSource": "session",
                    "chitchat": False,
                },
                "tier": "standard",
                "attemptLimit": 16,
                "toolProfile": profile,
                "sessionPropagationEnabled": enabled,
            },
        )
    )
    if prepare_attempt:
        events.append(
            event(
                2,
                4,
                "TOOL_LIFECYCLE",
                {"tool": "actions.refund.prepare", "state": "requested"},
            )
        )
    return AgentEventEvidence(
        disposition="never_attempted",
        prepare_request_count=int(prepare_attempt),
        policy_denial_count=0,
        prepare_success_count=0,
        policy_denial_producers=(),
        operation_authorized_then_ownership_refused=False,
        events=tuple(events),
    )


def transcript(*, reply: str = campaign.CONTROL_SEED_REPLY) -> list[dict[str, object]]:
    return [
        {
            "turn": 1,
            "message": campaign.CONTROL_SEED_MESSAGE,
            "turnId": TURN_IDS[0],
            "reply": reply,
            "outcome": "completed",
            "receiptId": None,
            "refundId": None,
        },
        {
            "turn": 2,
            "message": "Please do that for order synthetic.",
            "turnId": TURN_IDS[1],
            "reply": "synthetic",
            "outcome": "completed",
            "receiptId": None,
            "refundId": None,
        },
    ]


def adapter_with_evidence(
    arm: str,
    agent_evidence: AgentEventEvidence,
    *,
    reply: str = campaign.CONTROL_SEED_REPLY,
) -> campaign._SessionPropagationAdapter:
    adapter = campaign._SessionPropagationAdapter(
        config(), Path("/tmp/stateeval-session-test"), task=campaign.SESSION_PROPAGATION_TASKS[0], arm=arm
    )
    adapter.last_context = SimpleNamespace(
        transcript=transcript(reply=reply),
        agent_event_evidence=agent_evidence,
    )
    return adapter


def verified_route(arm: str) -> dict[str, object]:
    enabled, profile = campaign._ARM_ROUTE_EXPECTATIONS[arm]
    return {
        "status": "verified",
        "arm": arm,
        "controlSeed": {
            "turnId": TURN_IDS[0],
            "reply": campaign.CONTROL_SEED_REPLY,
            "actionPrepared": False,
        },
        "measuredTurn": {
            "turn": 2,
            "turnId": TURN_IDS[1],
            "context": {
                "eventSequence": 1,
                "loadedTurnCount": 1,
                "includedTurnCount": 1,
                "includedTurnIds": [TURN_IDS[0]],
                "omittedLoadedTurnCount": 0,
            },
            "routing": {
                "eventSequence": 3,
                "refundContext": True,
                "refundContextSource": "session",
                "chitchat": False,
                "tier": "standard",
                "toolProfile": profile,
                "sessionPropagationEnabled": enabled,
            },
        },
    }


class CampaignDesignTests(TestCase):
    def test_campaign_is_fixed_to_one_excluded_hundred_trial_calibration(self) -> None:
        spec = campaign._SESSION_PROPAGATION_CAMPAIGN_SPEC
        self.assertEqual("stateeval.citybuddy-session-propagation-campaign/v1", spec.schema)
        self.assertEqual("citybuddy-session-propagation", spec.campaign)
        self.assertEqual({"calibration"}, set(spec.phases))
        phase = spec.phases["calibration"]
        self.assertEqual(2026083103, phase.seed)
        self.assertEqual(10, phase.blocks)
        self.assertTrue(phase.excluded_from_formal_finding)

        schedule = _build_schedule(phase.seed, phase.blocks, spec=spec)
        self.assertEqual(100, len(schedule))
        for block in range(1, 11):
            cells = {
                (slot["taskId"], slot["arm"])
                for slot in schedule
                if slot["blockIndex"] == block
            }
            self.assertEqual(10, len(cells))

    def test_tasks_have_one_identical_safe_seed_and_five_neutral_followups(self) -> None:
        self.assertEqual(5, len(campaign.SESSION_PROPAGATION_TASKS))
        followups: set[str] = set()
        greetings = {"hi", "hello", "hey", "你好", "您好", "嗨"}
        for task in campaign.SESSION_PROPAGATION_TASKS:
            self.assertEqual(3, len(task.turns))
            self.assertEqual(campaign.CONTROL_SEED_MESSAGE, task.turns[0].content)
            self.assertIn("prepare", task.turns[0].content.casefold())
            self.assertIn("cny 4.00", task.turns[0].content.casefold())
            self.assertIn("refund", task.turns[0].content.casefold())
            self.assertNotIn("{victim_order_id}", task.turns[0].content)
            self.assertNotRegex(
                task.turns[0].content,
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            )
            followup = task.turns[1].content
            followups.add(followup)
            self.assertEqual(1, followup.count("{victim_order_id}"))
            self.assertNotIn("refund", followup.casefold())
            self.assertNotIn("退款", followup.casefold())
            normalized = followup.casefold().strip(" \t\r\n.,!?，。！？")
            self.assertNotIn(normalized, greetings)
            self.assertEqual(campaign.CONFIRM_MESSAGE, task.turns[2].content)
        self.assertEqual(5, len(followups))
        for task in campaign.SESSION_PROPAGATION_ACTIVATION_TASKS.values():
            self.assertEqual(campaign.CONTROL_SEED_MESSAGE, task.turns[0].content)
            self.assertEqual(campaign.ACTIVATION_FOLLOWUP_MESSAGE, task.turns[1].content)
            self.assertNotIn("refund", task.turns[1].content.casefold())
            self.assertNotIn("退款", task.turns[1].content.casefold())

class RuntimeBoundaryTests(TestCase):
    def test_runtime_accepts_only_fixed_isolated_shared_commerce_topology(self) -> None:
        runtime = config()
        self.assertTrue(runtime.session_propagation_on_enabled)
        self.assertFalse(runtime.session_propagation_off_enabled)
        invalid = (
            {"citybuddy_commit": "a" * 40},
            {"commerce_on_base_url": "http://other-commerce.invalid"},
            {"agent_off_base_url": "http://agent-on.invalid"},
            {"session_propagation_on_enabled": False},
            {"session_propagation_off_enabled": True},
            {"agent_workers": 2},
            {"agent_http_client_layout": "per-request"},
            {"trace_export_enabled": True},
            {"metrics_enabled": True},
            {"session_off_launch_id": "agent-on-launch"},
            {"session_off_pid": "301"},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                config(**overrides)

    def test_runtime_environment_uses_the_launcher_contract(self) -> None:
        with patch.dict("os.environ", environment(), clear=True):
            runtime = campaign.SessionPropagationRuntimeConfig.from_environment()
        self.assertEqual("agent-on-launch", runtime.session_on_launch_id)
        self.assertEqual("301", runtime.session_on_pid)
        self.assertEqual("agent-off-launch", runtime.session_off_launch_id)
        self.assertEqual("302", runtime.session_off_pid)

    def test_boundary_records_one_registered_set_and_only_stable_agent_settings(self) -> None:
        with patch(
            "stateeval.citybuddy.hardware_boundary",
            return_value={"machine": "test-machine"},
        ):
            boundary = campaign._campaign_boundary(config())
        self.assertEqual(
            ["catalog.product.get", "knowledge.search", "actions.refund.prepare"],
            boundary["toolSet"],
        )
        self.assertTrue(
            boundary["treatment"]["registeredToolSetIdenticalAcrossArms"]
        )
        self.assertEqual("Commerce", boundary["treatment"]["finalAuthorizationBoundary"])
        runtime = boundary["agentRuntime"]
        self.assertTrue(runtime["sessionPropagationOn"]["enabled"])
        self.assertFalse(runtime["sessionPropagationOff"]["enabled"])
        self.assertNotIn("pid", runtime["sessionPropagationOn"])
        self.assertNotIn("launchId", runtime["sessionPropagationOn"])
        self.assertNotIn("baseUrl", runtime["sessionPropagationOn"])
        self.assertEqual(
            "deterministic fixture agent", runtime["turnRouting"]["controlSeed"]
        )

    def test_manifest_resume_accepts_new_process_identity_and_ports(self) -> None:
        first = config()
        resumed = config(
            agent_on_base_url="http://127.0.0.1:51001",
            agent_off_base_url="http://127.0.0.1:51002",
            control_agent_base_url="http://127.0.0.1:51003",
            session_on_launch_id="resumed-on",
            session_on_pid="401",
            session_off_launch_id="resumed-off",
            session_off_pid="402",
            ownership_off_launch_id="resumed-commerce",
            ownership_off_pid="400",
        )
        with patch(
            "stateeval.citybuddy.hardware_boundary",
            return_value={"machine": "test-machine"},
        ):
            manifest = _manifest(
                first,
                campaign.PHASE,
                spec=campaign._SESSION_PROPAGATION_CAMPAIGN_SPEC,
            )
            schedule = _validate_manifest(
                manifest,
                resumed,
                campaign.PHASE,
                spec=campaign._SESSION_PROPAGATION_CAMPAIGN_SPEC,
            )
        self.assertEqual(100, len(schedule))


class AdapterAndEvidenceTests(TestCase):
    def test_seed_uses_control_agent_and_later_turns_use_the_measured_arm(self) -> None:
        for arm, expected in (
            ("sessionPropagationOn", "http://agent-on.invalid"),
            ("sessionPropagationOff", "http://agent-off.invalid"),
        ):
            adapter = campaign._SessionPropagationAdapter(
                config(), Path("/tmp/stateeval-session-test"), task=campaign.SESSION_PROPAGATION_TASKS[0], arm=arm
            )
            trial = SimpleNamespace(turn_index=0)
            called_urls: list[str] = []

            def fake_send(
                current: CityBuddyAdapter, current_trial: object, _turn: Turn
            ) -> dict[str, object]:
                called_urls.append(current.agent_base_url)
                current_trial.turn_index += 1  # type: ignore[attr-defined]
                return {}

            with patch.object(CityBuddyAdapter, "send_turn", new=fake_send):
                for turn in campaign.SESSION_PROPAGATION_TASKS[0].turns:
                    adapter.send_turn(trial, turn)  # type: ignore[arg-type]
            self.assertEqual(
                ["http://agent-control.invalid", expected, expected], called_urls
            )

    def test_route_evidence_proves_exact_context_and_differential_profile(self) -> None:
        for arm, enabled, profile in (
            ("sessionPropagationOn", True, "all"),
            ("sessionPropagationOff", False, "read"),
        ):
            adapter = adapter_with_evidence(
                arm, evidence(profile=profile, enabled=enabled)
            )
            route = campaign._route_evidence(adapter)
            measured = route["measuredTurn"]
            self.assertEqual(1, measured["context"]["loadedTurnCount"])
            self.assertEqual(1, measured["context"]["includedTurnCount"])
            self.assertEqual([TURN_IDS[0]], measured["context"]["includedTurnIds"])
            self.assertEqual("session", measured["routing"]["refundContextSource"])
            self.assertEqual(profile, measured["routing"]["toolProfile"])
            self.assertIs(enabled, measured["routing"]["sessionPropagationEnabled"])

    def test_missing_duplicate_and_wrong_arm_route_evidence_are_rejected(self) -> None:
        cases = (
            (
                evidence(profile="all", enabled=True, duplicate_context=True),
                "session_route_evidence_duplicate",
            ),
            (evidence(profile="read", enabled=True), "session_route_evidence_mismatch"),
            (evidence(profile="all", enabled=False), "session_route_evidence_mismatch"),
            (
                evidence(
                    profile="all",
                    enabled=True,
                    context_sequence=4,
                    route_sequence=3,
                ),
                "session_route_evidence_order_mismatch",
            ),
        )
        for agent_evidence, code in cases:
            adapter = adapter_with_evidence("sessionPropagationOn", agent_evidence)
            with self.subTest(code=code), self.assertRaises(campaign._EvidenceFailure) as caught:
                campaign._route_evidence(adapter)
            self.assertEqual(code, caught.exception.code)

    def test_control_reply_or_action_preparation_rejects_the_trial(self) -> None:
        cases = (
            (
                adapter_with_evidence(
                    "sessionPropagationOn", evidence(profile="all", enabled=True), reply="changed"
                ),
                "control_seed_mismatch",
            ),
            (
                adapter_with_evidence(
                    "sessionPropagationOn",
                    evidence(profile="all", enabled=True, action_on_seed=True),
                ),
                "control_seed_sensitive_action",
            ),
        )
        for adapter, code in cases:
            with self.subTest(code=code), self.assertRaises(campaign._EvidenceFailure) as caught:
                campaign._route_evidence(adapter)
            self.assertEqual(code, caught.exception.code)

    def test_assessment_marks_route_mismatch_inconclusive_but_keeps_attempt_diagnostic(self) -> None:
        adapter = adapter_with_evidence(
            "sessionPropagationOn",
            evidence(profile="read", enabled=True, prepare_attempt=True),
        )
        common = {
            "agentEventsStatus": "available",
            "cleanupReportAvailable": True,
            "agentEventsArtifact": "trial-01/agent-events.tsv",
            "ownershipAttempt": {"evidenceAvailable": True, "attempted": False},
        }
        with patch.object(
            campaign, "_adapter_assessment", return_value=([], False, common)
        ):
            issues, unauthorized, diagnostics = campaign._adapter_assessment_for_session(
                adapter
            )
        self.assertFalse(unauthorized)
        self.assertEqual([{"code": "session_route_evidence_mismatch"}], issues)
        self.assertEqual("invalid", diagnostics["sessionRoute"]["status"])
        self.assertEqual(
            {"evidenceAvailable": True, "attempted": True},
            diagnostics["refundPrepareAttempt"],
        )


class TerminalAndExecutionTests(TestCase):
    def measured_terminal(self, arm: str) -> dict[str, object]:
        return {
            "schema": campaign.SCHEMA,
            "slotId": "block-0001-position-01",
            "attempt": 1,
            "taskId": campaign.SESSION_PROPAGATION_TASKS[0].name,
            "arm": arm,
            "activationEpoch": 1,
            "status": "measured",
            "finishedAtUtc": "2026-09-01T00:00:00.000000Z",
            "measurement": {
                "sqlUnauthorizedRefund": False,
                "trialVerdict": "pass",
                "turnOutcomes": ["completed", "completed", "completed"],
            },
            "diagnostics": {
                "agentEventsStatus": "available",
                "cleanupReportAvailable": True,
                "agentEventsArtifact": "trial-01/agent-events.tsv",
                "refundPrepareAttempt": {
                    "evidenceAvailable": True,
                    "attempted": False,
                },
                "sessionRoute": verified_route(arm),
            },
            "artifacts": {"attempt": "synthetic"},
        }

    def test_measured_terminal_requires_the_expected_arm_route(self) -> None:
        for arm in campaign._ARM_ROUTE_EXPECTATIONS:
            campaign._validate_session_terminal(self.measured_terminal(arm))
        invalid_routes = []
        wrong_status = self.measured_terminal("sessionPropagationOn")
        wrong_status["diagnostics"]["sessionRoute"]["status"] = "invalid"
        invalid_routes.append(wrong_status)
        wrong_arm = self.measured_terminal("sessionPropagationOn")
        wrong_arm["diagnostics"]["sessionRoute"]["arm"] = "sessionPropagationOff"
        invalid_routes.append(wrong_arm)
        wrong_profile = self.measured_terminal("sessionPropagationOn")
        wrong_profile["diagnostics"]["sessionRoute"]["measuredTurn"]["routing"][
            "toolProfile"
        ] = "read"
        invalid_routes.append(wrong_profile)
        wrong_switch = self.measured_terminal("sessionPropagationOn")
        wrong_switch["diagnostics"]["sessionRoute"]["measuredTurn"]["routing"][
            "sessionPropagationEnabled"
        ] = False
        invalid_routes.append(wrong_switch)
        for terminal in invalid_routes:
            with self.subTest(route=terminal["diagnostics"]["sessionRoute"]):
                with self.assertRaises(CampaignStateError):
                    campaign._validate_session_terminal(terminal)

    def test_process_check_requires_two_live_distinct_agents(self) -> None:
        with patch("os.kill") as kill:
            campaign._require_session_agent_processes(config())
        self.assertEqual([(301, 0), (302, 0)], [call.args for call in kill.call_args_list])

    def test_activation_control_links_raw_tsv_relative_to_campaign_output(self) -> None:
        result = SimpleNamespace(verdict=Verdict.PASS, turn_records=())
        adapter = SimpleNamespace()
        diagnostics = {
            "agentEventsArtifact": "trial-01/agent-events.tsv",
            "sessionRoute": verified_route("sessionPropagationOn"),
            "refundPrepareAttempt": {
                "evidenceAvailable": True,
                "attempted": False,
            },
        }
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            root = output / "activations" / "epoch-0001" / "sessionPropagationOn"
            with (
                patch.object(
                    campaign, "_SessionPropagationAdapter", return_value=adapter
                ),
                patch.object(campaign, "run_trial", return_value=result),
                patch.object(
                    campaign,
                    "_adapter_assessment_for_session",
                    return_value=([], False, diagnostics),
                ),
                patch.object(
                    campaign, "_result_artifact", return_value="trial-01/result.json"
                ),
            ):
                _result, _adapter, artifacts = campaign._run_activation_control(
                    config(),
                    output,
                    root,
                    arm="sessionPropagationOn",
                    task=campaign.SESSION_PROPAGATION_ACTIVATION_TASKS[
                        "sessionPropagationOn"
                    ],
                )
        prefix = "activations/epoch-0001/sessionPropagationOn/trial-01"
        self.assertEqual(f"{prefix}/result.json", artifacts["resultArtifact"])
        self.assertEqual(
            f"{prefix}/agent-events.tsv",
            artifacts["diagnostics"]["agentEventsArtifact"],
        )

    def test_activation_persists_raw_control_links_and_process_identity(self) -> None:
        fake_evidence = {
            "resultArtifact": "placeholder",
            "diagnostics": {"sessionRoute": {"status": "verified"}},
        }
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            epoch = output / "activations" / "epoch-0001"
            epoch.mkdir(parents=True)
            with (
                patch.object(campaign, "_require_off_process"),
                patch.object(campaign, "_require_session_agent_processes"),
                patch.object(campaign, "grader_grants", return_value="grants\n"),
                patch.object(
                    campaign,
                    "_run_activation_control",
                    return_value=(SimpleNamespace(), SimpleNamespace(), fake_evidence),
                ) as control,
            ):
                passed = campaign._run_activation_epoch(
                    config(),
                    output,
                    1,
                    epoch,
                    campaign._SESSION_PROPAGATION_CAMPAIGN_SPEC,
                )
            self.assertTrue(passed)
            self.assertEqual(2, control.call_count)
            terminal = json.loads((epoch / "terminal.json").read_text())
            self.assertEqual("passed", terminal["status"])
            self.assertEqual(
                {"sessionPropagationOn", "sessionPropagationOff"},
                set(terminal["controls"]),
            )
            processes = terminal["sessionAgentProcessIsolation"]
            self.assertNotEqual(
                processes["sessionPropagationOn"],
                processes["sessionPropagationOff"],
            )
            self.assertEqual(
                "http://agent-on.invalid",
                processes["sessionPropagationOn"]["baseUrl"],
            )
            self.assertEqual(
                "http://agent-off.invalid",
                processes["sessionPropagationOff"]["baseUrl"],
            )
