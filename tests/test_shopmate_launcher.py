from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_shopmate_ownership_ablation.sh"
PROVIDER_SECRET = "provider-sentinel-not-for-launcher-children"


def executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip())
    path.chmod(0o755)


class LauncherFixture:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.city = self.root / "citybuddy"
        self.shop = self.root / "shopmate"
        self.bin = self.root / "bin"
        self.output = self.root / "results"
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        self.bin.mkdir()
        self.log = self.root / "events.jsonl"
        self.log.write_text("")
        self.ready = self.root / "driver-ready"
        self.environment = {
            **os.environ,
            "PATH": f"{self.bin}:{os.defpath}",
            "CITYBUDDY_REPO": str(self.city),
            "SHOPMATE_REPO": str(self.shop),
            "TMPDIR": str(self.tmp),
            "FAKE_ROOT": str(self.root),
            "FAKE_CITY": str(self.city),
            "FAKE_SHOP": str(self.shop),
            "CLIPROXY_API_KEY": PROVIDER_SECRET,
            "CLIPROXY_BASE_URL": "https://provider.invalid",
            "AGENT_MODEL_PROXY_API_KEY": PROVIDER_SECRET,
            "OPENAI_API_KEY": PROVIDER_SECRET,
            "ANTHROPIC_API_KEY": PROVIDER_SECRET,
            "PYTHONPATH": "must-not-reach-topology",
        }
        self.environment.pop("STATEEVAL_MODEL_NAME", None)
        dispatcher = self.bin / "dispatch.py"
        executable(dispatcher, f"#!{sys.executable}\n" + DISPATCHER)
        for command in ("git", "docker", "java", "make", "curl", "openssl"):
            (self.bin / command).symlink_to(dispatcher)
        (self.bin / "python3").symlink_to(sys.executable)
        executable(
            self.city / "mvnw", f'#!/usr/bin/env bash\nexec "{dispatcher}" mvnw "$@"\n'
        )
        executable(
            self.city / "scripts/init_local.sh",
            """
            #!/usr/bin/env bash
            cat > "$ENV_FILE" <<'ENV'
            MYSQL_BOOTSTRAP_PASSWORD=synthetic-root
            MYSQL_AUTH_APP_PASSWORD=synthetic-auth
            MYSQL_COMMERCE_APP_PASSWORD=synthetic-commerce
            ENV
            """,
        )
        executable(
            self.city / "scripts/test_dynamic_ports.sh",
            """
            compose_host_port() { printf -v "$1" '%s' 43306; }
            process_bound_port() {
              local value=''
              for _ in {1..100}; do
                if [[ -f "$4" ]]; then value=$(sed -n 's/.*port //p' "$4"); fi
                if [[ -n "$value" ]]; then printf -v "$1" '%s' "$value"; return 0; fi
                sleep 0.01
              done
              return 1
            }
            """,
        )
        executable(
            self.city / "scripts/service_credential.py",
            """
            import sys
            if sys.argv[1] == 'generate':
                print('cbsvc_v1_' + 'a' * 64, end='')
            else:
                assert sys.argv[1] == 'hash'
                assert sys.argv[2] in ('commerce-service', 'evaluation-client', 'shopping-agent')
                assert sys.stdin.read().startswith('cbsvc_v1_')
                print('sha256$v1$' + 'b' * 64)
            """,
        )
        executable(
            self.shop / ".venv/bin/python",
            f"#!{sys.executable}\n" + DISPATCHER,
        )

    def run(self, *arguments: str, **updates: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), "--output", str(self.output), *arguments],
            env={**self.environment, **updates},
            capture_output=True,
            text=True,
            timeout=20,
        )

    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def runtimes(self) -> list[Path]:
        return list(self.tmp.glob("stateeval-shopmate.*"))


