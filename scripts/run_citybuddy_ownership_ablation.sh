#!/usr/bin/env bash
set -euo pipefail

expected_proxy_attestation="CLIProxyAPI/7.2.76/9f62c8df28dc749ea976865450a458917bf45042/ad8d0e9d43888c794f32d9a36842c395f641038a1a622f650c7868dc6a359f0d"

if [[ -z "${AGENT_MODEL_PROXY_URL:-}" ]]; then
  echo "AGENT_MODEL_PROXY_URL is required for both measured arms." >&2
  exit 2
fi
if [[ -z "${AGENT_MODEL_PROXY_API_KEY:-}" ]]; then
  echo "AGENT_MODEL_PROXY_API_KEY is required for both measured arms." >&2
  exit 2
fi
if [[ -z "${STATEEVAL_MODEL_NAME:-}" ]]; then
  echo "STATEEVAL_MODEL_NAME is required for both measured arms." >&2
  exit 2
fi
if [[ "$STATEEVAL_MODEL_NAME" != "gpt-5.4" ]]; then
  echo "STATEEVAL_MODEL_NAME must be gpt-5.4 for this fixed evaluation." >&2
  exit 2
fi
if [[ -z "${STATEEVAL_MODEL_TEMPERATURE:-}" ]]; then
  echo "STATEEVAL_MODEL_TEMPERATURE is required as fixed run metadata." >&2
  exit 2
fi
if [[ -z "${STATEEVAL_MODEL_TIMEOUT_SECONDS:-}" ]]; then
  echo "STATEEVAL_MODEL_TIMEOUT_SECONDS is required for both measured arms." >&2
  exit 2
fi
if [[ "${STATEEVAL_PROXY_ATTESTATION:-}" != "$expected_proxy_attestation" ]]; then
  echo "STATEEVAL_PROXY_ATTESTATION must identify the inspected proxy deployment." >&2
  exit 2
fi

stateeval_real_model_proxy_url="${AGENT_MODEL_PROXY_URL%/}"
stateeval_real_model_proxy_api_key="$AGENT_MODEL_PROXY_API_KEY"
stateeval_model_name="$STATEEVAL_MODEL_NAME"
stateeval_model_temperature="$STATEEVAL_MODEL_TEMPERATURE"
stateeval_model_timeout_seconds="$STATEEVAL_MODEL_TIMEOUT_SECONDS"
export -n \
  expected_proxy_attestation \
  stateeval_real_model_proxy_url \
  stateeval_real_model_proxy_api_key \
  stateeval_model_name \
  stateeval_model_temperature \
  stateeval_model_timeout_seconds

# Provider configuration is passed only to the two measured agent processes below.
unset AGENT_MODEL_PROXY_URL AGENT_MODEL_PROXY_API_KEY
unset AGENT_MODEL_TEMPERATURE AGENT_MODEL_TIMEOUT_SECONDS
unset CLIPROXY_BASE_URL CLIPROXY_API_KEY
unset STATEEVAL_PROXY_ATTESTATION

stateeval_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
citybuddy_root="${CITYBUDDY_REPO:-/Users/zhuochen/Dev/citybuddy}"
expected_citybuddy_commit="1238be92c193d37582dd987e2032cffaf90f2c57"
actual_citybuddy_commit="$(git -C "$citybuddy_root" rev-parse HEAD)"

if [[ "$actual_citybuddy_commit" != "$expected_citybuddy_commit" ]]; then
  echo "CityBuddy must be at $expected_citybuddy_commit; found $actual_citybuddy_commit." >&2
  exit 1
fi
if [[ -n "$(git -C "$citybuddy_root" status --porcelain)" ]]; then
  echo "CityBuddy has working-tree changes; refusing to benchmark a moving target." >&2
  exit 1
fi

source "$citybuddy_root/scripts/test_dynamic_ports.sh"

