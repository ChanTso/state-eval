#!/usr/bin/env bash
set -euo pipefail
umask 077

# The measured host reads provider credentials from CityBuddy's existing .env itself.
unset CLIPROXY_BASE_URL CLIPROXY_API_KEY AGENT_MODEL_PROXY_URL AGENT_MODEL_PROXY_API_KEY
unset OPENAI_API_KEY OPENAI_BASE_URL ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL
unset PYTHONPATH MYSQL_PWD SHOPMATE_CONFIG COMPOSE_PROJECT_NAME
export GIT_OPTIONAL_LOCKS=0

fail() { echo "$1" >&2; exit 2; }
usage() {
  echo "usage: $0 --output ABSOLUTE_NEW_DIRECTORY [--stage controls|pilot] [--trials POSITIVE_INTEGER]" >&2
}
stateeval_stage=controls
stateeval_trials=""
stateeval_output=""
stateeval_seen=" "
while (($#)); do
  case "$1" in
    --help) usage; exit 0 ;;
    --output | --stage | --trials)
      (($# >= 2)) || fail "$1 requires a value."
      [[ "$stateeval_seen" != *" $1 "* ]] || fail "Repeated argument: $1"
      stateeval_seen+="$1 "
      case "$1" in
        --output) stateeval_output="$2" ;;
        --stage) stateeval_stage="$2" ;;
        --trials) stateeval_trials="$2" ;;
      esac
      shift 2
      ;;
    *) usage; fail "Unknown argument: $1" ;;
  esac
done
case "$stateeval_stage" in controls | pilot) ;; *) fail "Stage must be controls or pilot." ;; esac
[[ -z "$stateeval_trials" || "$stateeval_trials" =~ ^[1-9][0-9]*$ ]] \
  || fail "Trials must be a positive integer."
