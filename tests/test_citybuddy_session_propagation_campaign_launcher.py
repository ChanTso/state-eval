from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from test_citybuddy_ownership_campaign_launcher import (
    ATTESTATION,
    CITYBUDDY_SHA,
    ROOT,
    SECRET,
    STATEEVAL_SHA,
    LauncherFixture,
    _write_executable,
)


SCRIPT = ROOT / "scripts" / "run_citybuddy_session_propagation_campaign.sh"


def _manifest() -> dict[str, object]:
    return {
        "schema": "stateeval.citybuddy-session-propagation-campaign/v1",
        "campaign": "citybuddy-session-propagation",
        "phase": "calibration",
        "stateEvalCommit": STATEEVAL_SHA,
        "boundary": {
            "citybuddyCommit": CITYBUDDY_SHA,
            "model": "gpt-5.4",
            "temperature": {"valueSent": 0.0},
            "modelRequestTimeoutSeconds": 30.0,
        },
        "plan": {"seed": 2026083103, "blocks": 10, "plannedSlots": 100},
    }


def _run_fixture(
    fixture: LauncherFixture,
    action: str,
    output: Path,
    manifest: Path,
    *,
    updates: dict[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    fixture.reset_logs()
    environment = dict(fixture.environment)
    environment["FAKE_MANIFEST_PATH"] = str(manifest)
    for name, value in (updates or {}).items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), action, "--output", str(output)],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _install_session_python_fake(fixture: LauncherFixture) -> None:
    _write_executable(
        fixture.fake_bin / "python3",
        r"""
        #!/bin/sh
        for argument in "$@"; do
          if [ "$argument" = -c ]; then exec "$REAL_PYTHON" "$@"; fi
        done
        output=''
        previous=''
        for argument in "$@"; do
          if [ "$previous" = --output ]; then output="$argument"; fi
          previous="$argument"
        done
        printf '%s\n' \
          "python|commerce_on=$STATEEVAL_COMMERCE_ON_BASE_URL|commerce_off=$STATEEVAL_COMMERCE_OFF_BASE_URL|agent_on=$STATEEVAL_AGENT_ON_BASE_URL|agent_off=$STATEEVAL_AGENT_OFF_BASE_URL|on_flag=$STATEEVAL_SESSION_PROPAGATION_ON_ENABLED|off_flag=$STATEEVAL_SESSION_PROPAGATION_OFF_ENABLED|on_launch=$STATEEVAL_SESSION_ON_LAUNCH_ID|off_launch=$STATEEVAL_SESSION_OFF_LAUNCH_ID|on_pid=$STATEEVAL_SESSION_ON_PID|off_pid=$STATEEVAL_SESSION_OFF_PID|workers=$STATEEVAL_AGENT_WORKERS|layout=$STATEEVAL_AGENT_HTTP_CLIENT_LAYOUT|trace=$STATEEVAL_TRACE_EXPORT_ENABLED|metrics=$STATEEVAL_METRICS_ENABLED|args=$*" \
          >> "$PYTHON_LOG"
        if [ ! -d "$output" ]; then
          /bin/mkdir "$output"
          /bin/cp "$FAKE_MANIFEST_PATH" "$output/manifest.json"
        fi
        : > "$output/preserved-artifact"
        exit 0
        """,
    )


