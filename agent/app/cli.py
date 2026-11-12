"""Operator CLI (runs inside the agent container).

  python -m app.cli bootstrap [--print-env]   create bot user + token, enable local webhooks
  python -m app.cli setup-project <id|path>   add bot as Developer, create webhook, require pipelines to succeed
  python -m app.cli demo                      create a demo project + MR with intentional flaws
  python -m app.cli create-runner             print a GitLab Runner authentication token
  python -m app.cli review <project> <mr>     queue a manual review
  python -m app.cli check                     verify GitLab / Ollama / Redis / DB connectivity
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from .config import get_settings
from .gitlab_client import GitLabClient, GitLabError, NotFound, enc

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "demo"
WEBHOOK_URL = "http://agent-api:8080/webhook/gitlab"
DEMO_PROJECT = "ai-review-demo"


def admin_client() -> GitLabClient:
    s = get_settings()
    if not s.gitlab_admin_token:
        sys.exit("GITLAB_ADMIN_TOKEN is not set (run scripts/bootstrap.sh or create an admin PAT manually).")
    return GitLabClient(s.gitlab_url, s.gitlab_admin_token, s.gitlab_verify_ssl)


def cmd_bootstrap(args) -> None:
    s = get_settings()
    gl = admin_client()
    gl.request("PUT", "/application/settings", json={"allow_local_requests_from_web_hooks_and_services": True})
    users = gl.request("GET", "/users", params={"username": s.gitlab_bot_username}).json()
    if users:
        user = users[0]
    else:
        user = gl.request(
            "POST",
            "/users",
            json={
                "username": s.gitlab_bot_username,
                "name": "AI Code Reviewer",
                "email": f"{s.gitlab_bot_username}@example.local",
                "password": "Bot-" + s.gitlab_webhook_secret[:12] + "-Xk29!",
                "skip_confirmation": True,
                "can_create_group": False,
                "projects_limit": 0,
            },
        ).json()
    expires = (date.today() + timedelta(days=300)).isoformat()
    tok = gl.request(
        "POST",
        f"/users/{user['id']}/personal_access_tokens",
        json={"name": "ai-review-agent", "scopes": ["api"], "expires_at": expires},
    ).json()
    if args.print_env:
        print(f"GITLAB_TOKEN={tok['token']}")
    else:
        print(f"Bot user '{user['username']}' ready. Token: {tok['token']}")


def _project(gl: GitLabClient, ident: str) -> dict:
    return gl.request("GET", f"/projects/{enc(ident)}").json()


def cmd_setup_project(args) -> dict:
    s = get_settings()
    gl = admin_client()
    proj = _project(gl, args.project)
    pid = proj["id"]
    users = gl.request("GET", "/users", params={"username": s.gitlab_bot_username}).json()
    if not users:
        sys.exit("bot user not found - run `bootstrap` first")
    try:
        gl.request("POST", f"/projects/{pid}/members", json={"user_id": users[0]["id"], "access_level": 30})
    except GitLabError as exc:
        if exc.status != 409:  # already a member
            raise
    hooks = gl.request("GET", f"/projects/{pid}/hooks").json()
    if not any(h["url"] == WEBHOOK_URL for h in hooks):
        gl.request(
            "POST",
            f"/projects/{pid}/hooks",
            json={
                "url": WEBHOOK_URL,
                "token": s.gitlab_webhook_secret,
                "merge_requests_events": True,
                "note_events": True,
                "push_events": False,
                "enable_ssl_verification": False,
            },
        )
    gl.request("PUT", f"/projects/{pid}", json={"only_allow_merge_if_pipeline_succeeds": True})
    print(f"Project {proj['path_with_namespace']} (id {pid}) configured: bot=Developer, webhook, merge gate on.")
    return proj


def cmd_demo(_args) -> None:
    gl = admin_client()
    try:
        proj = _project(gl, f"root/{DEMO_PROJECT}")
    except NotFound:
        proj = gl.request(
            "POST",
            "/projects",
            json={"name": DEMO_PROJECT, "initialize_with_readme": True, "visibility": "private"},
        ).json()
    pid = proj["id"]
    ns = argparse.Namespace(project=str(pid))
    cmd_setup_project(ns)

    branch = "feature/risky-changes"
    try:
        gl.request("POST", f"/projects/{pid}/repository/branches", params={"branch": branch, "ref": proj["default_branch"]})
    except GitLabError as exc:
        if exc.status != 400:  # branch exists
            raise
    actions = []
    for f in sorted(SAMPLES.rglob("*")):
        if f.is_file():
            actions.append(
                {"action": "create", "file_path": str(f.relative_to(SAMPLES)), "content": f.read_text(encoding="utf-8")}
            )
    try:
        gl.request(
            "POST",
            f"/projects/{pid}/repository/commits",
            json={"branch": branch, "commit_message": "Add user service, order repository and web helpers", "actions": actions},
        )
    except GitLabError as exc:
        print(f"commit skipped ({exc})")
    try:
        mr = gl.request(
            "POST",
            f"/projects/{pid}/merge_requests",
            json={
                "source_branch": branch,
                "target_branch": proj["default_branch"],
                "title": "Add user service, order repository and web helpers",
                "description": "Adds a Python user service, a Java order repository and JS/TS web helpers.",
            },
        ).json()
        print(f"Merge request created: {mr['web_url']}")
    except GitLabError as exc:
        print(f"MR not created ({exc}); it may already exist.")
    print("The agent reviews it automatically: `docker compose logs -f agent-worker`")


def cmd_create_runner(_args) -> None:
    gl = admin_client()
    r = gl.request(
        "POST", "/user/runners", json={"runner_type": "instance_type", "description": "ai-review-local", "tag_list": ["local"], "run_untagged": True}
    ).json()
    print(r["token"])


def cmd_review(args) -> None:
    from .queueing import enqueue_review
    from .webhook import ReviewRequest

    print(enqueue_review(get_settings(), ReviewRequest(int(args.project), int(args.mr), "", "manual")))


def cmd_check(_args) -> None:
    from .ollama_client import OllamaClient
    from .queueing import get_redis

    s = get_settings()
    ok, msg = OllamaClient(s).health()
    print(f"ollama : {'OK' if ok else 'FAIL'} - {msg}")
    try:
        get_redis(s).ping()
        print("redis  : OK")
    except Exception as exc:  # noqa: BLE001
        print(f"redis  : FAIL - {exc}")
    try:
        user = GitLabClient(s.gitlab_url, s.gitlab_token, s.gitlab_verify_ssl, timeout=5).request("GET", "/user").json()
        print(f"gitlab : OK - authenticated as {user['username']}")
    except Exception as exc:  # noqa: BLE001
        print(f"gitlab : FAIL - {exc}")


def main() -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap")
    b.add_argument("--print-env", action="store_true")
    b.set_defaults(fn=cmd_bootstrap)
    sp = sub.add_parser("setup-project")
    sp.add_argument("project", help="numeric id or group/path")
    sp.set_defaults(fn=cmd_setup_project)
    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    sub.add_parser("create-runner").set_defaults(fn=cmd_create_runner)
    rv = sub.add_parser("review")
    rv.add_argument("project")
    rv.add_argument("mr")
    rv.set_defaults(fn=cmd_review)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
