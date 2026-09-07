from __future__ import annotations

import io
import json
import signal
import subprocess
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from stateeval import shopmate as driver
from stateeval.citybuddy import EvaluationIdentity, OracleSnapshot
from stateeval.core import TrialResult, Verdict

ORDER = "f4dacb49-165d-4184-939c-f092fe28a2e3"
PENDING = "c931ddc6-f1e6-4f45-9664-0c9c9d722373"


def runtime():
    return driver.Runtime(
        auth_base_url="http://127.0.0.1:18080",
        commerce_on_base_url="http://127.0.0.1:18081",
        commerce_off_base_url="http://127.0.0.1:18082",
        management_password="synthetic-management",
        evaluation_client_password="synthetic-evaluation",
        shopping_service_secret="synthetic-shopping",
        mysql_container="fixture-mysql",
        mysql_user="fixture-grader",
        mysql_password="synthetic-mysql",
        mock_payment_key="fixture-key",
        mock_payment_secret="synthetic-callback",
        citybuddy_root="/synthetic/citybuddy",
        shopmate_root="/synthetic/shopmate",
        citybuddy_commit="1" * 40,
        shopmate_commit="2" * 40,
        stateeval_commit="3" * 40,
        model_name="fixture-model",
        ownership_off_launch_id="owned-fixture-commerce",
        ownership_off_pid=12345,
    )


def snapshot(**tables):
    return OracleSnapshot(
        {name: tuple(rows) for name, rows in tables.items()}, "record_type\trecord_json\n"
    )


def card(**changes):
    return {
        "pendingActionId": PENDING,
        "orderId": ORDER,
        "amountMinor": 100,
        "currency": "CNY",
        "state": "PREPARED",
    } | changes


def ui(action=None, event_type="ui"):
    return {
        "type": event_type,
        "data": {
            "component": "refund_confirmation",
            "payload": {"action": card() if action is None else action},
        },
    }


def sse(events):
    return "".join(
        "event: " + e["type"] + "\ndata: " + json.dumps(e["data"], ensure_ascii=False) + "\n\n"
        for e in events
    ).encode("utf-8")


class Stream(io.BytesIO):
    def __init__(self, events):
        super().__init__(sse(events))
        self.headers = Message()
        self.headers["Content-Type"] = "text/event-stream; charset=utf-8"
        self.eof_observed = False

    def readline(self, size=-1):
        value = super().readline(size)
        if not value:
            self.eof_observed = True
        return value


def make_trial(directory, *, own=True):
    identity = EvaluationIdentity("sandbox-one", "case", "actor", "synthetic-actor-token", "h" * 43)
    return driver.Trial(
        "trial-one",
        directory,
        identity,
        identity,
        ORDER,
        "buyer-session",
        (identity,),
        snapshot(),
        host=SimpleNamespace(base_url="http://127.0.0.1:19000", close=Mock()),
        own=own,
    )


def confirmations():
    first = {
        "receiptId": "receipt-one",
        "refundId": "refund-one",
        "pendingActionId": PENDING,
        "amountMinor": 100,
        "currency": "CNY",
        "status": "REQUESTED",
        "replayed": False,
    }
    return [{"receipt": first}, {"receipt": first | {"replayed": True}}]


def successful_sql():
    binding = {
        "user_subject": "actor",
        "support_session_id": "buyer-session",
        "sandbox_id": "sandbox-one",
        "order_id": ORDER,
        "payment_attempt_id": "attempt-one",
        "currency": "CNY",
        "amount_minor": 100,
    }
    return {
        "mock_payment_attempt": [
            {
                "attempt_id": "attempt-one",
                "user_subject": "actor",
                "order_id": ORDER,
                "amount_minor": 1800,
                "currency": "CNY",
                "state": "SUCCEEDED",
            }
        ],
        "mock_refund": [
            {
                "refund_id": "refund-one",
                "user_subject": "actor",
                "order_id": ORDER,
                "payment_attempt_id": "attempt-one",
                "state": "REQUESTED",
                "requested_amount_minor": 100,
                "refunded_amount_minor": 0,
                "currency": "CNY",
            }
        ],
        "pending_action": [
            binding
            | {"pending_action_id": PENDING, "state": "CONSUMED", "required_scope": "refund:create"}
        ],
        "action_receipt": [
            binding
            | {
                "receipt_id": "receipt-one",
                "pending_action_id": PENDING,
                "refund_id": "refund-one",
                "outbox_event_id": "event-one",
                "result_state": "REQUESTED",
            }
        ],
        "refund_outbox": [
            {
                "event_id": "event-one",
                "aggregate_id": "refund-one",
                "event_type": "REFUND_REQUESTED",
            }
        ],
    }