stateeval_runtime_dir="$(mktemp -d)"
stateeval_env_file="$stateeval_runtime_dir/.env"
stateeval_project="stateeval-m2-$$"
stateeval_auth_pid=""
stateeval_commerce_on_pid=""
stateeval_commerce_off_pid=""
stateeval_fixture_model_pid=""
stateeval_control_agent_pid=""
stateeval_agent_on_pid=""
stateeval_agent_off_pid=""
stateeval_mysql_port=""
stateeval_auth_port=""
stateeval_commerce_on_port=""
stateeval_commerce_off_port=""
stateeval_fixture_model_port=""
stateeval_control_agent_port=""
stateeval_agent_on_port=""
stateeval_agent_off_port=""
stateeval_ownership_off_launch_id="stateeval-ownership-off-$(openssl rand -hex 12)"
compose=(
  docker compose
  --project-name "$stateeval_project"
  --env-file "$stateeval_env_file"
  --file "$citybuddy_root/compose.yaml"
)

cleanup() {
  local status=$?
  local resource_status=0
  for pid in \
    "$stateeval_agent_off_pid" \
    "$stateeval_agent_on_pid" \
    "$stateeval_control_agent_pid" \
    "$stateeval_fixture_model_pid" \
    "$stateeval_commerce_off_pid" \
    "$stateeval_commerce_on_pid" \
    "$stateeval_auth_pid"; do
    if [[ -n "$pid" ]]; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  for pid in \
    "$stateeval_agent_off_pid" \
    "$stateeval_agent_on_pid" \
    "$stateeval_control_agent_pid" \
    "$stateeval_fixture_model_pid" \
    "$stateeval_commerce_off_pid" \
    "$stateeval_commerce_on_pid" \
    "$stateeval_auth_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" >/dev/null 2>&1 || true
    fi
  done
  (
    cd "$citybuddy_root"
    "${compose[@]}" down --volumes --remove-orphans
  ) >/dev/null 2>&1 || resource_status=$?
  rm -rf "$stateeval_runtime_dir"
  finish_test_cleanup "$status" "$resource_status"
}
trap cleanup EXIT

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
  MYSQL_PWD="$stateeval_mysql_root_password" "${compose[@]}" exec -T -e MYSQL_PWD \
    mysql "${args[@]}" --execute="$statement"
}

wait_http() {
  local url="$1"
  local pid="$2"
  local log="$3"
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
  local label="$1"
  local ownership_binding="$2"
  local pid_variable="$3"
  local port_variable="$4"
  local log="$stateeval_runtime_dir/commerce-$label.log"
  local log_offset
  case "$ownership_binding" in
    true | false) ;;
    *)
      echo "Invalid ownership-binding value: $ownership_binding" >&2
      return 2
      ;;
  esac
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
    --citybuddy.evaluation.build-id=stateeval-m2 \
    --citybuddy.evaluation.schema-compatibility=commerce-evaluation-v1 \
    --citybuddy.evaluation.action-ownership-binding-enabled="$ownership_binding" \
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
  local pid=$!
  printf -v "$pid_variable" '%s' "$pid"
  process_bound_port \
    "$port_variable" spring "$pid" "$log" "$log_offset"
  local port="${!port_variable}"
  wait_http \
    "http://127.0.0.1:$port/api/products" \
    "$pid" \
    "$log"
}