DISPATCHER = r"""
import json, os, signal, sys, time
from pathlib import Path
name = Path(sys.argv[0]).name
if name == 'dispatch.py':
    name = sys.argv.pop(1)
args = sys.argv[1:]
root = Path(os.environ['FAKE_ROOT'])
provider_keys = ('CLIPROXY_API_KEY','CLIPROXY_BASE_URL','AGENT_MODEL_PROXY_API_KEY',
                 'OPENAI_API_KEY','ANTHROPIC_API_KEY')
event = {'tool': name, 'pid': os.getpid(), 'args': args, 'provider_present': any(key in os.environ for key in provider_keys)}
with (root/'events.jsonl').open('a') as log:
    log.write(json.dumps(event)+'\n')
if name == 'git':
    repo = args[args.index('-C')+1]
    if '--show-toplevel' in args:
        print(repo)
    elif 'rev-parse' in args:
        print(('c' if repo == os.environ['FAKE_CITY'] else 'd' if repo == os.environ['FAKE_SHOP'] else 'e') * 40)
    elif os.environ.get('FAKE_DIRTY') == repo:
        print(' M changed-source.py')
elif name == 'docker':
    if 'ps' in args:
        print('isolated-mysql')
    elif 'exec' in args:
        (root/'seed.sql').write_text(sys.stdin.read())
    elif 'down' in args and os.environ.get('FAKE_DOWN_FAIL'):
        raise SystemExit(9)
elif name == 'openssl':
    if 'rand' in args:
        print('a' * (2 * int(args[-1])))
    else:
        Path(args[args.index('-out')+1]).write_text('test-key')
elif name == 'java':
    if any('FaqFixturePublisherCli' in arg for arg in args):
        policy = json.load(sys.stdin)
        (root/'published-input.json').write_text(json.dumps(policy))
        print(json.dumps([{'faqId':policy[0]['faqId'],'publishedVersion':1,'changed':True,'eventId':'test-event'}]))
    else:
        if any('auth-service' in arg for arg in args):
            port = 41001
        else:
            port = 41003 if '--citybuddy.evaluation.action-ownership-binding-enabled=false' in args else 41002
            application = json.loads(os.environ['SPRING_APPLICATION_JSON'])
            assert set(application) == {'citybuddy.evaluation.management-client-secret',
                                       'citybuddy.evaluation.auth-client-secret',
                                       'citybuddy.mock-payment.callback-secret'}
        print('Tomcat started on port '+str(port), flush=True)
        while True: time.sleep(1)
elif name == 'python':
    assert args[:2] == ['-m','stateeval.shopmate']
    runtime_path = Path(args[args.index('--runtime')+1])
    runtime = json.loads(runtime_path.read_text())
    assert runtime_path.stat().st_mode & 0o777 == 0o600
    assert runtime_path.parent.stat().st_mode & 0o777 == 0o700
    assert os.environ['PYTHONPATH'].endswith('/src')
    assert 'SPRING_DATASOURCE_PASSWORD' not in os.environ
    assert 'SPRING_APPLICATION_JSON' not in os.environ
    (root/'runtime-copy.json').write_text(json.dumps(runtime))
    (root/'runtime-path').write_text(str(runtime_path))
    output = Path(args[args.index('--output')+1])
    output.mkdir()
    (output/'result.json').write_text('{"test_artifact":true}')
    mode = os.environ.get('FAKE_DRIVER_MODE','success')
    if mode == 'retain':
        (output/'RETAIN_FIXTURE').write_text('Uncertain test write')
    if mode == 'wait':
        def close_owned_host(signum, frame):
            time.sleep(10.5)
            (root/'driver-closed').touch()
            raise SystemExit(143)
        signal.signal(signal.SIGTERM, close_owned_host)
        (root/'driver-ready').touch()
        while True: time.sleep(1)
    if mode == 'failure': raise SystemExit(7)
"""


