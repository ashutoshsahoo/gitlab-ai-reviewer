#!/usr/bin/env bash
# Optional: start and register a Docker-executor GitLab Runner for project pipelines.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose --profile ci up -d gitlab-runner
RUNNER_TOKEN="$(docker compose run --rm -T --no-deps agent-api python -m app.cli create-runner | tail -n1)"

docker compose --profile ci exec -T gitlab-runner gitlab-runner register --non-interactive \
  --url http://gitlab:8929 \
  --clone-url http://gitlab:8929 \
  --token "$RUNNER_TOKEN" \
  --executor docker \
  --docker-image alpine:3.20 \
  --docker-network-mode ai-review-net \
  --docker-volumes /var/run/docker.sock:/var/run/docker.sock

echo "Runner registered. See samples/gitlab-ci.example.yml for a pipeline that lints and tests."
