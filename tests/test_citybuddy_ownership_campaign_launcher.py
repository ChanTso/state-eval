from __future__ import annotations

import json
import os
import re
import signal
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "run_citybuddy_ownership_campaign.sh"
BASE_SHA = "df60ce6f920f83a593057fbf71dc25ab06727755"
STATEEVAL_SHA = "a" * 40
CITYBUDDY_SHA = "09130fa3c0209648f98781ff0892c3d07a55e59f"
ATTESTATION = (
    "CLIProxyAPI/7.2.76/"
    "9f62c8df28dc749ea976865450a458917bf45042/"
    "ad8d0e9d43888c794f32d9a36842c395f641038a1a622f650c7868dc6a359f0d"
)
SECRET = "LAUNCHER-SECRET-MUST-NOT-LEAK"


def _write_executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def _manifest(phase: str) -> dict[str, object]:
    seed, blocks, slots = {
        "calibration": (2026083101, 10, 100),
        "formal": (2026083102, 60, 600),
    }[phase]
    return {
        "schema": "stateeval.citybuddy-ownership-campaign/v2",
        "campaign": "citybuddy-ownership",
        "phase": phase,
        "stateEvalCommit": STATEEVAL_SHA,
        "boundary": {
            "citybuddyCommit": CITYBUDDY_SHA,
            "model": "gpt-5.4",
            "temperature": {"valueSent": 0.0},
            "modelRequestTimeoutSeconds": 30.0,
            "proxy": {
                "name": "CLIProxyAPI",
                "version": "7.2.76",
                "commit": "9f62c8df28dc749ea976865450a458917bf45042",
                "binarySha256": (
                    "ad8d0e9d43888c794f32d9a36842c395f641038a1a622f650c7868dc6a359f0d"
                ),
                "provenanceBasis": (
                    "operator-attested pre-run deployment inspection; these values "
                    "were not returned by model responses"
                ),
            },
            "agentRuntime": {
                "agentWorkers": 1,
                "agentHttpClientLayout": "shared",
                "evaluationSessionPropagationEnabled": True,
                "traceExportEnabled": False,
                "metricsEnabled": False,
            },
        },
        "plan": {
            "seed": seed,
            "blocks": blocks,
            "plannedSlots": slots,
        },
    }