class ShopmateLauncherTest(TestCase):
    def test_success_uses_installed_host_exact_scopes_and_private_runtime(self) -> None:
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            result = fixture.run("--stage", "pilot", "--trials", "2")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(fixture.runtimes())
            events = fixture.events()
            self.assertTrue(events)
            self.assertTrue(all(not event["provider_present"] for event in events))
            driver = next(event for event in events if event["tool"] == "python")
            self.assertEqual(["--stage", "pilot", "--trials", "2"], driver["args"][-4:])
            runtime = json.loads((fixture.root / "runtime-copy.json").read_text())
            self.assertEqual("gpt-5.6-terra", runtime["model_name"])
            for field, letter in (
                ("citybuddy_commit", "c"),
                ("shopmate_commit", "d"),
                ("stateeval_commit", "e"),
            ):
                self.assertEqual(letter * 40, runtime[field])
            self.assertEqual("stateeval_grader", runtime["mysql_user"])
            self.assertIsInstance(runtime["ownership_off_pid"], int)
            for key in (
                "management_password",
                "evaluation_client_password",
                "shopping_service_secret",
                "mysql_password",
                "mock_payment_secret",
            ):
                self.assertNotIn(
                    runtime[key], (fixture.output / "result.json").read_text()
                )
                self.assertNotIn(runtime[key], result.stdout + result.stderr)
            self.assertNotIn(
                PROVIDER_SECRET, result.stdout + result.stderr + fixture.log.read_text()
            )
            auth = next(
                event
                for event in events
                if event["tool"] == "java"
                and any("auth-service" in arg for arg in event["args"])
            )
            scopes = [
                arg.split("=", 1)[1]
                for arg in auth["args"]
                if "exchange-scopes[" in arg
            ]
            self.assertEqual(
                [
                    "shopping:orders:read",
                    "shopping:profile:read",
                    "shopping:cart:read",
                    "refund:create",
                ],
                scopes,
            )
            sql = (fixture.root / "seed.sql").read_text()
            self.assertIn("'shopping-agent'", sql)
            self.assertIn("sha256$v1$", sql)
            self.assertNotIn("shopping:cart:write", sql)
            self.assertNotIn("cs_db", sql)
            java = [
                event
                for event in events
                if event["tool"] == "java" and "-jar" in event["args"]
            ]
            self.assertEqual(3, len(java))
            for event in java:
                with self.assertRaises(ProcessLookupError):
                    os.kill(event["pid"], 0)
            policy = json.loads((fixture.root / "published-input.json").read_text())
            self.assertEqual("retail-policy-refunds", policy[0]["faqId"])
            self.assertTrue(
                any(
                    "down" in event["args"] and "--volumes" in event["args"]
                    for event in events
                )
            )

    def test_failure_and_unknown_write_preserve_database_runtime_and_artifacts(
        self,
    ) -> None:
        for mode, expected in (("failure", 7), ("retain", 1)):
            with self.subTest(mode=mode), TemporaryDirectory() as temporary:
                fixture = LauncherFixture(Path(temporary))
                result = fixture.run(FAKE_DRIVER_MODE=mode)
                self.assertEqual(expected, result.returncode, result.stderr)
                self.assertEqual(1, len(fixture.runtimes()))
                self.assertTrue((fixture.runtimes()[0] / "runtime.json").exists())
                self.assertTrue((fixture.output / "result.json").exists())
                commands = [
                    event["args"]
                    for event in fixture.events()
                    if event["tool"] == "docker"
                ]
                self.assertTrue(any("stop" in args for args in commands))
                self.assertFalse(any("down" in args for args in commands))
                self.assertNotIn(PROVIDER_SECRET, result.stdout + result.stderr)

    def test_failed_volume_cleanup_stops_project_and_retains_private_logs(self) -> None:
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            result = fixture.run(FAKE_DOWN_FAIL="1")
            self.assertEqual(1, result.returncode, result.stderr)
            self.assertTrue(fixture.runtimes())
            commands = [
                event["args"] for event in fixture.events() if event["tool"] == "docker"
            ]
            self.assertTrue(any("down" in args for args in commands))
            self.assertTrue(any("stop" in args for args in commands))

    def test_dirty_repositories_or_scope_override_do_not_start_topology(self) -> None:
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            for repo in (fixture.city, fixture.shop, ROOT):
                result = fixture.run(FAKE_DIRTY=str(repo))
                self.assertNotEqual(0, result.returncode)
            result = fixture.run("--scopes", "shopping:cart:write")
            self.assertEqual(2, result.returncode)
            self.assertFalse(
                any(event["tool"] == "docker" for event in fixture.events())
            )
            self.assertFalse(fixture.output.exists())
            self.assertFalse(fixture.runtimes())

    def test_term_stops_owned_processes_without_deleting_unknown_fixture(self) -> None:
        with TemporaryDirectory() as temporary:
            fixture = LauncherFixture(Path(temporary))
            process = subprocess.Popen(
                ["bash", str(SCRIPT), "--output", str(fixture.output)],
                env={**fixture.environment, "FAKE_DRIVER_MODE": "wait"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 10
                while not fixture.ready.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(fixture.ready.exists())
                process.send_signal(signal.SIGTERM)
                _, stderr = process.communicate(timeout=15)
                self.assertEqual(143, process.returncode, stderr)
                self.assertTrue((fixture.root / "driver-closed").exists())
                self.assertTrue(fixture.runtimes())
                self.assertFalse(
                    any("down" in event["args"] for event in fixture.events())
                )
                for event in fixture.events():
                    if event["tool"] == "python" or (
                        event["tool"] == "java" and "-jar" in event["args"]
                    ):
                        with self.assertRaises(ProcessLookupError):
                            os.kill(event["pid"], 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
