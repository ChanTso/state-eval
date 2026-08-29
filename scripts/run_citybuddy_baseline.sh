#!/usr/bin/env bash
set -euo pipefail

stateeval_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
citybuddy_root="${CITYBUDDY_REPO:-/Users/zhuochen/Dev/citybuddy}"
expected_citybuddy_commit="805a46359f937a74e4a91203181ab604fb34114d"
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
stateeval_project="stateeval-m1-$$"
stateeval_auth_pid=""
stateeval_commerce_pid=""
stateeval_model_pid=""
stateeval_agent_pid=""
stateeval_mysql_port=""
stateeval_auth_port=""
stateeval_commerce_port=""
stateeval_model_port=""
stateeval_agent_port=""
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
    "$stateeval_agent_pid" \
    "$stateeval_model_pid" \
    "$stateeval_commerce_pid" \
    "$stateeval_auth_pid"; do
    if [[ -n "$pid" ]]; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  for pid in \
    "$stateeval_agent_pid" \
    "$stateeval_model_pid" \
    "$stateeval_commerce_pid" \
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
  local log_offset
  port_log_offset log_offset "$stateeval_runtime_dir/commerce.log"
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
    --citybuddy.evaluation.build-id=stateeval-m1 \
    --citybuddy.evaluation.schema-compatibility=commerce-evaluation-v1 \
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
    >>"$stateeval_runtime_dir/commerce.log" 2>&1 &
  stateeval_commerce_pid=$!
  process_bound_port \
    stateeval_commerce_port spring "$stateeval_commerce_pid" \
    "$stateeval_runtime_dir/commerce.log" "$log_offset"
  wait_http \
    "http://127.0.0.1:$stateeval_commerce_port/api/products" \
    "$stateeval_commerce_pid" \
    "$stateeval_runtime_dir/commerce.log"
}

start_model_fixture() {
  local log_offset
  port_log_offset log_offset "$stateeval_runtime_dir/model.log"
  (
    cd "$citybuddy_root"
    uv run python scripts/fake_litellm_server.py --port 0
  ) >>"$stateeval_runtime_dir/model.log" 2>&1 &
  stateeval_model_pid=$!
  process_bound_port \
    stateeval_model_port uvicorn "$stateeval_model_pid" "$stateeval_runtime_dir/model.log" \
    "$log_offset"
  wait_http \
    "http://127.0.0.1:$stateeval_model_port/fixture/counts" \
    "$stateeval_model_pid" \
    "$stateeval_runtime_dir/model.log"
}

start_agent() {
  local log_offset
  port_log_offset log_offset "$stateeval_runtime_dir/agent.log"
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
    AGENT_MODEL_PROXY_URL="http://127.0.0.1:$stateeval_model_port" \
    AGENT_COMMERCE_TOOLS_URL="http://127.0.0.1:$stateeval_commerce_port" \
    AGENT_COMMERCE_LIVENESS_URL="http://127.0.0.1:$stateeval_commerce_port" \
    uv run citybuddy-agent
  ) >>"$stateeval_runtime_dir/agent.log" 2>&1 &
  stateeval_agent_pid=$!
  process_bound_port \
    stateeval_agent_port uvicorn "$stateeval_agent_pid" "$stateeval_runtime_dir/agent.log" \
    "$log_offset"
  wait_http \
    "http://127.0.0.1:$stateeval_agent_port/api/sessions" \
    "$stateeval_agent_pid" \
    "$stateeval_runtime_dir/agent.log"
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
"

start_auth
start_commerce
start_model_fixture
start_agent

stateeval_output_dir="${STATEEVAL_RESULTS_DIR:-$stateeval_root/results/milestone-1}"
PYTHONPATH="$stateeval_root/src" \
STATEEVAL_AUTH_BASE_URL="http://127.0.0.1:$stateeval_auth_port" \
STATEEVAL_COMMERCE_BASE_URL="http://127.0.0.1:$stateeval_commerce_port" \
STATEEVAL_AGENT_BASE_URL="http://127.0.0.1:$stateeval_agent_port" \
STATEEVAL_MODEL_BASE_URL="http://127.0.0.1:$stateeval_model_port" \
STATEEVAL_MANAGEMENT_PASSWORD="$stateeval_management_password" \
STATEEVAL_EVALUATION_CLIENT_PASSWORD="$stateeval_evaluation_client_password" \
STATEEVAL_MYSQL_CONTAINER="$stateeval_mysql_container" \
STATEEVAL_MYSQL_USER=stateeval_grader \
STATEEVAL_MYSQL_PASSWORD="$stateeval_grader_password" \
STATEEVAL_MOCK_PAYMENT_KEY="$stateeval_mock_payment_key" \
STATEEVAL_MOCK_PAYMENT_SECRET="$stateeval_mock_payment_secret" \
STATEEVAL_CITYBUDDY_COMMIT="$actual_citybuddy_commit" \
python3 -m stateeval.citybuddy --output "$stateeval_output_dir"
