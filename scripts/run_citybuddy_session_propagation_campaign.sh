#!/usr/bin/env bash
set -euo pipefail

expected_citybuddy_commit="09130fa3c0209648f98781ff0892c3d07a55e59f"
minimum_stateeval_commit="52b16f5017319505e970ce96b24132b2677d14e8"
expected_proxy_attestation="CLIProxyAPI/7.2.76/9f62c8df28dc749ea976865450a458917bf45042/ad8d0e9d43888c794f32d9a36842c395f641038a1a622f650c7868dc6a359f0d"
stateeval_phase="calibration"
stateeval_seed=2026083103
stateeval_blocks=10
stateeval_slots=100

usage() {
  echo "usage: $0 {preflight|execute|resume} --output ABSOLUTE_PATH" >&2
}

fail_usage() {
  echo "$1" >&2
  usage
  exit 2
}

fail() {
  echo "$1" >&2
  exit 2
}

if (($# == 0)); then
  fail_usage "A command and output path are required."
fi

stateeval_command="$1"
shift
case "$stateeval_command" in
  preflight | execute | resume) ;;
  *) fail_usage "Unknown command: $stateeval_command" ;;
esac

stateeval_output=""
while (($# > 0)); do
  case "$1" in
    --output)
      (($# >= 2)) || fail_usage "--output requires a value."
      [[ -z "$stateeval_output" ]] || fail_usage "--output may be specified only once."
      stateeval_output="$2"
      shift 2
      ;;
    *) fail_usage "Unknown argument: $1" ;;
  esac
done
[[ -n "$stateeval_output" ]] || fail_usage "--output is required."

# Provider credentials are captured before any child starts and are injected
# only into the two measured agents.
for stateeval_required_name in \
  AGENT_MODEL_PROXY_URL AGENT_MODEL_PROXY_API_KEY \
  STATEEVAL_MODEL_TEMPERATURE STATEEVAL_MODEL_TIMEOUT_SECONDS CITYBUDDY_REPO; do
  [[ -n "${!stateeval_required_name:-}" ]] \
    || fail "$stateeval_required_name is required for this campaign."
done
case "$AGENT_MODEL_PROXY_URL" in
  http://[!/:]* | https://[!/:]*) ;;
  *) fail "AGENT_MODEL_PROXY_URL must include an HTTP(S) network authority." ;;
esac
[[ "$AGENT_MODEL_PROXY_URL" != *[[:space:]]* ]] \
  || fail "AGENT_MODEL_PROXY_URL must not contain whitespace."
[[ "${STATEEVAL_MODEL_NAME:-}" == "gpt-5.4" ]] \
  || fail "STATEEVAL_MODEL_NAME must be gpt-5.4 for this fixed campaign."
[[ "${STATEEVAL_PROXY_ATTESTATION:-}" == "$expected_proxy_attestation" ]] \
  || fail "STATEEVAL_PROXY_ATTESTATION must identify the inspected proxy deployment."

stateeval_real_model_proxy_url="${AGENT_MODEL_PROXY_URL%/}"
stateeval_real_model_proxy_api_key="$AGENT_MODEL_PROXY_API_KEY"
stateeval_model_name="$STATEEVAL_MODEL_NAME"
stateeval_model_temperature="$STATEEVAL_MODEL_TEMPERATURE"
stateeval_model_timeout_seconds="$STATEEVAL_MODEL_TIMEOUT_SECONDS"
export -n expected_proxy_attestation stateeval_real_model_proxy_url \
  stateeval_real_model_proxy_api_key stateeval_model_name \
  stateeval_model_temperature stateeval_model_timeout_seconds

unset AGENT_MODEL_PROXY_URL AGENT_MODEL_PROXY_API_KEY
unset AGENT_MODEL_TEMPERATURE AGENT_MODEL_TIMEOUT_SECONDS AGENT_WORKERS
unset AGENT_HTTP_CLIENT_LAYOUT AGENT_EVALUATION_SESSION_PROPAGATION_ENABLED
unset CITYBUDDY_METRICS_ENABLED CITYBUDDY_TRACE_EXPORT_URL CLIPROXY_BASE_URL
unset CLIPROXY_API_KEY STATEEVAL_PROXY_ATTESTATION
unset PYTHONPATH MYSQL_PWD COMPOSE_PROJECT_NAME
export GIT_OPTIONAL_LOCKS=0

stateeval_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

required_commands=(curl docker git java make mktemp openssl ps python3 uv)
for stateeval_required_command in "${required_commands[@]}"; do
  command -v "$stateeval_required_command" >/dev/null 2>&1 \
    || fail "Required command is unavailable: $stateeval_required_command"
done

python3 -I -B -c '
import math
import sys

try:
    temperature = float(sys.argv[1])
    timeout = float(sys.argv[2])
except ValueError:
    raise SystemExit(1)
if not math.isfinite(temperature) or not 0 <= temperature <= 2:
    raise SystemExit(1)
if not math.isfinite(timeout) or timeout <= 0:
    raise SystemExit(1)
' "$stateeval_model_temperature" "$stateeval_model_timeout_seconds" \
  || fail "Model temperature or timeout is outside the fixed runtime contract."

[[ "$CITYBUDDY_REPO" == /* && -d "$CITYBUDDY_REPO" ]] \
  || fail "CITYBUDDY_REPO must be an existing absolute canonical directory."
citybuddy_root="$(cd "$CITYBUDDY_REPO" && pwd -P)"
[[ "$citybuddy_root" == "$CITYBUDDY_REPO" ]] \
  || fail "CITYBUDDY_REPO must be an absolute canonical path."

[[ "$stateeval_output" == /* ]] || fail "--output must be an absolute canonical path."
stateeval_output_parent_input="$(dirname "$stateeval_output")"
stateeval_output_name="$(basename "$stateeval_output")"
[[ -d "$stateeval_output_parent_input" && "$stateeval_output_name" != "." \
  && "$stateeval_output_name" != "/" ]] \
  || fail "Campaign output parent must already exist."
stateeval_output_parent="$(cd "$stateeval_output_parent_input" && pwd -P)"
[[ "$stateeval_output_parent/$stateeval_output_name" == "$stateeval_output" ]] \
  || fail "--output must be an absolute canonical path."
[[ ! -L "$stateeval_output" && ( ! -e "$stateeval_output" || -d "$stateeval_output" ) ]] \
  || fail "Campaign output must not be a symlink or non-directory."
case "$stateeval_output/" in
  "$citybuddy_root/" | \
  "$citybuddy_root/"*)
    fail "Campaign output must be outside the frozen CityBuddy worktree."
    ;;
  "$stateeval_root/src/"* | \
  "$stateeval_root/scripts/"* | \
  "$stateeval_root/tests/"* | \
  "$stateeval_root/results/milestone-1/"* | \
  "$stateeval_root/results/milestone-2/"* | \
  "$stateeval_root/results/ownership-campaign-v1/"*)
    fail "Campaign output must not overlap source or frozen milestone paths."
    ;;
esac
case "/$stateeval_output/" in
  */stateeval-m2/* | */stateeval-m2-*/* | */milestone-1/* | */milestone-2/*)
    fail "Campaign output must not use a frozen milestone namespace."
    ;;
esac

validate_manifest_header() {
  python3 -I -B -c '
import json
import sys

with open(sys.argv[1], "rb") as stream:
    value = json.load(stream)
if not isinstance(value, dict):
    raise SystemExit(1)
if value.get("schema") != "stateeval.citybuddy-session-propagation-campaign/v1":
    raise SystemExit(1)
if value.get("campaign") != "citybuddy-session-propagation":
    raise SystemExit(1)
if value.get("phase") != "calibration":
    raise SystemExit(1)
' "$stateeval_output/manifest.json" >/dev/null 2>&1
}

resume_output_is_valid() {
  [[ -d "$stateeval_output" && ! -L "$stateeval_output" \
    && -f "$stateeval_output/manifest.json" ]] && validate_manifest_header
}

case "$stateeval_command" in
  execute)
    [[ ! -e "$stateeval_output" ]] || fail "execute requires a fresh output path."
    stateeval_output_mode="fresh"
    ;;
  resume)
    resume_output_is_valid \
      || fail "resume requires a valid existing session-propagation calibration manifest."
    stateeval_output_mode="resume"
    ;;
  preflight)
    if [[ -e "$stateeval_output" ]]; then
      resume_output_is_valid \
        || fail "Existing preflight output is not a resumable session-propagation calibration."
      stateeval_output_mode="resume"
    else
      stateeval_output_mode="fresh"
    fi
    ;;
esac

check_full_sha() {
  local label="$1"
  local value="$2"
  ((${#value} == 40)) && [[ "$value" != *[!0-9a-f]* ]] \
    || fail "$label did not resolve to a full lowercase Git SHA."
}

check_stateeval_repository() {
  [[ "$(git -C "$stateeval_root" rev-parse --show-toplevel)" == "$stateeval_root" ]] \
    || fail "StateEval launcher is not running from its repository root."
  actual_stateeval_commit="$(git -C "$stateeval_root" rev-parse HEAD)"
  check_full_sha "StateEval HEAD" "$actual_stateeval_commit"
  git -C "$stateeval_root" merge-base --is-ancestor \
    "$minimum_stateeval_commit" "$actual_stateeval_commit" \
    || fail "StateEval HEAD does not contain the frozen campaign base."
  git -C "$stateeval_root" cat-file -e \
    "$actual_stateeval_commit:scripts/run_citybuddy_session_propagation_campaign.sh" \
    2>/dev/null \
    || fail "StateEval HEAD does not contain the session-propagation launcher."
  [[ -z "$(git -C "$stateeval_root" status --porcelain=v1 --untracked-files=no)" ]] \
    || fail "StateEval tracked worktree or index is dirty."
}

check_citybuddy_repository() {
  local git_dir
  local common_dir

  [[ "$(git -C "$citybuddy_root" rev-parse --show-toplevel)" == "$citybuddy_root" ]] \
    || fail "CITYBUDDY_REPO must point at the CityBuddy worktree root."
  git_dir="$(git -C "$citybuddy_root" rev-parse --path-format=absolute --git-dir)"
  common_dir="$(git -C "$citybuddy_root" rev-parse --path-format=absolute --git-common-dir)"
  [[ "$git_dir" != "$common_dir" ]] \
    || fail "CITYBUDDY_REPO must be an independent linked worktree."
  actual_citybuddy_commit="$(git -C "$citybuddy_root" rev-parse HEAD)"
  check_full_sha "CityBuddy HEAD" "$actual_citybuddy_commit"
  [[ "$actual_citybuddy_commit" == "$expected_citybuddy_commit" ]] \
    || fail "CityBuddy must be at $expected_citybuddy_commit; found $actual_citybuddy_commit."
  [[ -z "$(git -C "$citybuddy_root" status --porcelain=v1 --untracked-files=no)" ]] \
    || fail "CityBuddy worktree or index is dirty."
}

check_citybuddy_files() {
  local required_file
  for required_file in \
    "$citybuddy_root/compose.yaml" \
    "$citybuddy_root/scripts/fake_litellm_server.py" \
    "$citybuddy_root/scripts/hash_test_credential.py" \
    "$citybuddy_root/scripts/test_dynamic_ports.sh"; do
    [[ -f "$required_file" ]] || fail "Frozen CityBuddy worktree is missing: $required_file"
  done
  for required_file in \
    "$citybuddy_root/mvnw" \
    "$citybuddy_root/scripts/init_local.sh"; do
    [[ -x "$required_file" ]] \
      || fail "Frozen CityBuddy worktree lacks an executable: $required_file"
  done
}

check_repositories() {
  check_stateeval_repository
  check_citybuddy_repository
  check_citybuddy_files
}

check_repositories

echo "session_propagation_calibration_preflight"
echo "phase=$stateeval_phase"
echo "output=$stateeval_output"
echo "output_mode=$stateeval_output_mode"
echo "seed=$stateeval_seed"
echo "blocks=$stateeval_blocks"
echo "slots=$stateeval_slots"
echo "citybuddy_commit=$actual_citybuddy_commit"
echo "stateeval_commit=$actual_stateeval_commit"
echo "model_alias=$stateeval_model_name"
echo "model_identity_basis=proxy-exposed-alias-not-upstream-snapshot"
echo "model_temperature=$stateeval_model_temperature"
echo "model_timeout_seconds=$stateeval_model_timeout_seconds"
echo "proxy_identity=$expected_proxy_attestation"
echo "agent_workers=1"
echo "agent_http_client_layout=shared"
echo "agent_attempt_budget=16"
echo "ownership_binding_enabled=false"
echo "session_propagation_on_enabled=true"
echo "session_propagation_off_enabled=false"
echo "trace_export_url=<empty>"
echo "metrics_enabled=false"

if [[ "$stateeval_command" == "preflight" ]]; then
  exit 0
fi

# Execute and resume alone may source helpers, allocate runtime state, or touch
# Docker and network IO.
# shellcheck source=/dev/null
source "$citybuddy_root/scripts/test_dynamic_ports.sh"

stateeval_runtime_dir=""
stateeval_env_file=""
stateeval_project=""
stateeval_auth_pid=""
stateeval_commerce_off_pid=""
stateeval_fixture_model_pid=""
stateeval_control_agent_pid=""
stateeval_agent_on_pid=""
stateeval_agent_off_pid=""
stateeval_campaign_pid=""
stateeval_foreground_pid=""
stateeval_compose_touched=false
stateeval_mysql_port=""
stateeval_auth_port=""
stateeval_commerce_off_port=""
stateeval_fixture_model_port=""
stateeval_control_agent_port=""
stateeval_agent_on_port=""
stateeval_agent_off_port=""
stateeval_ownership_off_launch_id=""
stateeval_session_on_launch_id=""
stateeval_session_off_launch_id=""
compose=()

# shellcheck disable=SC2329
stateeval_pids() {
  printf '%s\n' \
    "$stateeval_foreground_pid" \
    "$stateeval_campaign_pid" \
    "$stateeval_agent_off_pid" \
    "$stateeval_agent_on_pid" \
    "$stateeval_control_agent_pid" \
    "$stateeval_fixture_model_pid" \
    "$stateeval_commerce_off_pid" \
    "$stateeval_auth_pid"
}

# shellcheck disable=SC2329
cleanup() {
  local status=$?
  local resource_status=0
  local pid
  local any_alive
  local process_state
  local _
  trap - EXIT
  trap '' HUP INT TERM

  while IFS= read -r pid; do
    if [[ -n "$pid" ]]; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done <<EOF
$(stateeval_pids)
EOF

  for _ in {1..50}; do
    any_alive=false
    while IFS= read -r pid; do
      if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
        process_state="$(ps -o stat= -p "$pid" 2>/dev/null || true)"
        if [[ "$process_state" != *Z* ]]; then
          any_alive=true
        fi
      fi
    done <<EOF
$(stateeval_pids)
EOF
    [[ "$any_alive" == false ]] && break
    sleep 0.1
  done

  while IFS= read -r pid; do
    if [[ -n "$pid" ]]; then
      if kill -0 "$pid" >/dev/null 2>&1; then
        kill -KILL "$pid" >/dev/null 2>&1 || true
      fi
      wait "$pid" >/dev/null 2>&1 || true
    fi
  done <<EOF
$(stateeval_pids)
EOF

  if [[ "$stateeval_compose_touched" == true ]]; then
    (
      cd "$citybuddy_root"
      "${compose[@]}" down --volumes --remove-orphans
    ) >/dev/null 2>&1 || resource_status=$?
  fi
  if [[ -n "$stateeval_runtime_dir" && -d "$stateeval_runtime_dir" ]]; then
    if ! rm -rf -- "$stateeval_runtime_dir"; then
      [[ "$resource_status" -ne 0 ]] || resource_status=1
    fi
  fi
  finish_test_cleanup "$status" "$resource_status"
}

# shellcheck disable=SC2329
handle_signal() {
  local signal_name="$1"
  local status="$2"
  if [[ -n "$stateeval_foreground_pid" ]]; then
    kill -s "$signal_name" "$stateeval_foreground_pid" >/dev/null 2>&1 || true
  fi
  if [[ -n "$stateeval_campaign_pid" ]]; then
    kill -s "$signal_name" "$stateeval_campaign_pid" >/dev/null 2>&1 || true
  fi
  exit "$status"
}

trap cleanup EXIT
trap 'handle_signal HUP 129' HUP
trap 'handle_signal INT 130' INT
trap 'handle_signal TERM 143' TERM

wait_tracked() {
  local status=0
  stateeval_foreground_pid="$1"
  wait "$stateeval_foreground_pid" || status=$?
  stateeval_foreground_pid=""
  return "$status"
}

run_citybuddy_tracked() {
  (
    cd "$citybuddy_root"
    exec "$@"
  ) &
  wait_tracked "$!"
}

stateeval_runtime_dir="$(mktemp -d)"
stateeval_env_file="$stateeval_runtime_dir/.env"
stateeval_project="stateeval-session-propagation-calibration-$(openssl rand -hex 8)"
stateeval_ownership_off_launch_id="stateeval-session-propagation-calibration-commerce-$(openssl rand -hex 12)"
stateeval_session_on_launch_id="stateeval-session-propagation-calibration-on-$(openssl rand -hex 12)"
stateeval_session_off_launch_id="stateeval-session-propagation-calibration-off-$(openssl rand -hex 12)"
compose=(
  docker compose
  --project-name "$stateeval_project"
  --env-file "$stateeval_env_file"
  --file "$citybuddy_root/compose.yaml"
)

read_env_value() {
  sed -n "s/^$1=//p" "$stateeval_env_file"
}

mysql_root() {
  local database="$1"
  local statement="$2"
  local args=(
    mysql
    --protocol=tcp
    --host=127.0.0.1
    --port=3306
    --user=root
    --batch
    --skip-column-names
  )
  if [[ -n "$database" ]]; then
    args+=(--database="$database")
  fi
  (
    cd "$citybuddy_root"
    export MYSQL_PWD="$stateeval_mysql_root_password"
    exec "${compose[@]}" exec -T -e MYSQL_PWD \
      mysql "${args[@]}" --execute="$statement"
  ) &
  wait_tracked "$!"
}

wait_http() {
  local url="$1"
  local pid="$2"
  local log="$3"
  local _
  for _ in {1..90}; do
    if curl --silent --output /dev/null "$url" 2>/dev/null; then
      return 0
    fi
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      tail -n 120 "$log" >&2 || true
      echo "Process exited while waiting for $url." >&2
      return 1
    fi
    sleep 1
  done
  tail -n 120 "$log" >&2 || true
  echo "Timed out waiting for $url." >&2
  return 1
}

start_auth() {
  local log_offset
  port_log_offset log_offset "$stateeval_runtime_dir/auth.log"
  SPRING_DATASOURCE_PASSWORD="$stateeval_auth_app_password" \
    java -jar "$citybuddy_root/auth-service/target/auth-service-0.0.1-SNAPSHOT.jar" \
    --server.port=0 \
    --spring.profiles.active=evaluation \
    --spring.datasource.url="jdbc:mysql://127.0.0.1:$stateeval_mysql_port/commerce_db?useSSL=false&allowPublicKeyRetrieval=true" \
    --spring.datasource.username=auth_app \
    --citybuddy.identity.enabled=true \
    --citybuddy.identity.issuer=https://identity.citybuddy.test \
    --citybuddy.identity.user-audience=citybuddy-web \
    --citybuddy.identity.current-kid=stateeval-current \
    --citybuddy.identity.current-private-key-path="$stateeval_runtime_dir/current-private.pem" \
    --citybuddy.identity.current-public-key-path="$stateeval_runtime_dir/current-public.pem" \
    '--citybuddy.identity.exchange-scopes[0]=catalog:read' \
    '--citybuddy.identity.exchange-scopes[1]=refund:create' \
    >>"$stateeval_runtime_dir/auth.log" 2>&1 &
  stateeval_auth_pid=$!
  process_bound_port \
    stateeval_auth_port spring "$stateeval_auth_pid" "$stateeval_runtime_dir/auth.log" "$log_offset"
  wait_http \
    "http://127.0.0.1:$stateeval_auth_port/auth/jwks" \
    "$stateeval_auth_pid" \
    "$stateeval_runtime_dir/auth.log"
}

start_commerce() {
  local log="$stateeval_runtime_dir/commerce-off.log"
  local log_offset
  port_log_offset log_offset "$log"
  SPRING_DATASOURCE_PASSWORD="$stateeval_commerce_app_password" \
    java -jar "$citybuddy_root/commerce-service/target/commerce-service-0.0.1-SNAPSHOT.jar" \
    --server.port=0 \
    --spring.profiles.active=evaluation \
    --spring.datasource.url="jdbc:mysql://127.0.0.1:$stateeval_mysql_port/commerce_db?useSSL=false&allowPublicKeyRetrieval=true" \
    --spring.datasource.username=commerce_app \
    --spring.datasource.hikari.connection-timeout=2000 \
    --citybuddy.obo.enabled=true \
    --citybuddy.obo.issuer=https://identity.citybuddy.test \
    --citybuddy.obo.jwks-url="http://127.0.0.1:$stateeval_auth_port/auth/jwks" \
    --citybuddy.obo.jwks-cache-ttl=1s \
    --citybuddy.agent-tools.enabled=true \
    --citybuddy.evaluation.management-client-id=evaluation-manager \
    --citybuddy.evaluation.management-client-secret="$stateeval_management_password" \
    --citybuddy.evaluation.auth-base-url="http://127.0.0.1:$stateeval_auth_port" \
    --citybuddy.evaluation.auth-client-id=commerce-service \
    --citybuddy.evaluation.auth-client-secret="$stateeval_commerce_service_password" \
    --citybuddy.evaluation.identity-issuer=https://identity.citybuddy.test \
    --citybuddy.evaluation.user-audience=citybuddy-web \
    --citybuddy.evaluation.jwks-url="http://127.0.0.1:$stateeval_auth_port/auth/jwks" \
    --citybuddy.evaluation.jwks-cache-ttl=1s \
    --citybuddy.evaluation.provisioning-timeout=10s \
    --citybuddy.evaluation.auth-expiry-safety=2s \
    --citybuddy.evaluation.cleanup-retry=1s \
    --citybuddy.evaluation.janitor-interval=5s \
    --citybuddy.evaluation.max-cleanup-attempts=5 \
    --citybuddy.evaluation.janitor-batch-size=4 \
    --citybuddy.evaluation.build-id=stateeval-session-propagation-calibration \
    --citybuddy.evaluation.schema-compatibility=commerce-evaluation-v1 \
    --citybuddy.evaluation.action-ownership-binding-enabled=false \
    --citybuddy.mock-payment.enabled=true \
    --citybuddy.mock-payment.required-permission=support:chat \
    --citybuddy.mock-payment.callback-key-id="$stateeval_mock_payment_key" \
    --citybuddy.mock-payment.callback-secret="$stateeval_mock_payment_secret" \
    --citybuddy.mock-payment.callback-maximum-age=5m \
    --citybuddy.mock-payment.callback-clock-skew=30s \
    --citybuddy.refund.enabled=true \
    --citybuddy.refund.required-permission=refund:create \
    --citybuddy.refund.lock-wait-timeout-seconds=1 \
    --citybuddy.refund.maximum-observation-attempts=2 \
    --citybuddy.refund.observation-backoff=25ms \
    --citybuddy.actions.enabled=true \
    --citybuddy.actions.required-scope=refund:create \
    --citybuddy.actions.pending-ttl=15m \
    --citybuddy.actions.lock-wait-timeout-seconds=1 \
    --citybuddy.actions.maximum-observation-attempts=2 \
    --citybuddy.actions.observation-backoff=25ms \
    >>"$log" 2>&1 &
  stateeval_commerce_off_pid=$!
  process_bound_port \
    stateeval_commerce_off_port spring "$stateeval_commerce_off_pid" "$log" "$log_offset"
  wait_http \
    "http://127.0.0.1:$stateeval_commerce_off_port/api/products" \
    "$stateeval_commerce_off_pid" \
    "$log"
}

start_model_fixture() {
  local log_offset
  port_log_offset log_offset "$stateeval_runtime_dir/control-model.log"
  (
    cd "$citybuddy_root"
    exec uv run python scripts/fake_litellm_server.py --port 0
  ) >>"$stateeval_runtime_dir/control-model.log" 2>&1 &
  stateeval_fixture_model_pid=$!
  process_bound_port \
    stateeval_fixture_model_port uvicorn "$stateeval_fixture_model_pid" \
    "$stateeval_runtime_dir/control-model.log" "$log_offset"
  wait_http \
    "http://127.0.0.1:$stateeval_fixture_model_port/fixture/counts" \
    "$stateeval_fixture_model_pid" \
    "$stateeval_runtime_dir/control-model.log"
}

start_agent() {
  local label="$1"
  local session_propagation_enabled="$2"
  local model_proxy_url="$3"
  local model_name="$4"
  local model_proxy_api_key="$5"
  local model_temperature="$6"
  local model_timeout_seconds="$7"
  local pid_variable="$8"
  local port_variable="$9"
  case "$session_propagation_enabled" in
    true | false) ;;
    *)
      echo "Invalid session-propagation value: $session_propagation_enabled" >&2
      return 2
      ;;
  esac
  export -n \
    model_proxy_url \
    model_proxy_api_key \
    model_temperature \
    model_timeout_seconds
  local log="$stateeval_runtime_dir/agent-$label.log"
  local log_offset
  port_log_offset log_offset "$log"
  (
    cd "$citybuddy_root"
    export \
      AGENT_PORT=0 \
      AGENT_IDENTITY_ENABLED=true \
      AGENT_EVALUATION_ENABLED=true \
      AGENT_EVALUATION_SESSION_PROPAGATION_ENABLED="$session_propagation_enabled" \
      AGENT_EVALUATION_CLIENT_ID=evaluation-manager \
      AGENT_EVALUATION_CLIENT_SECRET="$stateeval_management_password" \
      AGENT_WORKERS=1 \
      AGENT_HTTP_CLIENT_LAYOUT=shared \
      CITYBUDDY_METRICS_ENABLED=false \
      CITYBUDDY_TRACE_EXPORT_URL='' \
      CITYBUDDY_ENVIRONMENT=integration \
      IDENTITY_ISSUER=https://identity.citybuddy.test \
      IDENTITY_USER_AUDIENCE=citybuddy-web \
      IDENTITY_JWKS_URL="http://127.0.0.1:$stateeval_auth_port/auth/jwks" \
      IDENTITY_EXCHANGE_URL="http://127.0.0.1:$stateeval_auth_port/auth/token/exchange" \
      MYSQL_HOST=127.0.0.1 \
      MYSQL_PORT="$stateeval_mysql_port" \
      MYSQL_AGENT_APP_PASSWORD="$stateeval_agent_app_password" \
      AGENT_SERVICE_CLIENT_ID=agent-service \
      AGENT_SERVICE_CLIENT_SECRET="$stateeval_agent_service_password" \
      AGENT_EXCHANGE_SCOPES='catalog:read refund:create' \
      AGENT_ATTEMPT_BUDGET=16 \
      AGENT_MODEL_PROXY_URL="$model_proxy_url" \
      AGENT_MODEL_PROXY_API_KEY="$model_proxy_api_key" \
      AGENT_MODEL_TEMPERATURE="$model_temperature" \
      AGENT_MODEL_TIMEOUT_SECONDS="$model_timeout_seconds" \
      AGENT_PRIMARY_ROLE_ALIAS="$model_name" \
      AGENT_FALLBACK_ROLE_ALIAS="$model_name" \
      AGENT_COMMERCE_TOOLS_URL="http://127.0.0.1:$stateeval_commerce_off_port" \
      AGENT_COMMERCE_LIVENESS_URL="http://127.0.0.1:$stateeval_commerce_off_port"
    exec uv run citybuddy-agent
  ) >>"$log" 2>&1 &
  local pid=$!
  printf -v "$pid_variable" '%s' "$pid"
  process_bound_port \
    "$port_variable" uvicorn "$pid" "$log" "$log_offset"
  local port="${!port_variable}"
  wait_http \
    "http://127.0.0.1:$port/api/sessions" \
    "$pid" \
    "$log"
}

assert_runtime_isolated_and_alive() {
  local status=0
  if [[ -z "$stateeval_commerce_off_pid" ]] \
    || ! kill -0 "$stateeval_commerce_off_pid" >/dev/null 2>&1; then
    tail -n 120 "$stateeval_runtime_dir/commerce-off.log" >&2 || true
    echo "Ownership-binding-off commerce process did not remain continuous." >&2
    status=1
  fi
  if [[ -z "$stateeval_agent_on_pid" || -z "$stateeval_agent_off_pid" \
    || "$stateeval_agent_on_pid" == "$stateeval_agent_off_pid" \
    || -z "$stateeval_agent_on_port" || -z "$stateeval_agent_off_port" \
    || "$stateeval_agent_on_port" == "$stateeval_agent_off_port" \
    || -z "$stateeval_session_on_launch_id" || -z "$stateeval_session_off_launch_id" \
    || "$stateeval_session_on_launch_id" == "$stateeval_session_off_launch_id" ]]; then
    echo "Measured session-propagation agents are not independently identified." >&2
    status=1
  fi
  if [[ -z "$stateeval_agent_on_pid" ]] \
    || ! kill -0 "$stateeval_agent_on_pid" >/dev/null 2>&1; then
    tail -n 120 "$stateeval_runtime_dir/agent-on.log" >&2 || true
    echo "Session-propagation-on agent process did not remain continuous." >&2
    status=1
  fi
  if [[ -z "$stateeval_agent_off_pid" ]] \
    || ! kill -0 "$stateeval_agent_off_pid" >/dev/null 2>&1; then
    tail -n 120 "$stateeval_runtime_dir/agent-off.log" >&2 || true
    echo "Session-propagation-off agent process did not remain continuous." >&2
    status=1
  fi
  return "$status"
}

run_citybuddy_tracked env ENV_FILE="$stateeval_env_file" ./scripts/init_local.sh
stateeval_compose_touched=true
run_citybuddy_tracked "${compose[@]}" up --detach --wait --wait-timeout 60 mysql
compose_host_port stateeval_mysql_port mysql 3306
stateeval_mysql_container="$("${compose[@]}" ps --quiet mysql)"
if [[ -z "$stateeval_mysql_container" ]]; then
  echo "Could not resolve the evaluation MySQL container." >&2
  exit 1
fi

stateeval_mysql_root_password="$(read_env_value MYSQL_BOOTSTRAP_PASSWORD)"
stateeval_auth_app_password="$(read_env_value MYSQL_AUTH_APP_PASSWORD)"
stateeval_commerce_app_password="$(read_env_value MYSQL_COMMERCE_APP_PASSWORD)"
stateeval_agent_app_password="$(read_env_value MYSQL_AGENT_APP_PASSWORD)"

run_citybuddy_tracked make \
  ENV_FILE="$stateeval_env_file" COMPOSE_PROJECT_NAME="$stateeval_project" grant-access
run_citybuddy_tracked make \
  ENV_FILE="$stateeval_env_file" COMPOSE_PROJECT_NAME="$stateeval_project" \
  migrate-auth migrate-commerce migrate-agent
run_citybuddy_tracked make \
  ENV_FILE="$stateeval_env_file" COMPOSE_PROJECT_NAME="$stateeval_project" grant-access
run_citybuddy_tracked ./mvnw \
  -q -pl auth-service,commerce-service -am -DskipTests clean package

stateeval_commerce_service_password="$(openssl rand -hex 24)"
stateeval_evaluation_client_password="$(openssl rand -hex 24)"
stateeval_agent_service_password="$(openssl rand -hex 24)"
stateeval_management_password="$(openssl rand -hex 24)"
stateeval_grader_password="$(openssl rand -hex 24)"
stateeval_mock_payment_key="stateeval-session-propagation-$(openssl rand -hex 12)"
stateeval_mock_payment_secret="$(openssl rand -hex 32)"

stateeval_commerce_service_hash="$(
  cd "$citybuddy_root"
  uv run python scripts/hash_test_credential.py "$stateeval_commerce_service_password"
)"
stateeval_evaluation_client_hash="$(
  cd "$citybuddy_root"
  uv run python scripts/hash_test_credential.py "$stateeval_evaluation_client_password"
)"
stateeval_agent_service_hash="$(
  cd "$citybuddy_root"
  uv run python scripts/hash_test_credential.py "$stateeval_agent_service_password"
)"

openssl genpkey \
  -algorithm RSA \
  -pkeyopt rsa_keygen_bits:2048 \
  -out "$stateeval_runtime_dir/current-private.pem" \
  2>/dev/null
openssl pkey \
  -in "$stateeval_runtime_dir/current-private.pem" \
  -pubout \
  -out "$stateeval_runtime_dir/current-public.pem" \
  2>/dev/null

mysql_root commerce_db "
DELETE FROM auth_signing_key_metadata;
DELETE FROM auth_service_identity
WHERE client_id IN ('commerce-service', 'evaluation-client', 'agent-service');
INSERT INTO auth_service_identity
  (service_id, client_id, credential_hash, state, allowed_scopes)
VALUES
  ('00000000-0000-0000-0000-000000000101', 'commerce-service',
   '$stateeval_commerce_service_hash', 'ACTIVE', 'eval:principal:manage'),
  ('00000000-0000-0000-0000-000000000102', 'evaluation-client',
   '$stateeval_evaluation_client_hash', 'ACTIVE', 'eval:test-token:issue'),
  ('00000000-0000-0000-0000-000000000103', 'agent-service',
   '$stateeval_agent_service_hash', 'ACTIVE', 'catalog:read refund:create');
INSERT INTO auth_signing_key_metadata (kid, state, activated_at, retire_after)
VALUES ('stateeval-current', 'CURRENT', CURRENT_TIMESTAMP(6), NULL);
"

mysql_root "" "
DROP USER IF EXISTS 'stateeval_grader'@'%';
CREATE USER 'stateeval_grader'@'%' IDENTIFIED BY '$stateeval_grader_password';
GRANT SELECT ON commerce_db.standard_order TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.mock_payment_attempt TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.mock_payment_callback TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.inventory_ledger TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.mock_refund TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.commerce_outbox TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.pending_action TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.action_receipt TO 'stateeval_grader'@'%';
GRANT SELECT ON cs_db.support_event TO 'stateeval_grader'@'%';
"

start_auth
start_commerce
start_model_fixture
start_agent \
  control \
  true \
  "http://127.0.0.1:$stateeval_fixture_model_port" \
  support-standard-primary \
  "" \
  "" \
  2 \
  stateeval_control_agent_pid \
  stateeval_control_agent_port
start_agent \
  on \
  true \
  "$stateeval_real_model_proxy_url" \
  "$stateeval_model_name" \
  "$stateeval_real_model_proxy_api_key" \
  "$stateeval_model_temperature" \
  "$stateeval_model_timeout_seconds" \
  stateeval_agent_on_pid \
  stateeval_agent_on_port
start_agent \
  off \
  false \
  "$stateeval_real_model_proxy_url" \
  "$stateeval_model_name" \
  "$stateeval_real_model_proxy_api_key" \
  "$stateeval_model_temperature" \
  "$stateeval_model_timeout_seconds" \
  stateeval_agent_off_pid \
  stateeval_agent_off_port

# Provider location and credentials are runtime configuration, not evidence.
stateeval_real_model_proxy_url=""
stateeval_real_model_proxy_api_key=""
unset stateeval_real_model_proxy_url stateeval_real_model_proxy_api_key

# Re-check immutable source and output boundaries immediately before the
# campaign can create or resume an artifact.
check_repositories
if [[ "$(cd "$stateeval_output_parent_input" && pwd -P)" != "$stateeval_output_parent" ]]; then
  echo "Campaign output parent changed after preflight; refusing to continue." >&2
  exit 2
fi
if [[ "$stateeval_command" == "execute" ]]; then
  if [[ -e "$stateeval_output" || -L "$stateeval_output" ]]; then
    echo "execute output appeared after preflight; refusing to continue." >&2
    exit 2
  fi
else
  if ! resume_output_is_valid; then
    echo "resume output changed after preflight; refusing to continue." >&2
    exit 2
  fi
fi
assert_runtime_isolated_and_alive

stateeval_campaign_args=(--output "$stateeval_output")
if [[ "$stateeval_command" == "resume" ]]; then
  stateeval_campaign_args+=(--resume)
fi

(
  cd "$stateeval_root"
  export \
    PYTHONPATH=src \
    STATEEVAL_AUTH_BASE_URL="http://127.0.0.1:$stateeval_auth_port" \
    STATEEVAL_COMMERCE_ON_BASE_URL="http://127.0.0.1:$stateeval_commerce_off_port" \
    STATEEVAL_COMMERCE_OFF_BASE_URL="http://127.0.0.1:$stateeval_commerce_off_port" \
    STATEEVAL_AGENT_ON_BASE_URL="http://127.0.0.1:$stateeval_agent_on_port" \
    STATEEVAL_AGENT_OFF_BASE_URL="http://127.0.0.1:$stateeval_agent_off_port" \
    STATEEVAL_CONTROL_AGENT_BASE_URL="http://127.0.0.1:$stateeval_control_agent_port" \
    STATEEVAL_MANAGEMENT_PASSWORD="$stateeval_management_password" \
    STATEEVAL_EVALUATION_CLIENT_PASSWORD="$stateeval_evaluation_client_password" \
    STATEEVAL_MYSQL_CONTAINER="$stateeval_mysql_container" \
    STATEEVAL_MYSQL_USER=stateeval_grader \
    STATEEVAL_MYSQL_PASSWORD="$stateeval_grader_password" \
    STATEEVAL_MOCK_PAYMENT_KEY="$stateeval_mock_payment_key" \
    STATEEVAL_MOCK_PAYMENT_SECRET="$stateeval_mock_payment_secret" \
    STATEEVAL_CITYBUDDY_COMMIT="$actual_citybuddy_commit" \
    STATEEVAL_MODEL_NAME="$stateeval_model_name" \
    STATEEVAL_MODEL_TEMPERATURE="$stateeval_model_temperature" \
    STATEEVAL_MODEL_TIMEOUT_SECONDS="$stateeval_model_timeout_seconds" \
    STATEEVAL_OWNERSHIP_OFF_LAUNCH_ID="$stateeval_ownership_off_launch_id" \
    STATEEVAL_OWNERSHIP_OFF_PID="$stateeval_commerce_off_pid" \
    STATEEVAL_SESSION_ON_LAUNCH_ID="$stateeval_session_on_launch_id" \
    STATEEVAL_SESSION_ON_PID="$stateeval_agent_on_pid" \
    STATEEVAL_SESSION_OFF_LAUNCH_ID="$stateeval_session_off_launch_id" \
    STATEEVAL_SESSION_OFF_PID="$stateeval_agent_off_pid" \
    STATEEVAL_AGENT_WORKERS=1 \
    STATEEVAL_AGENT_HTTP_CLIENT_LAYOUT=shared \
    STATEEVAL_SESSION_PROPAGATION_ON_ENABLED=true \
    STATEEVAL_SESSION_PROPAGATION_OFF_ENABLED=false \
    STATEEVAL_TRACE_EXPORT_ENABLED=false \
    STATEEVAL_METRICS_ENABLED=false
  exec python3 -m stateeval.citybuddy_session_propagation_campaign \
    "${stateeval_campaign_args[@]}"
) &
stateeval_campaign_pid=$!
stateeval_run_status=0
wait "$stateeval_campaign_pid" || stateeval_run_status=$?
stateeval_campaign_pid=""
stateeval_continuity_status=0
assert_runtime_isolated_and_alive || stateeval_continuity_status=$?
if [[ "$stateeval_run_status" -eq 0 && "$stateeval_continuity_status" -ne 0 ]]; then
  stateeval_run_status="$stateeval_continuity_status"
fi
exit "$stateeval_run_status"