class StreamTests(TestCase):
    def test_utf8_multiline_crlf_comments_and_redacted_wire_copy(self):
        source = io.BytesIO(
            ': 心跳\r\nevent: text_delta\r\ndata: {"text":\r\ndata: "你好 synthetic-token"}\r\n\r\n'.encode()
        )
        raw = io.BytesIO()
        events = driver.read_sse(source, raw, ("synthetic-token",))
        self.assertEqual([{"type": "text_delta", "data": {"text": "你好 [REDACTED]"}}], events)
        self.assertIn("心跳", raw.getvalue().decode())
        self.assertNotIn(b"synthetic-token", raw.getvalue())

    def test_terminal_event_does_not_stop_before_eof_or_hide_a_following_error(self):
        events = [
            {"type": "turn_complete", "data": {}},
            {"type": "error", "data": {"message": "late failure"}},
        ]
        source = Stream(events)
        self.assertEqual(events, driver.read_sse(source, io.BytesIO()))
        self.assertTrue(source.eof_observed)

    def test_incomplete_frame_invalid_utf8_and_nonobject_payload_are_not_complete(self):
        for wire in (b'data: {"text":"unfinished"}\n', b"data: \xff\n\n", b"data: []\n\n"):
            with self.subTest(wire=wire), self.assertRaises((RuntimeError, UnicodeDecodeError)):
                driver.read_sse(io.BytesIO(wire), io.BytesIO())
        with (
            patch.object(driver, "STREAM_LIMIT", 8),
            self.assertRaisesRegex(RuntimeError, "bounded"),
        ):
            driver.read_sse(io.BytesIO(b"data: {}\n\n"), io.BytesIO())

    def test_only_unique_final_card_is_clickable(self):
        self.assertIsNone(driver.refund_card([ui(event_type="ui_partial")]))
        self.assertEqual(card(), driver.refund_card([ui(event_type="ui_partial"), ui(), ui()]))
        with self.assertRaisesRegex(RuntimeError, "Multiple"):
            driver.refund_card([ui(), ui(card(pendingActionId="different"))])
        with self.assertRaisesRegex(RuntimeError, "Conflicting"):
            driver.refund_card([ui(), ui(card(amountMinor=200))])
        with self.assertRaisesRegex((RuntimeError, TypeError), "Malformed"):
            driver.refund_card([ui({"pendingActionId": None})])

    def test_unknown_commands_or_nonrefund_writes_are_not_quiet(self):
        for state in ("running", None, "future_status"):
            with self.subTest(state=state), self.assertRaises(RuntimeError):
                driver.require_quiet({"run_status": state, "commands": []})
        for command in (
            {"kind": "refund", "state": "unknown"},
            {"kind": "refund", "state": "pending"},
            {"kind": "cart", "state": "confirmed"},
        ):
            with self.subTest(command=command), self.assertRaises(RuntimeError):
                driver.require_quiet({"run_status": "completed", "commands": [command]})
        with self.assertRaises((RuntimeError, TypeError)):
            driver.require_quiet({"run_status": "completed"})
        driver.require_quiet(
            {"run_status": "failed", "commands": [{"kind": "refund", "state": "rejected"}]}
        )


class AdapterTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.output = self.directory / "results"
        self.output.mkdir()
        self.artifact = self.output / "trial-one"
        self.artifact.mkdir()
        self.adapter = driver.ShopMateAdapter(
            runtime(), self.output, self.directory, arm="ownership_on", own=True
        )
        self.trial = make_trial(self.artifact)

    def test_own_control_clicks_after_eof_and_repeats_same_card_as_original_actor(self):
        stream = Stream([ui(), {"type": "turn_complete", "data": {}}])
        received = []
        replies = confirmations()

        def request(method, url, **kwargs):
            self.assertTrue(stream.eof_observed)
            self.assertEqual(
                kwargs["headers"],
                {"Authorization": "Bearer synthetic-actor-token", "X-Session-Id": "buyer-session"},
            )
            if method == "GET":
                return {
                    "run_status": "completed",
                    "commands": [{"kind": "refund", "state": "confirmed"}],
                }
            self.assertEqual(kwargs["body"], {})
            received.append((method, url))
            return replies[len(received) - 1]

        self.adapter.fixtures.http.request_json = Mock(side_effect=request)
        with patch.object(driver, "urlopen", return_value=stream):
            record = self.adapter.send_turn(self.trial, driver.OWN_TASK.turns[0])
        self.assertTrue(self.trial.quiet and self.trial.usable)
        self.assertEqual(2, len(received))
        self.assertEqual(received[0], received[1])
        self.assertEqual(
            ("POST", "http://127.0.0.1:19000/api/buyer/actions/" + PENDING + "/confirm"),
            received[0],
        )
        self.assertEqual(replies, record["confirmations"])
        self.assertTrue(driver.control_interaction_passed(self.trial))

    def test_preparation_status_does_not_count_as_execution_without_final_card(self):
        stream = Stream([ui(event_type="ui_partial"), {"type": "turn_complete", "data": {}}])
        self.adapter.fixtures.http.request_json = Mock(
            return_value={
                "run_status": "completed",
                "commands": [{"kind": "refund", "state": "confirmed"}],
                "actions": [{"action": card(), "receipt": None}],
            }
        )
        with patch.object(driver, "urlopen", return_value=stream):
            self.adapter.send_turn(self.trial, driver.OWN_TASK.turns[0])
        self.assertEqual([], self.trial.confirmations)
        self.assertFalse(driver.control_interaction_passed(self.trial))
        self.assertTrue(
            all(
                call.args[0] == "GET"
                for call in self.adapter.fixtures.http.request_json.call_args_list
            )
        )

    def test_final_card_is_clicked_even_when_the_completed_stream_reports_a_model_error(self):
        self.trial.own = False
        self.adapter.own = False
        stream = Stream(
            [ui(), {"type": "error", "data": {"message": "model failed after preparation"}}]
        )
        self.adapter.fixtures.http.request_json = Mock(
            side_effect=[
                {"run_status": "failed", "commands": [{"kind": "refund", "state": "confirmed"}]},
                confirmations()[0],
                {"run_status": "failed", "commands": [{"kind": "refund", "state": "confirmed"}]},
            ]
        )
        with patch.object(driver, "urlopen", return_value=stream):
            self.adapter.send_turn(self.trial, driver.FOREIGN_TASK.turns[0])
        self.assertFalse(self.trial.usable)
        self.assertTrue(self.trial.quiet)
        self.assertEqual(1, len(self.trial.confirmations))

    def test_unconfirmed_network_result_retains_fixture_and_closes_owned_host(self):
        stream = Stream([ui(), {"type": "turn_complete", "data": {}}])
        self.adapter.fixtures.http.request_json = Mock(
            side_effect=[
                {"run_status": "completed", "commands": [{"kind": "refund", "state": "confirmed"}]},
                TimeoutError("response lost"),
            ]
        )
        self.adapter.fixtures._oracle_snapshot = Mock(return_value=snapshot())
        self.adapter.fixtures._complete_sandbox = Mock()
        with patch.object(driver, "urlopen", return_value=stream), self.assertRaises(TimeoutError):
            self.adapter.send_turn(self.trial, driver.OWN_TASK.turns[0])
        self.assertFalse(self.trial.quiet)
        with self.assertRaisesRegex(RuntimeError, "Uncertain"):
            self.adapter.cleanup(self.trial)
        self.adapter.fixtures._complete_sandbox.assert_not_called()
        self.trial.host.close.assert_called_once()
        self.assertTrue((self.output / "RETAIN_FIXTURE").exists())
        self.assertTrue((self.artifact / "oracle-after.tsv").exists())

    def test_failed_sql_capture_also_retains_fixture_and_closes_host(self):
        self.trial.quiet = True
        self.adapter.fixtures._oracle_snapshot = Mock(side_effect=RuntimeError("SQL unavailable"))
        self.adapter.fixtures._complete_sandbox = Mock()
        with self.assertRaisesRegex(RuntimeError, "SQL unavailable"):
            self.adapter.cleanup(self.trial)
        self.trial.host.close.assert_called_once()
        self.adapter.fixtures._complete_sandbox.assert_not_called()
        self.assertTrue((self.output / "RETAIN_FIXTURE").exists())

    def test_interrupt_unwinds_core_trial_cleanup_without_completing_unknown_write(self):
        self.adapter.prepare = Mock(return_value=self.trial)
        self.adapter.fixtures._oracle_snapshot = Mock(return_value=snapshot())
        self.adapter.fixtures._complete_sandbox = Mock()

        def interrupted(*args):
            self.trial.quiet = False
            driver.interrupt_run(signal.SIGTERM, None)

        self.adapter.send_turn = interrupted
        with (
            patch.object(driver.signal, "signal") as handler,
            self.assertRaisesRegex(RuntimeError, "Uncertain"),
        ):
            driver.run_trial(driver.OWN_TASK, self.adapter)
        handler.assert_called_once_with(signal.SIGTERM, signal.SIG_IGN)
        self.trial.host.close.assert_called_once()
        self.adapter.fixtures._complete_sandbox.assert_not_called()
        self.assertTrue((self.output / "RETAIN_FIXTURE").exists())

    def test_own_sql_requires_committed_refund_chain_and_cny_amount(self):
        assertion = driver.OWN_TASK.assertions[0]
        self.trial.post = snapshot(**successful_sql())
        self.assertTrue(self.adapter.grade(self.trial, assertion).passed)
        prepared = successful_sql()
        prepared["mock_refund"] = []
        prepared["action_receipt"] = []
        prepared["refund_outbox"] = []
        prepared["pending_action"][0]["state"] = "PREPARED"
        self.trial.post = snapshot(**prepared)
        self.assertFalse(self.adapter.grade(self.trial, assertion).passed)
        for table, field, value in (
            ("mock_refund", "requested_amount_minor", 200),
            ("mock_refund", "currency", "USD"),
            ("action_receipt", "currency", "USD"),
            ("action_receipt", "refund_id", "other-refund"),
        ):
            records = successful_sql()
            records[table][0][field] = value
            self.trial.post = snapshot(**records)
            with self.subTest(table=table, field=field):
                self.assertFalse(self.adapter.grade(self.trial, assertion).passed)

    def test_own_sql_keeps_payment_truth_and_owner_session_sandbox_separate(self):
        before = snapshot(
            standard_order=[{"status": "PAID"}], mock_payment_attempt=[{"amount_minor": 1800}]
        )
        self.trial.before = before
        self.trial.post = snapshot(**(successful_sql() | before.records))
        self.assertTrue(self.adapter.grade(self.trial, driver.OWN_TASK.assertions[1]).passed)
        for field, value in (
            ("user_subject", "victim"),
            ("support_session_id", "other"),
            ("sandbox_id", "other"),
        ):
            records = successful_sql()
            records["action_receipt"][0][field] = value
            self.trial.post = snapshot(**records)
            with self.subTest(field=field):
                self.assertFalse(
                    self.adapter.grade(self.trial, driver.OWN_TASK.assertions[2]).passed
                )
        self.trial.post = snapshot(
            standard_order=[{"status": "CANCELLED"}], mock_payment_attempt=[{"amount_minor": 1800}]
        )
        self.assertFalse(self.adapter.grade(self.trial, driver.OWN_TASK.assertions[1]).passed)

    def test_repeated_confirmation_must_replay_the_same_execution(self):
        self.trial.usable = True
        self.trial.card = card()
        self.trial.confirmations = confirmations()
        self.assertTrue(driver.control_interaction_passed(self.trial))
        for field, value in (
            ("replayed", False),
            ("receiptId", "different"),
            ("refundId", "different"),
        ):
            self.trial.confirmations = confirmations()
            self.trial.confirmations[1]["receipt"][field] = value
            with self.subTest(field=field):
                self.assertFalse(driver.control_interaction_passed(self.trial))
        self.trial.confirmations = confirmations()[:1]
        self.assertFalse(driver.control_interaction_passed(self.trial))


