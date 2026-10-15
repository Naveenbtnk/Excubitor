"""End-to-end check of the admin path and the demo login, against the compose stack.

This is the last check before calling a deployment good: it drives the running stack the way a
browser would -- cookies, no tokens handled by hand -- rather than importing the app, so it covers
what only a built deployment can get wrong: the environment seeding of the first administrator,
the startup wiring behind the published demo credential, and the role guards as they behave through
a real session. The in-process suite in ``backend/tests`` owns the same invariants; this is the
version that proves they survive the deployment.

It spends nothing on the provider: nothing it calls reaches the model.

    docker compose up --build -d
    python scripts/verify_live_stack.py

``backend/.env`` has to hold ``ADMIN_EMAIL`` and ``ADMIN_PASSWORD``. The demo half needs
``DEMO_ACCOUNT_ENABLED=true`` and a ``DEMO_ACCOUNT_PASSWORD`` and skips with a reason without them.
The stack is left as it was found: one throwaway administrator is created, demoted, refused and
finally deleted through the route under test, and the seeded administrator is demoted and promoted
back. Point it at another origin with ``EXCUBITOR_BASE_URL``.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import pathlib
import re
import secrets
import urllib.error
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / "backend" / ".env"
BASE = os.getenv("EXCUBITOR_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
SEED_ADMIN = "admin@cloudops.local"
THROWAWAY = "throwaway-verify@excubitor.local"
DEMO = "demo@excubitor.local"


def env(name: str) -> str:
    """Read one value from the deployment's environment file, never printing what it holds."""
    match = re.search(rf"^{name}=(.*)$", ENV_FILE.read_text(encoding="utf-8"), re.M)
    assert match, f"{name} is not set in {ENV_FILE}"
    return match.group(1).strip().strip('"').strip("'")


class Session:
    """A browser-shaped client: cookies kept, no tokens handled by hand."""

    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method: str, path: str, body: object | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            BASE + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read()
                return response.status, json.loads(raw or b"null")
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw or b"null")
            except json.JSONDecodeError:
                return error.code, raw[:200].decode("utf-8", "replace")


checks: list[bool] = []
skipped: list[str] = []


def step(label: str, ok: bool, extra: object = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' -- {extra}' if extra != '' else ''}")
    checks.append(bool(ok))
    return bool(ok)


def skip(label: str, why: str) -> None:
    print(f"  [SKIP] {label} -- {why}")
    skipped.append(label)


def detail_of(response: object) -> str:
    if isinstance(response, dict):
        return str(response.get("detail", response))
    return str(response)


print(f"== admin path ({BASE}) ==")
throwaway_password = secrets.token_urlsafe(24)
admin = Session()
status, body = admin.call(
    "POST", "/auth/login", {"email": env("ADMIN_EMAIL"), "password": env("ADMIN_PASSWORD")}
)
step("the seeded administrator signs in", status == 200, f"{status} role={body.get('user', {}).get('role')}")

status, users = admin.call("GET", "/users")
rows = users.get("items", []) if isinstance(users, dict) else []
seed = next((row for row in rows if row["email"] == SEED_ADMIN), None)
step("the roster lists the seeded administrator", status == 200 and seed is not None, f"{status} count={len(rows)}")

leftover = next((row for row in rows if row["email"] == THROWAWAY), None)
if leftover:
    # A leftover from an interrupted earlier run: it has to go first, or the create below answers
    # 409. Removing it is also the first live proof that the route deletes a row.
    status, _ = admin.call("DELETE", f"/users/{leftover['id']}")
    step("a leftover throwaway account from an earlier run is deleted", status == 204, f"{status}")

status, created = admin.call(
    "POST",
    "/users",
    {
        "email": THROWAWAY,
        "password": throwaway_password,
        "name": "Throwaway Verifier",
        "role": "admin",
    },
)
throwaway_id = created.get("id") if isinstance(created, dict) else None
step("a second administrator can be created", status == 201 and created.get("role") == "admin", f"{status} id={throwaway_id}")