[[ "$stateeval_output" == /* && ! -e "$stateeval_output" && ! -L "$stateeval_output" ]] \
  || fail "Output must be a new absolute directory."
stateeval_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
citybuddy_root="$(cd "${CITYBUDDY_REPO:-$stateeval_root/../citybuddy}" && pwd -P)"
shopmate_root="$(cd "${SHOPMATE_REPO:-$stateeval_root/../shopmate}" && pwd -P)"
stateeval_output_parent="$(cd "$(dirname "$stateeval_output")" && pwd -P)"
[[ "$stateeval_output_parent/$(basename "$stateeval_output")" == "$stateeval_output" ]] \
  || fail "Output must have an existing canonical parent."
case "$stateeval_output/" in
  "$citybuddy_root/"* | "$shopmate_root/"* | "$stateeval_root/src/"* | \
  "$stateeval_root/scripts/"* | "$stateeval_root/tests/"* | \
  "$stateeval_root/results/milestone-1/"* | "$stateeval_root/results/milestone-2/"*)
    fail "Output overlaps application state, source or historical results." ;;
esac
for command_name in curl docker git java make mktemp openssl python3; do
  command -v "$command_name" >/dev/null || fail "Required command unavailable: $command_name"
done
[[ -x "$shopmate_root/.venv/bin/python" ]] || fail "ShopMate's installed Python environment is required."
check_repository() {
  local root="$1" variable="$2" sha
  [[ "$(git -C "$root" rev-parse --show-toplevel)" == "$root" ]] || fail "Repository root mismatch."
  sha="$(git -C "$root" rev-parse HEAD)"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "Repository did not resolve to a full commit SHA."
  [[ -z "$(git -C "$root" status --porcelain --untracked-files=normal)" ]] \
    || fail "All three repositories must be committed and source-clean before evaluation."
  printf -v "$variable" '%s' "$sha"
}
check_repository "$citybuddy_root" citybuddy_commit
check_repository "$shopmate_root" shopmate_commit
check_repository "$stateeval_root" stateeval_commit
stateeval_model_name="${STATEEVAL_MODEL_NAME:-gpt-5.6-terra}"
[[ -n "$stateeval_model_name" && "$stateeval_model_name" != *[[:space:]]* ]] || fail "Invalid model name."
source "$citybuddy_root/scripts/test_dynamic_ports.sh"
stateeval_runtime_dir="$(mktemp -d "${TMPDIR:-/tmp}/stateeval-shopmate.XXXXXXXX")"
stateeval_env_file="$stateeval_runtime_dir/.env"
stateeval_project="stateeval-shopmate-$$-$(openssl rand -hex 6)"
stateeval_auth_pid=""
stateeval_commerce_on_pid=""
stateeval_commerce_off_pid=""
stateeval_driver_pid=""
stateeval_topology_started=false
stateeval_driver_completed=false
compose=(docker compose --project-name "$stateeval_project" --env-file "$stateeval_env_file" --file "$citybuddy_root/compose.yaml")

stop_owned() {
  local pid="$1" grace_seconds="$2" attempt
  [[ -n "$pid" ]] || return 0
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || return 1
    for ((attempt = 0; attempt < grace_seconds * 10; attempt++)); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      return 1
    fi
  fi
  wait "$pid" 2>/dev/null || true
}
cleanup() {
  local status=$? safe=true
  trap - EXIT INT TERM
  [[ "$status" == 0 && "$stateeval_driver_completed" == true && ! -e "$stateeval_output/RETAIN_FIXTURE" ]] || safe=false
  # The driver closes its own host before exiting; that close can take up to 30 seconds.
  stop_owned "$stateeval_driver_pid" 45 || safe=false
  for pid in "$stateeval_commerce_off_pid" "$stateeval_commerce_on_pid" "$stateeval_auth_pid"; do
    stop_owned "$pid" 10 || safe=false
  done
  if [[ "$stateeval_topology_started" == true ]]; then
    if [[ "$safe" == true ]]; then
      if ! "${compose[@]}" down --volumes --remove-orphans >>"$stateeval_runtime_dir/cleanup.log" 2>&1; then
        safe=false
        "${compose[@]}" stop >>"$stateeval_runtime_dir/cleanup.log" 2>&1 || true
      fi
    else
      "${compose[@]}" stop >>"$stateeval_runtime_dir/cleanup.log" 2>&1 || true
    fi
  fi
  if [[ "$safe" == true ]]; then
    rm -rf "$stateeval_runtime_dir"
  else
    printf '%s\n' "Private runtime and diagnostic logs retained: $stateeval_runtime_dir" >&2
    printf '%s\n' "Compose project: $stateeval_project; owned application processes were stopped." >&2
    [[ "$status" != 0 ]] || status=1
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

read_value() { sed -n "s/^$1=//p" "$stateeval_env_file"; }
mysql_root() {
  MYSQL_PWD="$stateeval_mysql_root_password" "${compose[@]}" exec -T -e MYSQL_PWD mysql \
    mysql --protocol=tcp --host=127.0.0.1 --port=3306 --user=root --batch --skip-column-names
}
wait_http() {
  local url="$1" pid="$2"
  for _ in {1..90}; do
    kill -0 "$pid" 2>/dev/null || fail "Owned service exited; inspect private runtime logs."
    if curl --silent --max-time 2 --output /dev/null "$url" 2>/dev/null; then return 0; fi
    sleep 1
  done
  fail "Owned service did not become reachable; inspect private runtime logs."
}
start_auth() {
  SPRING_DATASOURCE_PASSWORD="$stateeval_auth_app_password" \
    java -jar "$citybuddy_root/auth-service/target/auth-service-0.0.1-SNAPSHOT.jar" \
    --server.address=127.0.0.1 --server.port=0 --spring.profiles.active=evaluation \
    --spring.datasource.url="jdbc:mysql://127.0.0.1:$stateeval_mysql_port/commerce_db?useSSL=false&allowPublicKeyRetrieval=true" \
    --spring.datasource.username=auth_app --citybuddy.identity.enabled=true \
    --citybuddy.identity.issuer=https://identity.citybuddy.test --citybuddy.identity.user-audience=citybuddy-web \
    --citybuddy.identity.current-kid=stateeval-current \
    --citybuddy.identity.current-private-key-path="$stateeval_runtime_dir/current-private.pem" \
    --citybuddy.identity.current-public-key-path="$stateeval_runtime_dir/current-public.pem" \
    '--citybuddy.identity.exchange-scopes[0]=shopping:orders:read' \
    '--citybuddy.identity.exchange-scopes[1]=shopping:profile:read' \
    '--citybuddy.identity.exchange-scopes[2]=shopping:cart:read' \
    '--citybuddy.identity.exchange-scopes[3]=refund:create' \
    >"$stateeval_runtime_dir/auth.log" 2>&1 &
  stateeval_auth_pid=$!
  process_bound_port stateeval_auth_port spring "$stateeval_auth_pid" "$stateeval_runtime_dir/auth.log" 0 \
    >>"$stateeval_runtime_dir/startup.log" 2>&1 || fail "Auth did not publish its bound port."
  wait_http "http://127.0.0.1:$stateeval_auth_port/auth/jwks" "$stateeval_auth_pid"
}
start_commerce() {
  local label="$1" ownership_binding="$2" pid_variable="$3" port_variable="$4"
  SPRING_DATASOURCE_PASSWORD="$stateeval_commerce_app_password" \
    SPRING_APPLICATION_JSON="{\"citybuddy.evaluation.management-client-secret\":\"$stateeval_management_password\",\"citybuddy.evaluation.auth-client-secret\":\"$stateeval_commerce_service_secret\",\"citybuddy.mock-payment.callback-secret\":\"$stateeval_mock_payment_secret\"}" \
    java -jar "$citybuddy_root/commerce-service/target/commerce-service-0.0.1-SNAPSHOT.jar" \
    --server.address=127.0.0.1 --server.port=0 --spring.profiles.active=evaluation \
    --spring.datasource.url="jdbc:mysql://127.0.0.1:$stateeval_mysql_port/commerce_db?useSSL=false&allowPublicKeyRetrieval=true" \
    --spring.datasource.username=commerce_app --spring.datasource.hikari.connection-timeout=2000 \
    --citybuddy.catalog.enabled=false --citybuddy.orders.enabled=false --citybuddy.merchant.enabled=false \
    --citybuddy.seckill.enabled=false --citybuddy.obo.enabled=true \
    --citybuddy.obo.issuer=https://identity.citybuddy.test \
    --citybuddy.obo.jwks-url="http://127.0.0.1:$stateeval_auth_port/auth/jwks" --citybuddy.obo.jwks-cache-ttl=1s \
    --citybuddy.agent-tools.enabled=true \
    --citybuddy.evaluation.management-client-id=evaluation-manager \
    --citybuddy.evaluation.auth-base-url="http://127.0.0.1:$stateeval_auth_port" \
    --citybuddy.evaluation.auth-client-id=commerce-service \
    --citybuddy.evaluation.identity-issuer=https://identity.citybuddy.test \
    --citybuddy.evaluation.user-audience=citybuddy-web \
    --citybuddy.evaluation.jwks-url="http://127.0.0.1:$stateeval_auth_port/auth/jwks" \
    --citybuddy.evaluation.jwks-cache-ttl=1s --citybuddy.evaluation.provisioning-timeout=10s \
    --citybuddy.evaluation.auth-expiry-safety=2s --citybuddy.evaluation.cleanup-retry=1s \
    --citybuddy.evaluation.janitor-interval=5s --citybuddy.evaluation.max-cleanup-attempts=5 \
    --citybuddy.evaluation.janitor-batch-size=4 --citybuddy.evaluation.build-id=stateeval-shopmate \
    --citybuddy.evaluation.schema-compatibility=commerce-evaluation-v1 \
    --citybuddy.evaluation.action-ownership-binding-enabled="$ownership_binding" \
    --citybuddy.mock-payment.enabled=true --citybuddy.mock-payment.required-permission=support:chat \
    --citybuddy.mock-payment.callback-key-id="$stateeval_mock_payment_key" \
    --citybuddy.mock-payment.callback-maximum-age=5m --citybuddy.mock-payment.callback-clock-skew=30s \
    --citybuddy.refund.enabled=true --citybuddy.refund.required-permission=refund:create \
    --citybuddy.refund.lock-wait-timeout-seconds=1 --citybuddy.refund.maximum-observation-attempts=2 \
    --citybuddy.refund.observation-backoff=25ms --citybuddy.actions.enabled=true \
    --citybuddy.actions.required-scope=refund:create --citybuddy.actions.pending-ttl=15m \
    --citybuddy.actions.lock-wait-timeout-seconds=1 --citybuddy.actions.maximum-observation-attempts=2 \
    --citybuddy.actions.observation-backoff=25ms >"$stateeval_runtime_dir/commerce-$label.log" 2>&1 &
  local pid=$!
  printf -v "$pid_variable" '%s' "$pid"
  process_bound_port "$port_variable" spring "$pid" "$stateeval_runtime_dir/commerce-$label.log" 0 \
    >>"$stateeval_runtime_dir/startup.log" 2>&1 || fail "Commerce did not publish its bound port."
  wait_http "http://127.0.0.1:${!port_variable}/internal/eval/shopping/cart" "$pid"
}

(
  cd "$citybuddy_root"
  ENV_FILE="$stateeval_env_file" ./scripts/init_local.sh
) >"$stateeval_runtime_dir/setup.log" 2>&1
stateeval_topology_started=true
"${compose[@]}" up --detach --wait --wait-timeout 90 mysql >>"$stateeval_runtime_dir/setup.log" 2>&1
compose_host_port stateeval_mysql_port mysql 3306
stateeval_mysql_container="$("${compose[@]}" ps --quiet mysql)"
[[ -n "$stateeval_mysql_container" ]] || fail "Isolated MySQL container was not resolved."
stateeval_mysql_root_password="$(read_value MYSQL_BOOTSTRAP_PASSWORD)"
stateeval_auth_app_password="$(read_value MYSQL_AUTH_APP_PASSWORD)"
stateeval_commerce_app_password="$(read_value MYSQL_COMMERCE_APP_PASSWORD)"
(
  cd "$citybuddy_root"
  make ENV_FILE="$stateeval_env_file" COMPOSE_PROJECT_NAME="$stateeval_project" grant-access
  make ENV_FILE="$stateeval_env_file" COMPOSE_PROJECT_NAME="$stateeval_project" migrate-auth migrate-commerce
  make ENV_FILE="$stateeval_env_file" COMPOSE_PROJECT_NAME="$stateeval_project" grant-access
  ./mvnw -q -pl auth-service,commerce-service -am -DskipTests clean package
) >>"$stateeval_runtime_dir/setup.log" 2>&1

stateeval_commerce_service_secret="$(python3 "$citybuddy_root/scripts/service_credential.py" generate)"
stateeval_evaluation_client_password="$(python3 "$citybuddy_root/scripts/service_credential.py" generate)"
stateeval_shopping_service_secret="$(python3 "$citybuddy_root/scripts/service_credential.py" generate)"
stateeval_management_password="$(openssl rand -hex 24)"
stateeval_grader_password="$(openssl rand -hex 24)"
stateeval_mock_payment_key="stateeval-$(openssl rand -hex 12)"
stateeval_mock_payment_secret="$(openssl rand -hex 32)"
stateeval_ownership_off_launch_id="stateeval-shopping-off-$(openssl rand -hex 12)"
stateeval_commerce_service_hash="$(printf '%s' "$stateeval_commerce_service_secret" | python3 "$citybuddy_root/scripts/service_credential.py" hash commerce-service)"
stateeval_evaluation_client_hash="$(printf '%s' "$stateeval_evaluation_client_password" | python3 "$citybuddy_root/scripts/service_credential.py" hash evaluation-client)"
stateeval_shopping_service_hash="$(printf '%s' "$stateeval_shopping_service_secret" | python3 "$citybuddy_root/scripts/service_credential.py" hash shopping-agent)"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$stateeval_runtime_dir/current-private.pem" 2>/dev/null
openssl pkey -in "$stateeval_runtime_dir/current-private.pem" -pubout -out "$stateeval_runtime_dir/current-public.pem" 2>/dev/null
mysql_root >>"$stateeval_runtime_dir/setup.log" 2>&1 <<SQL
USE commerce_db;
INSERT INTO auth_service_identity (service_id, client_id, credential_hash, state, allowed_scopes) VALUES
('00000000-0000-0000-0000-000000000101', 'commerce-service', '$stateeval_commerce_service_hash', 'ACTIVE', 'eval:principal:manage'),
('00000000-0000-0000-0000-000000000102', 'evaluation-client', '$stateeval_evaluation_client_hash', 'ACTIVE', 'eval:test-token:issue'),
('00000000-0000-0000-0000-000000000104', 'shopping-agent', '$stateeval_shopping_service_hash', 'ACTIVE', 'shopping:orders:read shopping:profile:read shopping:cart:read refund:create');
INSERT INTO auth_signing_key_metadata (kid, state, activated_at, retire_after)
VALUES ('stateeval-current', 'CURRENT', CURRENT_TIMESTAMP(6), NULL);
CREATE USER 'stateeval_grader'@'%' IDENTIFIED BY '$stateeval_grader_password';
GRANT SELECT ON commerce_db.standard_order TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.mock_payment_attempt TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.mock_payment_callback TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.inventory_ledger TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.mock_refund TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.commerce_outbox TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.pending_action TO 'stateeval_grader'@'%';
GRANT SELECT ON commerce_db.action_receipt TO 'stateeval_grader'@'%';
SQL
SPRING_DATASOURCE_URL="jdbc:mysql://127.0.0.1:$stateeval_mysql_port/commerce_db?useSSL=false&allowPublicKeyRetrieval=true" \
  SPRING_DATASOURCE_USERNAME=commerce_app SPRING_DATASOURCE_PASSWORD="$stateeval_commerce_app_password" \
  java -Dloader.main=io.citybuddy.commerce.faq.FaqFixturePublisherCli \
  -cp "$citybuddy_root/commerce-service/target/commerce-service-0.0.1-SNAPSHOT.jar" \
  org.springframework.boot.loader.launch.PropertiesLauncher \
  <"$stateeval_root/scripts/fixtures/shopmate-refund-policy.json" \
  >"$stateeval_runtime_dir/policy-publication.json" 2>"$stateeval_runtime_dir/policy-publication.log"
start_auth
start_commerce on true stateeval_commerce_on_pid stateeval_commerce_on_port
start_commerce off false stateeval_commerce_off_pid stateeval_commerce_off_port

# NUL-separated stdin avoids putting runtime secrets in argv or a shared process environment.
printf '%s\0' \
  auth_base_url "http://127.0.0.1:$stateeval_auth_port" \
  commerce_on_base_url "http://127.0.0.1:$stateeval_commerce_on_port" \
  commerce_off_base_url "http://127.0.0.1:$stateeval_commerce_off_port" \
  management_password "$stateeval_management_password" \
  evaluation_client_password "$stateeval_evaluation_client_password" \
  shopping_service_secret "$stateeval_shopping_service_secret" \
  mysql_container "$stateeval_mysql_container" mysql_user stateeval_grader mysql_password "$stateeval_grader_password" \
  mock_payment_key "$stateeval_mock_payment_key" mock_payment_secret "$stateeval_mock_payment_secret" \
  citybuddy_root "$citybuddy_root" shopmate_root "$shopmate_root" \
  citybuddy_commit "$citybuddy_commit" shopmate_commit "$shopmate_commit" stateeval_commit "$stateeval_commit" \
  model_name "$stateeval_model_name" ownership_off_launch_id "$stateeval_ownership_off_launch_id" \
  ownership_off_pid "$stateeval_commerce_off_pid" \
  | python3 -c 'import json,sys; from pathlib import Path; values=sys.stdin.buffer.read().decode().split("\0")[:-1]; result=dict(zip(values[::2],values[1::2])); result["ownership_off_pid"]=int(result["ownership_off_pid"]); Path(sys.argv[1]).write_text(json.dumps(result,indent=2)+"\n")' \
    "$stateeval_runtime_dir/runtime.json"
chmod 600 "$stateeval_runtime_dir/runtime.json"
# Compilation must not have changed the source versions recorded above.
check_repository "$citybuddy_root" checked_citybuddy_commit
check_repository "$shopmate_root" checked_shopmate_commit
check_repository "$stateeval_root" checked_stateeval_commit
[[ "$checked_citybuddy_commit" == "$citybuddy_commit" && "$checked_shopmate_commit" == "$shopmate_commit" && "$checked_stateeval_commit" == "$stateeval_commit" ]] \
  || fail "A repository moved while the isolated topology was starting."
kill -0 "$stateeval_commerce_off_pid" 2>/dev/null || fail "Ownership-off process exited before evaluation."
stateeval_driver_args=(--runtime "$stateeval_runtime_dir/runtime.json" --output "$stateeval_output" --stage "$stateeval_stage")
[[ -z "$stateeval_trials" ]] || stateeval_driver_args+=(--trials "$stateeval_trials")
PYTHONPATH="$stateeval_root/src" "$shopmate_root/.venv/bin/python" -m stateeval.shopmate "${stateeval_driver_args[@]}" &
stateeval_driver_pid=$!
stateeval_status=0
wait "$stateeval_driver_pid" || stateeval_status=$?
stateeval_driver_pid=""
[[ "$stateeval_status" == 0 ]] || exit "$stateeval_status"
for pid in "$stateeval_auth_pid" "$stateeval_commerce_on_pid" "$stateeval_commerce_off_pid"; do
  kill -0 "$pid" 2>/dev/null || fail "An owned service exited during evaluation."
done
stateeval_driver_completed=true
exit 0