class SessionPropagationLauncherContractTest(TestCase):
    def test_script_is_executable_and_has_the_fixed_campaign_flags(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(SCRIPT.stat().st_mode & stat.S_IXUSR)
        syntax = subprocess.run(
            ["/bin/bash", "-n", str(SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, syntax.returncode, syntax.stderr)
        self.assertIn(
            'expected_citybuddy_commit="09130fa3c0209648f98781ff0892c3d07a55e59f"',
            source,
        )
        self.assertIn(
            "--citybuddy.evaluation.action-ownership-binding-enabled=false", source
        )
        self.assertIn("STATEEVAL_SESSION_PROPAGATION_ON_ENABLED=true", source)
        self.assertIn("STATEEVAL_SESSION_PROPAGATION_OFF_ENABLED=false", source)


class SessionPropagationLauncherFixtureTest(TestCase):
    def test_preflight_prints_the_fixed_plan_without_starting_topology(self) -> None:
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            manifest = fixture.root / "session-manifest.json"
            manifest.write_text(json.dumps(_manifest()) + "\n", encoding="utf-8")
            output = fixture.output("session-preflight")

            result = _run_fixture(fixture, "preflight", output, manifest)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(output.exists())
            for line in (
                "seed=2026083103",
                "blocks=10",
                "slots=100",
            ):
                self.assertIn(line, result.stdout)
            logs = fixture.text_logs()
            self.assertTrue(
                all(line.startswith("git|") for line in logs.splitlines()), logs
            )
            self.assertNotIn(SECRET, result.stdout + result.stderr + logs)

    def test_execute_uses_one_commerce_and_two_isolated_measured_agents(self) -> None:
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            _install_session_python_fake(fixture)
            manifest = fixture.root / "session-manifest.json"
            manifest.write_text(json.dumps(_manifest()) + "\n", encoding="utf-8")
            output = fixture.output("session-calibration")

            result = _run_fixture(fixture, "execute", output, manifest)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((output / "manifest.json").is_file())
            agent_lines = fixture.agent_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(3, len(agent_lines))
            control = next(line for line in agent_lines if line.startswith("agent|control|"))
            self.assertIn("session=true|trace=[]|metrics=false|key=empty", control)
            measured = [line for line in agent_lines if not line.startswith("agent|control|")]
            self.assertEqual(2, len(measured))
            self.assertTrue(
                all("workers=1|layout=shared" in line for line in measured), measured
            )
            self.assertEqual(
                {"true", "false"},
                {
                    line.split("|session=", 1)[1].split("|", 1)[0]
                    for line in measured
                },
            )
            self.assertTrue(all("key=present" in line for line in measured))

            python_line = fixture.python_log.read_text(encoding="utf-8").strip()
            fields = {
                name: value
                for name, value in (
                    field.split("=", 1) for field in python_line.split("|")[1:]
                )
            }
            self.assertEqual(fields["commerce_on"], fields["commerce_off"])
            self.assertNotEqual(fields["agent_on"], fields["agent_off"])
            self.assertEqual("true", fields["on_flag"])
            self.assertEqual("false", fields["off_flag"])
            self.assertNotEqual(fields["on_launch"], fields["off_launch"])
            self.assertNotEqual(fields["on_pid"], fields["off_pid"])
            self.assertEqual("1", fields["workers"])
            self.assertEqual("shared", fields["layout"])
            self.assertEqual("false", fields["trace"])
            self.assertEqual("false", fields["metrics"])
            self.assertIn(
                "args=-m stateeval.citybuddy_session_propagation_campaign "
                f"--output {output}",
                python_line,
            )
            self.assertNotIn("--phase", python_line)
            self.assertNotIn("--resume", python_line)
            docker = fixture.docker_log.read_text(encoding="utf-8")
            self.assertIn("stateeval-session-propagation-calibration-", docker)
            self.assertIn("down --volumes --remove-orphans", docker)
            self.assertFalse((fixture.root / "runtime").exists())
            self.assertNotIn(SECRET, result.stdout + result.stderr + fixture.text_logs())

    def test_resume_passes_only_the_resume_flag_and_preserves_namespace(self) -> None:
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            _install_session_python_fake(fixture)
            manifest = fixture.root / "session-manifest.json"
            manifest.write_text(json.dumps(_manifest()) + "\n", encoding="utf-8")
            output = fixture.output("session-resume")
            output.mkdir()
            (output / "manifest.json").write_text(
                json.dumps(_manifest()) + "\n", encoding="utf-8"
            )

            result = _run_fixture(fixture, "resume", output, manifest)

            self.assertEqual(0, result.returncode, result.stderr)
            python_line = fixture.python_log.read_text(encoding="utf-8").strip()
            self.assertTrue(python_line.endswith("--resume"), python_line)
            self.assertEqual(1, python_line.count("--resume"))
            self.assertTrue((output / "preserved-artifact").is_file())
            self.assertIn(
                "down --volumes --remove-orphans",
                fixture.docker_log.read_text(encoding="utf-8"),
            )

    def test_proxy_boundary_rejects_mismatches_before_topology(self) -> None:
        cases = (
            {"AGENT_MODEL_PROXY_API_KEY": None},
            {"STATEEVAL_MODEL_NAME": "other-alias"},
            {"STATEEVAL_MODEL_TEMPERATURE": "nan"},
            {"STATEEVAL_MODEL_TIMEOUT_SECONDS": "0"},
            {"STATEEVAL_PROXY_ATTESTATION": ATTESTATION + "-mismatch"},
        )
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            manifest = fixture.root / "session-manifest.json"
            manifest.write_text(json.dumps(_manifest()) + "\n", encoding="utf-8")
            for index, updates in enumerate(cases):
                with self.subTest(updates=updates):
                    output = fixture.output(f"proxy-rejected-{index}")
                    result = _run_fixture(
                        fixture,
                        "preflight",
                        output,
                        manifest,
                        updates=updates,
                    )
                    self.assertEqual(2, result.returncode)
                    self.assertFalse(output.exists())
                    logs = fixture.text_logs()
                    self.assertEqual("", logs)
                    self.assertNotIn(SECRET, result.stdout + result.stderr + logs)