start_model_fixture() {
  local log_offset
  port_log_offset log_offset "$stateeval_runtime_dir/control-model.log"
  (
    cd "$citybuddy_root"
    uv run python scripts/fake_litellm_server.py --port 0
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
  local model_proxy_url="$2"
  local model_name="$3"
  local model_proxy_api_key="$4"
  local model_temperature="$5"
  local model_timeout_seconds="$6"
  local commerce_port="$7"
  local pid_variable="$8"
  local port_variable="$9"
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
    AGENT_PORT=0 \
    AGENT_IDENTITY_ENABLED=true \
    AGENT_EVALUATION_ENABLED=true \
    AGENT_EVALUATION_CLIENT_ID=evaluation-manager \
    AGENT_EVALUATION_CLIENT_SECRET="$stateeval_management_password" \
    CITYBUDDY_METRICS_ENABLED=true \
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
    AGENT_COMMERCE_TOOLS_URL="http://127.0.0.1:$commerce_port" \
    AGENT_COMMERCE_LIVENESS_URL="http://127.0.0.1:$commerce_port" \
    uv run citybuddy-agent
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

assert_ownership_off_alive() {
  if kill -0 "$stateeval_commerce_off_pid" >/dev/null 2>&1; then
    return 0
  fi
  tail -n 120 "$stateeval_runtime_dir/commerce-off.log" >&2 || true
  echo "Ownership-binding-off commerce process did not remain continuous." >&2
  return 1
}

(
  cd "$citybuddy_root"
  ENV_FILE="$stateeval_env_file" ./scripts/init_local.sh
  "${compose[@]}" up --detach --wait --wait-timeout 60 mysql
)
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

(
  cd "$citybuddy_root"
  make ENV_FILE="$stateeval_env_file" COMPOSE_PROJECT_NAME="$stateeval_project" grant-access
  make ENV_FILE="$stateeval_env_file" COMPOSE_PROJECT_NAME="$stateeval_project" \
    migrate-auth migrate-commerce migrate-agent
  make ENV_FILE="$stateeval_env_file" COMPOSE_PROJECT_NAME="$stateeval_project" grant-access
  ./mvnw -q -pl auth-service,commerce-service -am -DskipTests clean package
)

stateeval_commerce_service_password="$(openssl rand -hex 24)"
stateeval_evaluation_client_password="$(openssl rand -hex 24)"
stateeval_agent_service_password="$(openssl rand -hex 24)"
stateeval_management_password="$(openssl rand -hex 24)"
stateeval_grader_password="$(openssl rand -hex 24)"
stateeval_mock_payment_key="stateeval-$(openssl rand -hex 12)"
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
start_commerce on true stateeval_commerce_on_pid stateeval_commerce_on_port
start_commerce off false stateeval_commerce_off_pid stateeval_commerce_off_port
start_model_fixture
start_agent \
  control \
  "http://127.0.0.1:$stateeval_fixture_model_port" \
  support-standard-primary \
  "" \
  "" \
  2 \
  "$stateeval_commerce_off_port" \
  stateeval_control_agent_pid \
  stateeval_control_agent_port
start_agent \
  on \
  "$stateeval_real_model_proxy_url" \
  "$stateeval_model_name" \
  "$stateeval_real_model_proxy_api_key" \
  "$stateeval_model_temperature" \
  "$stateeval_model_timeout_seconds" \
  "$stateeval_commerce_on_port" \
  stateeval_agent_on_pid \
  stateeval_agent_on_port
start_agent \
  off \
  "$stateeval_real_model_proxy_url" \
  "$stateeval_model_name" \
  "$stateeval_real_model_proxy_api_key" \
  "$stateeval_model_temperature" \
  "$stateeval_model_timeout_seconds" \
  "$stateeval_commerce_off_port" \
  stateeval_agent_off_pid \
  stateeval_agent_off_port

# Provider location and credentials are runtime configuration, not evaluation evidence.
stateeval_real_model_proxy_url=""
stateeval_real_model_proxy_api_key=""
unset stateeval_real_model_proxy_url stateeval_real_model_proxy_api_key

stateeval_output_dir="${STATEEVAL_RESULTS_DIR:-$stateeval_root/results/milestone-2}"
assert_ownership_off_alive
stateeval_run_status=0
PYTHONPATH="$stateeval_root/src" \
STATEEVAL_AUTH_BASE_URL="http://127.0.0.1:$stateeval_auth_port" \
STATEEVAL_COMMERCE_ON_BASE_URL="http://127.0.0.1:$stateeval_commerce_on_port" \
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
python3 -m stateeval.citybuddy --output "$stateeval_output_dir" || stateeval_run_status=$?
assert_ownership_off_alive
exit "$stateeval_run_status"