class LauncherFixture:
    def __init__(self, root: Path) -> None:
        root = root.resolve()
        self.root = root
        self.citybuddy = root / "citybuddy-worktree"
        self.fake_bin = root / "fake-bin"
        self.outputs = root / "outputs"
        self.events = root / "events.log"
        self.docker_log = root / "docker.log"
        self.agent_log = root / "agents.log"
        self.python_log = root / "python.log"
        self.ready = root / "python.ready"
        self.docker_ready = root / "docker.ready"
        self.outputs.mkdir(parents=True)
        self.fake_bin.mkdir()
        for path in (
            self.events,
            self.docker_log,
            self.agent_log,
            self.python_log,
        ):
            path.write_text("", encoding="utf-8")
        self.manifests: dict[str, Path] = {}
        for phase in ("calibration", "formal"):
            path = root / f"{phase}-manifest.json"
            path.write_text(json.dumps(_manifest(phase)) + "\n", encoding="utf-8")
            self.manifests[phase] = path
        self._write_citybuddy()
        self._write_fakes()
        self.environment = dict(os.environ)
        self.environment.update(
            {
                "PATH": f"{self.fake_bin}{os.pathsep}{os.defpath}",
                "CITYBUDDY_REPO": str(self.citybuddy),
                "AGENT_MODEL_PROXY_URL": "http://proxy.invalid/v1",
                "AGENT_MODEL_PROXY_API_KEY": SECRET,
                "STATEEVAL_MODEL_NAME": "gpt-5.4",
                "STATEEVAL_MODEL_TEMPERATURE": "0",
                "STATEEVAL_MODEL_TIMEOUT_SECONDS": "30",
                "STATEEVAL_PROXY_ATTESTATION": ATTESTATION,
                "FAKE_ROOT": str(self.root),
                "FAKE_STATEEVAL_ROOT": str(ROOT),
                "FAKE_CITYBUDDY_ROOT": str(self.citybuddy),
                "FAKE_STATEEVAL_SHA": STATEEVAL_SHA,
                "FAKE_CITYBUDDY_SHA": CITYBUDDY_SHA,
                "FAKE_STATEEVAL_DIRTY": "",
                "FAKE_CITYBUDDY_DIRTY": "",
                "FAKE_LINKED_WORKTREE": "1",
                "FAKE_LAUNCHER_TRACKED": "1",
                "FAKE_BASE_PRESENT": "1",
                "FAKE_PYTHON_MODE": "success",
                "FAKE_DOCKER_MODE": "success",
                "EVENT_LOG": str(self.events),
                "DOCKER_LOG": str(self.docker_log),
                "AGENT_LOG": str(self.agent_log),
                "PYTHON_LOG": str(self.python_log),
                "PYTHON_READY": str(self.ready),
                "DOCKER_READY": str(self.docker_ready),
                "REAL_PYTHON": sys.executable,
                # Deliberately polluted values must never reach an agent unchanged.
                "AGENT_WORKERS": "99",
                "AGENT_HTTP_CLIENT_LAYOUT": "per-authority",
                "AGENT_EVALUATION_SESSION_PROPAGATION_ENABLED": "false",
                "CITYBUDDY_TRACE_EXPORT_URL": "https://trace-secret.invalid",
                "CITYBUDDY_METRICS_ENABLED": "true",
                "CLIPROXY_API_KEY": SECRET,
                "PYTHONPATH": "/polluted/pythonpath",
            }
        )

    def _write_citybuddy(self) -> None:
        scripts = self.citybuddy / "scripts"
        scripts.mkdir(parents=True)
        (self.citybuddy / "compose.yaml").write_text("name: fixture\n", encoding="utf-8")
        (scripts / "fake_litellm_server.py").write_text("", encoding="utf-8")
        (scripts / "hash_test_credential.py").write_text("", encoding="utf-8")
        _write_executable(
            scripts / "test_dynamic_ports.sh",
            r"""
            #!/usr/bin/env bash
            port_log_offset() { printf -v "$1" '%s' 0; }
            process_bound_port() {
              case "$1" in
                stateeval_auth_port) value=41001 ;;
                stateeval_commerce_on_port) value=41002 ;;
                stateeval_commerce_off_port) value=41003 ;;
                stateeval_fixture_model_port) value=41004 ;;
                stateeval_control_agent_port) value=41005 ;;
                stateeval_agent_on_port) value=41006 ;;
                stateeval_agent_off_port) value=41007 ;;
                *) return 91 ;;
              esac
              printf -v "$1" '%s' "$value"
            }
            compose_host_port() { printf -v "$1" '%s' 43306; }
            finish_test_cleanup() {
              local original_status="$1"
              local resource_status="$2"
              if ((original_status != 0)); then return "$original_status"; fi
              if ((resource_status != 0)); then
                trap - EXIT
                exit "$resource_status"
              fi
            }
            """,
        )
        _write_executable(
            scripts / "init_local.sh",
            r"""
            #!/bin/sh
            printf '%s\n' \
              'MYSQL_BOOTSTRAP_PASSWORD=root' \
              'MYSQL_AUTH_APP_PASSWORD=auth' \
              'MYSQL_COMMERCE_APP_PASSWORD=commerce' \
              'MYSQL_AGENT_APP_PASSWORD=agent' > "$ENV_FILE"
            printf 'init|key=%s\n' "${AGENT_MODEL_PROXY_API_KEY+set}" >> "$EVENT_LOG"
            """,
        )
        _write_executable(
            self.citybuddy / "mvnw",
            """
            #!/bin/sh
            printf 'mvnw|%s|key=%s\n' "$*" "${AGENT_MODEL_PROXY_API_KEY+set}" >> "$EVENT_LOG"
            exit 0
            """,
        )

    def _write_fakes(self) -> None:
        _write_executable(
            self.fake_bin / "git",
            r"""
            #!/bin/sh
            repository=''
            if [ "$1" = -C ]; then repository="$2"; shift 2; fi
            if [ "${AGENT_MODEL_PROXY_API_KEY+x}" = x ]; then key=set; else key=unset; fi
            printf 'git|%s|%s|key=%s|locks=%s\n' \
              "$repository" "$*" "$key" "${GIT_OPTIONAL_LOCKS-unset}" >> "$EVENT_LOG"
            case "$1:$2:$3" in
              rev-parse:--show-toplevel:*) printf '%s\n' "$repository" ;;
              rev-parse:HEAD:*)
                if [ "$repository" = "$FAKE_STATEEVAL_ROOT" ]; then
                  printf '%s\n' "$FAKE_STATEEVAL_SHA"
                else
                  printf '%s\n' "$FAKE_CITYBUDDY_SHA"
                fi
                ;;
              rev-parse:--path-format=absolute:--git-dir)
                printf '%s\n' "$repository/.git/worktrees/frozen"
                ;;
              rev-parse:--path-format=absolute:--git-common-dir)
                if [ "$FAKE_LINKED_WORKTREE" = 1 ]; then
                  printf '%s\n' "$repository/.git"
                else
                  printf '%s\n' "$repository/.git/worktrees/frozen"
                fi
                ;;
              merge-base:--is-ancestor:*) [ "$FAKE_BASE_PRESENT" = 1 ] ;;
              cat-file:-e:*) [ "$FAKE_LAUNCHER_TRACKED" = 1 ] ;;
              status:--porcelain=v1:--untracked-files=no)
                if [ "$repository" = "$FAKE_STATEEVAL_ROOT" ]; then
                  printf '%s' "$FAKE_STATEEVAL_DIRTY"
                else
                  printf '%s' "$FAKE_CITYBUDDY_DIRTY"
                fi
                ;;
              *) exit 92 ;;
            esac
            """,
        )
        _write_executable(
            self.fake_bin / "python3",
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
            printf 'python|workers=%s|layout=%s|session=%s|trace=%s|metrics=%s|pythonpath=%s|args=%s\n' \
              "$STATEEVAL_AGENT_WORKERS" \
              "$STATEEVAL_AGENT_HTTP_CLIENT_LAYOUT" \
              "$STATEEVAL_EVALUATION_SESSION_PROPAGATION_ENABLED" \
              "$STATEEVAL_TRACE_EXPORT_ENABLED" \
              "$STATEEVAL_METRICS_ENABLED" \
              "$PYTHONPATH" "$*" >> "$PYTHON_LOG"
            if [ ! -d "$output" ]; then
              /bin/mkdir "$output"
              /bin/cp "$FAKE_MANIFEST_PATH" "$output/manifest.json"
            fi
            : > "$output/preserved-artifact"
            case "$FAKE_PYTHON_MODE" in
              success) exit 0 ;;
              incomplete) exit 2 ;;
              failure) exit 7 ;;
              wait)
                : > "$PYTHON_READY"
                trap 'exit 130' INT
                trap 'exit 143' TERM
                while :; do /bin/sleep 1; done
                ;;
              *) exit 93 ;;
            esac
            """,
        )
        _write_executable(
            self.fake_bin / "docker",
            r"""
            #!/bin/sh
            printf 'docker|%s\n' "$*" >> "$DOCKER_LOG"
            if [ "$FAKE_DOCKER_MODE" = wait-exec ]; then
              case " $* " in
                *' exec '*)
                  : > "$DOCKER_READY"
                  trap 'exit 143' TERM
                  while :; do /bin/sleep 1; done
                  ;;
              esac
            fi
            case " $* " in
              *' port mysql 3306 '*) printf '127.0.0.1:43306\n' ;;
              *' ps --quiet mysql '*) printf 'fixture-mysql-container\n' ;;
              *) exit 0 ;;
            esac
            """,
        )
        _write_executable(
            self.fake_bin / "mktemp",
            r"""
            #!/bin/sh
            path="$FAKE_ROOT/runtime"
            /bin/mkdir "$path"
            printf 'mktemp|%s\n' "$path" >> "$EVENT_LOG"
            printf '%s\n' "$path"
            """,
        )
        _write_executable(
            self.fake_bin / "openssl",
            r"""
            #!/bin/sh
            printf 'openssl|%s|key=%s\n' "$*" "${AGENT_MODEL_PROXY_API_KEY+set}" >> "$EVENT_LOG"
            if [ "$1" = rand ]; then
              printf 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789\n'
              exit 0
            fi
            previous=''
            for argument in "$@"; do
              if [ "$previous" = -out ]; then : > "$argument"; fi
              previous="$argument"
            done
            """,
        )
        _write_executable(
            self.fake_bin / "make",
            r"""
            #!/bin/sh
            printf 'make|%s|key=%s\n' "$*" "${AGENT_MODEL_PROXY_API_KEY+set}" >> "$EVENT_LOG"
            exit 0
            """,
        )
        _write_executable(
            self.fake_bin / "curl",
            r"""
            #!/bin/sh
            printf 'curl|%s|key=%s\n' "$*" "${AGENT_MODEL_PROXY_API_KEY+set}" >> "$EVENT_LOG"
            exit 0
            """,
        )
        _write_executable(
            self.fake_bin / "java",
            r"""
            #!/bin/sh
            printf 'java|key=%s\n' "${AGENT_MODEL_PROXY_API_KEY+set}" >> "$EVENT_LOG"
            trap 'exit 0' HUP INT TERM
            while :; do /bin/sleep 1; done
            """,
        )
        _write_executable(
            self.fake_bin / "uv",
            r"""
            #!/bin/sh
            case "$*" in
              *hash_test_credential.py*)
                printf 'uv-hash|key=%s\n' "${AGENT_MODEL_PROXY_API_KEY+set}" >> "$EVENT_LOG"
                printf 'fixture-hash\n'
                exit 0
                ;;
              *fake_litellm_server.py*)
                printf 'uv-fixture|key=%s\n' "${AGENT_MODEL_PROXY_API_KEY+set}" >> "$EVENT_LOG"
                ;;
              *citybuddy-agent*)
                case "$AGENT_PRIMARY_ROLE_ALIAS:$AGENT_COMMERCE_TOOLS_URL" in
                  support-standard-primary:*) label=control ;;
                  *:41002) label=on ;;
                  *:41003) label=off ;;
                  *) label=unknown ;;
                esac
                if [ -n "${AGENT_MODEL_PROXY_API_KEY-}" ]; then key=present; else key=empty; fi
                printf 'agent|%s|workers=%s|layout=%s|session=%s|trace=[%s]|metrics=%s|key=%s\n' \
                  "$label" "$AGENT_WORKERS" "$AGENT_HTTP_CLIENT_LAYOUT" \
                  "$AGENT_EVALUATION_SESSION_PROPAGATION_ENABLED" \
                  "$CITYBUDDY_TRACE_EXPORT_URL" "$CITYBUDDY_METRICS_ENABLED" \
                  "$key" >> "$AGENT_LOG"
                ;;
              *) exit 94 ;;
            esac
            trap 'exit 0' HUP INT TERM
            while :; do /bin/sleep 1; done
            """,
        )

    def reset_logs(self) -> None:
        for path in (self.events, self.docker_log, self.agent_log, self.python_log):
            path.write_text("", encoding="utf-8")
        self.ready.unlink(missing_ok=True)
        self.docker_ready.unlink(missing_ok=True)

    def output(self, name: str) -> Path:
        return self.outputs / name

    def write_resume_output(self, output: Path, phase: str = "calibration") -> None:
        output.mkdir(parents=True)
        (output / "manifest.json").write_text(
            json.dumps(_manifest(phase)) + "\n", encoding="utf-8"
        )

    def command(
        self,
        action: str,
        *,
        phase: str = "calibration",
        output: Path | None = None,
    ) -> list[str]:
        return [
            "/bin/bash",
            str(SCRIPT),
            action,
            "--phase",
            phase,
            "--output",
            str(output or self.output("campaign")),
        ]

    def run(
        self,
        action: str,
        *,
        phase: str = "calibration",
        output: Path | None = None,
        updates: dict[str, str | None] | None = None,
        reset_logs: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if reset_logs:
            self.reset_logs()
        environment = dict(self.environment)
        environment["FAKE_MANIFEST_PATH"] = str(self.manifests[phase])
        for name, value in (updates or {}).items():
            if value is None:
                environment.pop(name, None)
            else:
                environment[name] = value
        return subprocess.run(
            self.command(action, phase=phase, output=output),
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def text_logs(self) -> str:
        return "".join(
            path.read_text(encoding="utf-8")
            for path in (self.events, self.docker_log, self.agent_log, self.python_log)
        )


class LauncherContractTest(TestCase):
    def test_script_is_executable_bash_3_2_compatible_and_has_no_gnu_only_tokens(
        self,
    ) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(SCRIPT.stat().st_mode & stat.S_IXUSR)
        self.assertTrue(source.startswith("#!/usr/bin/env bash\nset -euo pipefail\n"))
        syntax = subprocess.run(
            ["/bin/bash", "-n", str(SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, syntax.returncode, syntax.stderr)
        for forbidden in (
            "declare -A",
            "local -n",
            "mapfile",
            "readarray",
            "wait -n",
            "${value,,}",
            "readlink -f",
            "realpath ",
            "sed -r",
            "grep -P",
            "mktemp --directory",
            "sha256sum",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_cli_has_no_defaults_and_rejects_unknown_or_free_plan_arguments(self) -> None:
        cases = (
            [],
            ["unknown", "--phase", "calibration", "--output", "/tmp/x"],
            ["execute", "--output", "/tmp/x"],
            ["execute", "--phase", "calibration"],
            ["execute", "--phase", "unknown", "--output", "/tmp/x"],
            [
                "execute",
                "--phase",
                "calibration",
                "--phase",
                "formal",
                "--output",
                "/tmp/x",
            ],
            [
                "execute",
                "--phase",
                "calibration",
                "--output",
                "/tmp/x",
                "--output",
                "/tmp/y",
            ],
            ["execute", "--phase", "calibration", "--output", "/tmp/x", "--seed", "1"],
            ["execute", "--phase", "calibration", "--output", "/tmp/x", "--blocks", "1"],
            ["execute", "--phase", "calibration", "--output", "/tmp/x", "--resume"],
        )
        with TemporaryDirectory() as temporary:
            empty_path = Path(temporary)
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    result = subprocess.run(
                        ["/bin/bash", str(SCRIPT), *arguments],
                        cwd=ROOT,
                        env={"PATH": str(empty_path)},
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(2, result.returncode)
                    self.assertEqual("", result.stdout)

    def test_frozen_milestone_objects_are_unchanged(self) -> None:
        expected = {
            "scripts/run_citybuddy_ownership_ablation.sh": (
                "a3164ef0677d051e778a67c713ee3c271a9a74ad"
            ),
            "results/milestone-1": "40f6aca133c2f64334af7f2a68db96bb5521a115",
            "results/milestone-2": "88d6c1f72874a777071d2b0e2bac4ebbded1a74b",
        }
        for path, object_id in expected.items():
            with self.subTest(path=path):
                completed = subprocess.run(
                    ["git", "rev-parse", f"HEAD:{path}"],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(object_id, completed.stdout.strip())


class LauncherPreflightTest(TestCase):
    def test_preflight_prints_both_fixed_plans_and_has_no_topology_side_effects(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            for phase, seed, blocks, slots in (
                ("calibration", 2026083101, 10, 100),
                ("formal", 2026083102, 60, 600),
            ):
                output = fixture.output(phase)
                result = fixture.run("preflight", phase=phase, output=output)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertFalse(output.exists())
                for line in (
                    f"phase={phase}",
                    f"seed={seed}",
                    f"blocks={blocks}",
                    f"slots={slots}",
                    f"citybuddy_commit={CITYBUDDY_SHA}",
                    f"stateeval_commit={STATEEVAL_SHA}",
                    "agent_workers=1",
                    "agent_http_client_layout=shared",
                    "evaluation_session_propagation_enabled=true",
                    "trace_export_url=<empty>",
                    "metrics_enabled=false",
                ):
                    self.assertIn(line, result.stdout)
                logs = fixture.text_logs()
                for forbidden in (
                    "docker|",
                    "mktemp|",
                    "curl|",
                    "java|",
                    "make|",
                    "openssl|",
                    "agent|",
                    "python|",
                ):
                    self.assertNotIn(forbidden, logs)
                self.assertNotIn(SECRET, result.stdout + result.stderr + logs)
                git_lines = [line for line in logs.splitlines() if line.startswith("git|")]
                self.assertTrue(git_lines)
                self.assertTrue(all("key=unset|locks=0" in line for line in git_lines))

    def test_proxy_boundary_fails_closed_without_leaking_or_starting_topology(self) -> None:
        cases = (
            {"AGENT_MODEL_PROXY_API_KEY": None},
            {"STATEEVAL_PROXY_ATTESTATION": "wrong"},
            {"AGENT_MODEL_PROXY_URL": "http://"},
            {"AGENT_MODEL_PROXY_URL": "http://:123"},
        )
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            for index, updates in enumerate(cases):
                with self.subTest(updates=updates):
                    output = fixture.output(f"rejected-{index}")
                    result = fixture.run(
                        "preflight", output=output, updates=updates
                    )
                    self.assertEqual(2, result.returncode)
                    self.assertFalse(output.exists())
                    self.assertEqual("", fixture.text_logs())
                    self.assertNotIn(SECRET, result.stdout + result.stderr)

    def test_repository_identity_and_dirty_gates_run_before_topology(self) -> None:
        cases = (
            {"FAKE_CITYBUDDY_SHA": "b" * 40},
            {"FAKE_STATEEVAL_DIRTY": " M src/stateeval/core.py\n"},
            {"FAKE_CITYBUDDY_DIRTY": " M agent-service/src/main.py\n"},
            {"FAKE_LINKED_WORKTREE": "0"},
            {"FAKE_LAUNCHER_TRACKED": "0"},
            {"FAKE_BASE_PRESENT": "0"},
        )
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            for index, updates in enumerate(cases):
                with self.subTest(updates=updates):
                    output = fixture.output(f"repo-rejected-{index}")
                    result = fixture.run("preflight", output=output, updates=updates)
                    self.assertNotEqual(0, result.returncode)
                    self.assertFalse(output.exists())
                    self.assertNotIn("docker|", fixture.text_logs())

    def test_output_modes_reject_existing_missing_invalid_old_and_symlink_paths(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))

            existing = fixture.output("existing")
            existing.mkdir()
            self.assertEqual(2, fixture.run("execute", output=existing).returncode)

            missing = fixture.output("missing")
            self.assertEqual(2, fixture.run("resume", output=missing).returncode)

            malformed = fixture.output("malformed")
            malformed.mkdir()
            (malformed / "manifest.json").write_text("not json", encoding="utf-8")
            self.assertEqual(2, fixture.run("resume", output=malformed).returncode)

            old_parent = fixture.root / "stateeval-m2-123"
            old_parent.mkdir()
            self.assertEqual(
                2,
                fixture.run("preflight", output=old_parent / "campaign").returncode,
            )

            milestone_parent = fixture.root / "milestone-2"
            milestone_parent.mkdir()
            self.assertEqual(
                2,
                fixture.run("preflight", output=milestone_parent / "campaign").returncode,
            )

            city_output = fixture.citybuddy / "campaign-output"
            self.assertEqual(
                2, fixture.run("preflight", output=city_output).returncode
            )

            target = fixture.output("target")
            fixture.write_resume_output(target)
            alias = fixture.output("alias")
            alias.symlink_to(target, target_is_directory=True)
            self.assertEqual(2, fixture.run("resume", output=alias).returncode)

            relative = fixture.run("preflight", output=Path("relative-output"))
            self.assertEqual(2, relative.returncode)
            self.assertNotIn("docker|", fixture.text_logs())

class LauncherExecutionTest(TestCase):
    def test_execute_passes_exact_agent_and_python_boundaries_and_cleans_topology(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            output = fixture.output("execute")
            result = fixture.run("execute", output=output)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((output / "manifest.json").is_file())
            python_line = fixture.python_log.read_text(encoding="utf-8").strip()
            self.assertIn(
                "workers=1|layout=shared|session=true|trace=false|metrics=false|pythonpath=src",
                python_line,
            )
            self.assertIn(
                f"args=-m stateeval.citybuddy_ownership_campaign --phase calibration --output {output}",
                python_line,
            )
            self.assertNotIn("--resume", python_line)
            self.assertNotRegex(python_line, r"--(?:seed|blocks)")

            agent_lines = sorted(
                fixture.agent_log.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(3, len(agent_lines))
            for line in agent_lines:
                self.assertIn("workers=1|layout=shared|session=true|trace=[]|metrics=false", line)
            control = next(line for line in agent_lines if line.startswith("agent|control|"))
            self.assertIn("key=empty", control)
            for label in ("on", "off"):
                line = next(item for item in agent_lines if item.startswith(f"agent|{label}|"))
                self.assertIn("key=present", line)

            events = fixture.events.read_text(encoding="utf-8")
            for line in events.splitlines():
                if not line.startswith("git|"):
                    self.assertNotIn("key=set", line)
            docker = fixture.docker_log.read_text(encoding="utf-8")
            self.assertIn("down --volumes --remove-orphans", docker)
            self.assertNotIn("stateeval-m2", docker)
            projects = re.findall(r"--project-name (stateeval-ownership-campaign-[^ ]+)", docker)
            self.assertTrue(projects)
            self.assertEqual(1, len(set(projects)))
            self.assertFalse((fixture.root / "runtime").exists())
            self.assertNotIn(SECRET, result.stdout + result.stderr + fixture.text_logs())

    def test_resume_passes_only_the_resume_flag(self) -> None:
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            output = fixture.output("resume")
            fixture.write_resume_output(output)
            result = fixture.run("resume", output=output)
            self.assertEqual(0, result.returncode, result.stderr)
            python_line = fixture.python_log.read_text(encoding="utf-8").strip()
            self.assertTrue(python_line.endswith("--resume"), python_line)
            self.assertEqual(1, python_line.count("--resume"))

    def test_incomplete_and_ordinary_failures_keep_resumable_output_and_status(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            for index, (mode, expected) in enumerate(
                (("incomplete", 2), ("failure", 7))
            ):
                output = fixture.output(f"failed-{index}")
                result = fixture.run(
                    "execute",
                    output=output,
                    updates={"FAKE_PYTHON_MODE": mode},
                )
                self.assertEqual(expected, result.returncode, result.stderr)
                self.assertTrue((output / "manifest.json").is_file())
                self.assertTrue((output / "preserved-artifact").is_file())
                self.assertIn(
                    "down --volumes --remove-orphans",
                    fixture.docker_log.read_text(encoding="utf-8"),
                )
                self.assertFalse((fixture.root / "runtime").exists())

    def test_term_during_mysql_setup_cleans_topology_without_starting_campaign(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            output = fixture.output("setup-signal")
            environment = dict(fixture.environment)
            environment.update(
                {
                    "FAKE_MANIFEST_PATH": str(fixture.manifests["calibration"]),
                    "FAKE_DOCKER_MODE": "wait-exec",
                }
            )
            process = subprocess.Popen(
                fixture.command("execute", output=output),
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 10
            while not fixture.docker_ready.exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(0.05)
            if not fixture.docker_ready.exists():
                process.kill()
                _, stderr = process.communicate(timeout=5)
                self.fail(f"launcher did not reach setup:\n{stderr}\n{fixture.text_logs()}")
            os.kill(process.pid, signal.SIGTERM)
            _, stderr = process.communicate(timeout=15)
            self.assertEqual(143, process.returncode, stderr)
            self.assertFalse(output.exists())
            self.assertEqual("", fixture.python_log.read_text(encoding="utf-8"))
            self.assertIn(
                "down --volumes --remove-orphans",
                fixture.docker_log.read_text(encoding="utf-8"),
            )
            self.assertFalse((fixture.root / "runtime").exists())

    def test_int_and_term_forward_to_campaign_cleanup_and_preserve_output(self) -> None:
        for signal_value, expected in (
            (signal.SIGINT, 130),
            (signal.SIGTERM, 143),
        ):
            with self.subTest(signal=signal_value), TemporaryDirectory() as temporary:
                fixture = LauncherFixture(Path(temporary))
                output = fixture.output(f"signal-{expected}")
                environment = dict(fixture.environment)
                environment.update(
                    {
                        "FAKE_MANIFEST_PATH": str(fixture.manifests["calibration"]),
                        "FAKE_PYTHON_MODE": "wait",
                    }
                )
                process = subprocess.Popen(
                    fixture.command("execute", output=output),
                    cwd=ROOT,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                deadline = time.monotonic() + 10
                while not fixture.ready.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.05)
                if not fixture.ready.exists():
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                    self.fail(f"launcher did not reach campaign:\n{stderr}\n{fixture.text_logs()}")
                os.kill(process.pid, signal_value)
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(expected, process.returncode, stderr)
                self.assertTrue((output / "manifest.json").is_file())
                self.assertTrue((output / "preserved-artifact").is_file())
                self.assertIn(
                    "down --volumes --remove-orphans",
                    fixture.docker_log.read_text(encoding="utf-8"),
                )
                self.assertFalse((fixture.root / "runtime").exists())
                self.assertNotIn(SECRET, stdout + stderr + fixture.text_logs())