second = Session()
status, body = second.call(
    "POST", "/auth/login", {"email": THROWAWAY, "password": throwaway_password}
)
step("the second administrator signs in", status == 200 and body.get("user", {}).get("role") == "admin", f"{status}")
step("and reaches the admin-only roster", second.call("GET", "/users")[0] == 200, "")

status, demoted = second.call("PATCH", f"/users/{seed['id']}", {"role": "viewer"})
step("the seeded administrator is demoted to viewer", status == 200 and demoted.get("role") == "viewer", f"{status}")

# A demotion has to bite on the next request, not the next sign-in: the session is a cookie, and
# the role is read from the row every time.
step("the demotion lands on the next request, same session", admin.call("GET", "/users")[0] == 403, "")
step("and the demoted account can no longer write", admin.call("POST", "/alerts/simulate", {})[0] == 403, "")

status, refusal = second.call("PATCH", f"/users/{throwaway_id}", {"role": "viewer"})
step("the last administrator cannot be demoted", status == 400 and "last administrator" in detail_of(refusal), f"{status}")

status, refusal = second.call("DELETE", f"/users/{throwaway_id}")
step("the last administrator cannot be deleted", status == 400 and "last administrator" in detail_of(refusal), f"{status}")

status, restored = second.call("PATCH", f"/users/{seed['id']}", {"role": "admin"})
step("the seeded administrator is promoted back", status == 200 and restored.get("role") == "admin", f"{status}")
step("and is an administrator again on the same session", admin.call("GET", "/users")[0] == 200, "")

status, refusal = second.call("DELETE", f"/users/{throwaway_id}")
step("an administrator cannot delete their own account", status == 400 and "own account" in detail_of(refusal), f"{status}")

# The throwaway row leaves through the route under test, which is also the cleanup: nothing this
# script created is left behind by the time it exits.
status, _ = admin.call("DELETE", f"/users/{throwaway_id}")
step("another administrator can delete the account", status == 204, f"{status}")
step("the deleted account's session is over", second.call("GET", "/auth/me")[0] == 401, "")

status, users = admin.call("GET", "/users")
remaining = [row["email"] for row in users.get("items", [])] if isinstance(users, dict) else []
step("the roster no longer lists it", status == 200 and THROWAWAY not in remaining, f"{status} count={len(remaining)}")

print("== demo login ==")
visitor = Session()
status, published = visitor.call("GET", "/auth/demo")
if status != 200 or not isinstance(published, dict) or not published.get("enabled"):
    skip(
        "the deployment publishes a demo credential",
        "DEMO_ACCOUNT_ENABLED / DEMO_ACCOUNT_PASSWORD are not set on this stack",
    )
else:
    step(
        "the deployment publishes a demo credential",
        published.get("email") == DEMO,
        f"{status} email={published.get('email')} password_published={bool(published.get('password'))}",
    )
    status, body = visitor.call(
        "POST", "/auth/login", {"email": DEMO, "password": published.get("password")}
    )
    step("the demo credential signs in", status == 200, f"{status} role={body.get('user', {}).get('role')}")
    step("and is a viewer", body.get("user", {}).get("role") == "viewer", "")
    step("it can read the overview", visitor.call("GET", "/overview")[0] == 200, "")
    step("it can read the post-mortem queue", visitor.call("GET", "/post-mortems")[0] == 200, "")
    step("it cannot simulate an alert", visitor.call("POST", "/alerts/simulate", {})[0] == 403, "")
    step(
        "it cannot index a runbook",
        visitor.call("POST", "/knowledge/runbooks", {"title": "t", "content": "c" * 40})[0] == 403,
        "",
    )
    step("it cannot list users", visitor.call("GET", "/users")[0] == 403, "")
    step("it cannot delete an account", visitor.call("DELETE", "/users/1")[0] == 403, "")

failed = checks.count(False)
passed = len(checks) - failed
suffix = f", {len(skipped)} skipped" if skipped else ""
print(f"\n{passed}/{len(checks)} checks passed{suffix}")
raise SystemExit(1 if failed else 0)
