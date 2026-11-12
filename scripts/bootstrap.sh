#!/usr/bin/env bash
# One-time setup after `docker compose up -d`:
#   1. waits for GitLab
#   2. creates an admin token for root (via rails runner)
#   3. creates the ai-reviewer bot user + token (via REST), enables local webhooks
#   4. writes tokens to .env and restarts the agent containers
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || cp .env.example .env

set_env() { # key value
  if grep -q "^$1=" .env; then
    sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
  else
    echo "$1=$2" >> .env
  fi
}
get_env() { grep "^$1=" .env | head -n1 | cut -d= -f2- || true; }

PORT="$(get_env GITLAB_PORT)"; PORT="${PORT:-8929}"

echo "==> Waiting for GitLab on http://localhost:${PORT} (first start takes 3-8 minutes)..."
for i in $(seq 1 120); do
  if curl -fsS -o /dev/null "http://localhost:${PORT}/users/sign_in"; then break; fi
  sleep 10
  [ "$i" = 120 ] && { echo "GitLab did not become ready"; exit 1; }
done
echo "    GitLab is up."

if [ "$(get_env GITLAB_WEBHOOK_SECRET)" = "change-me-webhook-secret" ] || [ -z "$(get_env GITLAB_WEBHOOK_SECRET)" ]; then
  set_env GITLAB_WEBHOOK_SECRET "$(openssl rand -hex 16)"
fi

if [ -z "$(get_env GITLAB_ADMIN_TOKEN)" ]; then
  echo "==> Creating admin API token for root (rails runner, ~1 min)..."
  ADMIN_TOKEN="glpat-$(openssl rand -hex 13)"
  docker compose cp scripts/gitlab_admin_token.rb gitlab:/tmp/gitlab_admin_token.rb
  docker compose exec -T -e ADMIN_TOKEN="$ADMIN_TOKEN" gitlab gitlab-rails runner /tmp/gitlab_admin_token.rb
  set_env GITLAB_ADMIN_TOKEN "$ADMIN_TOKEN"
fi

echo "==> Building agent image..."
docker compose build agent-api

echo "==> Creating bot user + token..."
BOT_LINE="$(docker compose run --rm -T --no-deps agent-api python -m app.cli bootstrap --print-env | grep '^GITLAB_TOKEN=')"
set_env GITLAB_TOKEN "${BOT_LINE#GITLAB_TOKEN=}"

echo "==> (Re)starting agent services..."
docker compose up -d agent-api agent-worker

cat <<EOF

Done.
  GitLab : http://localhost:${PORT}   (root / $(get_env GITLAB_ROOT_PASSWORD))
  Agent  : http://localhost:$(get_env AGENT_PORT || echo 8080)/healthz

Next:  make demo      # creates a demo project + merge request that gets reviewed
EOF