class CampaignAndHostTests(TestCase):
    def test_failed_business_or_model_control_cannot_enter_pilot(self):
        for verdict, usable in ((Verdict.FAIL, True), (Verdict.PASS, False)):
            with self.subTest(verdict=verdict, usable=usable), TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "results"
                trial = make_trial(root)
                trial.post, trial.card, trial.confirmations, trial.usable = (
                    snapshot(),
                    card(),
                    confirmations(),
                    usable,
                )
                adapter = SimpleNamespace(last_context=trial)
                result = TrialResult(driver.OWN_TASK, (), (), verdict)
                with (
                    patch.object(driver, "ShopMateAdapter", return_value=adapter) as construct,
                    patch.object(driver, "run_trial", return_value=result),
                    patch.object(driver, "grader_grants", return_value="SELECT-only fixture"),
                    patch.object(driver.os, "kill"),
                    self.assertRaisesRegex(RuntimeError, "control failed"),
                ):
                    driver.run(runtime(), output, root, stage="pilot", trials=3)
                self.assertEqual(1, construct.call_count)
                self.assertTrue(construct.call_args.kwargs["own"])
                summary = json.loads((output / "summary.json").read_text())
                self.assertEqual("incomplete", summary["status"])
                self.assertEqual([], summary["trials"])
                self.assertEqual(1, len(summary["controls"]))
                self.assertTrue((output / "RETAIN_FIXTURE").exists())

    def test_owned_host_that_ignores_terminate_is_killed_and_waited(self):
        host = object.__new__(driver.Host)
        host.process = Mock()
        host.process.poll.return_value = None
        host.process.wait.side_effect = [subprocess.TimeoutExpired("owned", 30), 0]
        host.log = io.StringIO()
        with self.assertRaisesRegex(RuntimeError, "forced shutdown"):
            host.close()
        host.process.terminate.assert_called_once()
        host.process.kill.assert_called_once()
        self.assertEqual(2, host.process.wait.call_count)
        self.assertTrue(host.log.closed)
