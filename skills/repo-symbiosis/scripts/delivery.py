"""Plan delivery waves and coordinate path reservations through a Git remote."""

import hashlib
import hmac
import os
import random
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

from . import privacy
from .memory import items, required_text, shape, valid_id
from .repository import OID, SymbiosisError, json_bytes, path_name, read_json, require


REF = "refs/heads/symbiosis-coordination"
CLAIM_ID = re.compile(r"[a-f0-9]{32}\Z")
REMOTE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*\Z")
BRANCH = re.compile(r"symbiosis/[a-z0-9][a-z0-9-]{0,100}\Z")
MAX_RECEIPTS = 1000
RECEIPT_REPLAY_WINDOW = timedelta(days=7)
DEFAULT_LEASE = "4h"
LEASE_LIMITS = (5 * 60, 30 * 86400)
DURATION = re.compile(r"([1-9][0-9]{0,5})([mhd])\Z")
TOKEN_HASH = re.compile(r"[0-9a-f]{64}\Z")
TOKEN_ENV = "SYMBIOSIS_CLAIM_TOKEN"
COORDINATION_DEADLINE = 90
sleep = time.sleep


def scope(task):
    paths = items(task, "paths")
    resources = items(task, "resources")
    require(paths, f"{task['id']} needs explicit write paths")
    for path in paths:
        require(isinstance(path, str), "Write paths must be strings")
        path_name(path.removesuffix("/"))
        require(not any(char in path for char in "*?[]"), "Write paths are literals or directories ending in /")
    for resource in resources:
        valid_id(resource)
    require(len(paths) == len(set(p.casefold() for p in paths)), "Duplicate write paths")
    require(len(resources) == len(set(resources)), "Duplicate resources")
    return paths, resources


def overlaps(first, second):
    first, second = first.casefold(), second.casefold()
    return (first.rstrip("/") == second.rstrip("/") or first.startswith(second.rstrip("/") + "/")
            or second.startswith(first.rstrip("/") + "/"))


def conflicts(first, second):
    if first["id"] == second["id"]:
        return True
    if set(first.get("resources", [])) & set(second.get("resources", [])):
        return True
    return any(overlaps(a, b) for a in first["paths"] for b in second["paths"])


def includes(claim, path):
    # Git paths are case-sensitive, so a reservation permits only the exact paths it names.
    # Overlap detection between claims stays case-insensitive to stay safe on case-folding checkouts.
    return any(path == item or item.endswith("/") and path.startswith(item) for item in claim["paths"])


def backlog(data):
    plan = read_json(data, "backlog")
    shape(plan, ["tasks"], ["completed"], "backlog")
    completed = [valid_id(value) for value in items(plan, "completed")]
    require(len(completed) == len(set(completed)), "Duplicate completed task")
    tasks = {}
    for task in items(plan, "tasks"):
        shape(task, ["id", "title", "paths"], ["resources", "depends_on", "acceptance", "priority"], "task")
        task_id = valid_id(task["id"])
        require(task_id not in tasks, f"Duplicate task {task_id}")
        required_text(task["title"], "task title", 200)
        scope(task)
        dependencies = [valid_id(value) for value in items(task, "depends_on")]
        require(len(dependencies) == len(set(dependencies)), f"Duplicate dependency on {task_id}")
        require(task_id not in dependencies, f"{task_id} depends on itself")
        for acceptance in items(task, "acceptance"):
            required_text(acceptance, "acceptance criterion", 1000)
        require(type(task.get("priority", 0)) is int, "Priority must be an integer")
        tasks[task_id] = task
    known = set(completed) | tasks.keys()
    for task in tasks.values():
        missing = set(task.get("depends_on", [])) - known
        require(not missing, f"{task['id']} has unknown dependencies: {', '.join(sorted(missing))}")
    pending = {key: set(task.get("depends_on", [])) - set(completed)
               for key, task in tasks.items() if key not in completed}
    try:
        TopologicalSorter(pending).prepare()
    except CycleError as error:
        raise SymbiosisError("Dependency cycle: " + " -> ".join(error.args[1])) from error
    return tasks, set(completed)


def schedule(tasks, completed, active, capacity):
    done = set(completed)
    remaining = sorted(tasks.keys() - done, key=lambda key: (-tasks[key].get("priority", 0), key))
    waves = []
    while remaining:
        wave = []
        for key in remaining:
            task = tasks[key]
            if set(task.get("depends_on", [])) - done:
                continue
            if any(conflicts(task, other) for other in active.values()):
                continue
            if any(conflicts(task, tasks[other]) for other in wave):
                continue
            wave.append(key)
            if len(wave) == capacity:
                break
        if not wave:
            break
        waves.append(wave)
        done.update(wave)
        remaining = [key for key in remaining if key not in done]
    blocked = []
    for key in remaining:
        task = tasks[key]
        blocked.append({
            "id": key,
            "dependencies": sorted(set(task.get("depends_on", [])) - done),
            "claims": sorted(other["id"] for other in active.values() if conflicts(task, other)),
        })
    return {"waves": waves, "blocked": blocked, "completed": sorted(completed)}


def receipt_time(row):
    try:
        moment = datetime.fromisoformat(row["recorded_at"])
    except (TypeError, KeyError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def prune_receipts(state, now=None):
    """Keep the newest receipts in the ledger head so it stays under its size limits.

    Pruned receipts remain in the coordination branch history. Receipts recorded within the replay
    window are kept even above the cap, because they still guard against duplicate webhook deliveries.
    """
    receipts = state.get("receipts")
    if not receipts or len(receipts) <= MAX_RECEIPTS:
        return 0
    now = now or datetime.now(timezone.utc)
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    ordered = sorted(receipts, key=lambda key: (receipt_time(receipts[key]) or oldest, key))
    removed = 0
    for key in ordered[:len(receipts) - MAX_RECEIPTS]:
        moment = receipt_time(receipts[key])
        if moment and now - moment < RECEIPT_REPLAY_WINDOW:
            break
        del receipts[key]
        removed += 1
    return removed


def utc_now():
    return datetime.now(timezone.utc)


def lease_seconds(text):
    """Parse a lease such as 30m, 4h or 2d. "none" means the reservation never expires."""
    if text in (None, "none"):
        return None
    match = DURATION.fullmatch(text) if isinstance(text, str) else None
    require(match, "Lease must look like 30m, 4h, 2d or none")
    seconds = int(match[1]) * {"m": 60, "h": 3600, "d": 86400}[match[2]]
    require(LEASE_LIMITS[0] <= seconds <= LEASE_LIMITS[1], "Lease must be between 5 minutes and 30 days")
    return seconds


def timestamp(value, label):
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise SymbiosisError(f"Invalid {label}") from error
    require(moment.tzinfo is not None, f"The {label} needs a timezone")
    return moment


def expired(claim, now=None):
    return "expires_at" in claim and timestamp(claim["expires_at"], "lease expiry") <= (now or utc_now())


def live(claims, now=None):
    now = now or utc_now()
    return {key: claim for key, claim in claims.items() if not expired(claim, now)}


def prune_expired(state, now=None):
    """Remove expired reservations. Every ledger write calls this, so expired claims never block new ones."""
    now = now or utc_now()
    gone = sorted(key for key, claim in state["claims"].items() if expired(claim, now))
    for key in gone:
        del state["claims"][key]
    return gone


def start_lease(claim, seconds, now=None):
    claim["lease"] = seconds
    claim["expires_at"] = ((now or utc_now()) + timedelta(seconds=seconds)).isoformat()


def digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_key(claim_id):
    return f"symbiosis.{claim_id}.token"


def backoff(attempt):
    """Full-jitter exponential backoff so many agents racing for the ledger spread out."""
    sleep(random.uniform(0, min(2.0, 0.05 * 2 ** attempt)))


class Ledger:
    def __init__(self, repo, remote):
        require(REMOTE.fullmatch(remote), "Use a configured Git remote name")
        fetch = repo.git("remote", "get-url", "--all", remote).splitlines()
        push = repo.git("remote", "get-url", "--push", "--all", remote).splitlines()
        require(len(fetch) == 1 and fetch == push, "Coordination requires one identical fetch and push URL")
        self.repo = repo
        self.remote = remote

    def state(self):
        listing = self.repo.git("ls-remote", "--refs", self.remote, REF).splitlines()
        if not listing:
            return None, {"schema": 1, "claims": {}, "completed": {}}
        require(len(listing) == 1, "Unexpected coordination ref lookup")
        head, ref = listing[0].decode().split("\t", 1)
        require(OID.fullmatch(head) and ref == REF, "Unexpected coordination ref")
        present = self.repo.run("cat-file", "-e", f"{head}^{{commit}}")
        if present.returncode:
            self.repo.git("fetch", "--no-tags", "--no-write-fetch-head", self.remote, head)
        tree = self.repo.tree(head)
        require(set(tree) == {"claims.json"} and tree["claims.json"]["mode"] == "100644",
                "Invalid coordination tree")
        return head, decode_state(self.repo.blob(tree["claims.json"]["blob"]))

    def read(self):
        head, state = self.state()
        return head, state["claims"], state["completed"]

    def update(self, change, message):
        deadline = time.monotonic() + COORDINATION_DEADLINE
        attempt = 0
        while True:
            old, state = self.state()
            result, changed = change(state["claims"], state["completed"])
            if not changed:
                return result
            cleared = prune_expired(state)
            prune_receipts(state)
            content = json_bytes(state)
            privacy.reject(privacy.inspect("claims.json", content))
            blob = self.repo.git("hash-object", "-w", "--stdin", data=content).strip()
            tree = self.repo.git("mktree", data=b"100644 blob " + blob + b"\tclaims.json\n").decode().strip()
            parents = ["-p", old] if old else []
            text = message + (f"\n\nCleared expired reservations: {', '.join(cleared)}" if cleared else "")
            commit = self.repo.git("commit-tree", tree, *parents, "-m", text).decode().strip()
            push = self.repo.run(
                "-c", f"remote.{self.remote}.mirror=false", "push", "--porcelain", "--no-follow-tags",
                "--recurse-submodules=no", self.remote, f"{commit}:{REF}",
            )
            if push.returncode == 0:
                return result
            latest, _ = self.state()
            require(latest != old and latest is not None,
                    "Coordination push failed. Check the remote, authentication, branch rules, and push hooks.")
            if old:
                require(self.repo.run("merge-base", "--is-ancestor", old, latest).returncode == 0,
                        "Coordination history was rewritten")
            attempt += 1
            require(time.monotonic() < deadline,
                    "Coordination branch is busy. Retry the same request after checking current claims.")
            backoff(attempt)

    def saved_token(self, claim_id):
        result = self.repo.run("config", "--local", "--get", token_key(claim_id))
        return result.stdout.decode().strip() if result.returncode == 0 else None

    def authorize(self, claim, token, action):
        """Reservations made with a holder token change only for a caller that presents it."""
        if "token_hash" not in claim:
            return
        candidates = [token, os.environ.get(TOKEN_ENV), self.saved_token(claim["claim"])]
        require(any(value and hmac.compare_digest(digest(value), claim["token_hash"]) for value in candidates),
                f"Only the session holding {claim['id']} can {action} it. Pass its --token, set {TOKEN_ENV}, "
                "or run from the clone that made the claim.")

    def claim(self, task, completed, owner, base, branch, lease=DEFAULT_LEASE):
        required_text(owner, "claim owner", 100)
        require(BRANCH.fullmatch(branch), "Use a unique symbiosis/<task-and-session> branch")
        paths, resources = scope(task)
        seconds = lease_seconds(lease)
        requested = {"id": task["id"], "owner": owner, "base": base, "branch": branch,
                     "paths": sorted(paths), "resources": sorted(resources)}
        token = secrets.token_hex(16)
        claim = {**requested, "claim": uuid.uuid4().hex, "created_at": utc_now().isoformat(),
                 "token_hash": digest(token)}
        if seconds:
            start_lease(claim, seconds)

        def reserve(claims, merged):
            done = completed | merged.keys()
            dependencies = set(task.get("depends_on", []))
            require(task["id"] not in done, f"{task['id']} is already complete")
            require(not dependencies - done, f"{task['id']} has unfinished dependencies")
            for dependency in sorted(dependencies & merged.keys()):
                require(self.repo.run("merge-base", "--is-ancestor", merged[dependency], base).returncode == 0,
                        f"Reservation base does not contain {dependency}. Fetch the protected base and retry.")
            current = claims.get(task["id"])
            if current and not expired(current) and all(current[key] == value for key, value in requested.items()):
                return current, False
            for other in live(claims).values():
                require(branch != other["branch"] and not conflicts(claim, other),
                        f"Reservation overlaps {other['id']} ({other['owner']})")
            claims[task["id"]] = claim
            return claim, True

        # Save the token before publishing so a crash after the push cannot lose it.
        key = token_key(claim["claim"])
        self.repo.git("config", "--local", key, token)
        stored = False
        try:
            result = self.update(reserve, f"Reserve {task['id']} ({claim['claim']})")
            stored = result["claim"] == claim["claim"]
        finally:
            if not stored:
                self.repo.run("config", "--local", "--unset", key)
        saved = self.saved_token(result["claim"])
        return {**result, **({"token": saved} if saved else {})}

    def renew(self, task_id, claim_id, token=None, lease=None, hold=False):
        valid_id(task_id)
        require(CLAIM_ID.fullmatch(claim_id), "Invalid claim ID")
        seconds = lease_seconds(lease) if lease else None

        def change(claims, merged):
            current = claims.get(task_id)
            require(current and current["claim"] == claim_id,
                    f"Reservation for {task_id} is missing or has changed. Claim the task again.")
            self.authorize(current, token, "renew")
            if hold:
                if "expires_at" not in current:
                    return current, False
                del current["lease"], current["expires_at"]
                return current, True
            duration = seconds or current.get("lease")
            if duration is None:
                return current, False
            start_lease(current, duration)
            return current, True

        action = "Hold" if hold else "Renew"
        return self.update(change, f"{action} {task_id} ({claim_id})")

    def extend(self, task_id, claim_id, paths, resources, token=None):
        valid_id(task_id)
        require(CLAIM_ID.fullmatch(claim_id), "Invalid claim ID")
        require(paths or resources, "Name at least one --path or --resource to add")

        def change(claims, merged):
            current = claims.get(task_id)
            require(current and current["claim"] == claim_id,
                    f"Reservation for {task_id} is missing or has changed. Claim the task again.")
            self.authorize(current, token, "extend")
            added = [path for path in dict.fromkeys(paths) if not includes(current, path)]
            extra = [name for name in dict.fromkeys(resources) if name not in current["resources"]]
            if not added and not extra:
                return current, False
            candidate = {**current, "paths": sorted(current["paths"] + added),
                         "resources": sorted(current["resources"] + extra)}
            scope(candidate)
            for other in live(claims).values():
                if other["id"] != task_id:
                    require(not conflicts(candidate, other), f"Extension overlaps {other['id']} ({other['owner']})")
            if "lease" in candidate:
                start_lease(candidate, candidate["lease"])
            claims[task_id] = candidate
            return candidate, True

        return self.update(change, f"Extend {task_id} ({claim_id})")

    def release(self, task_id, owner, claim_id, outcome, merge_revision, token=None, override=None):
        valid_id(task_id)
        required_text(owner, "claim owner", 100)
        require(CLAIM_ID.fullmatch(claim_id), "Invalid claim ID")
        require(outcome in ("merged", "abandoned") and bool(merge_revision) == (outcome == "merged"),
                "Merged outcomes need a merge revision")
        if override is not None:
            required_text(override, "override reason", 500)

        def finish(claims, merged):
            current = claims.get(task_id)
            if not current:
                return {"released": False, "id": task_id}, False
            require(current["claim"] == claim_id, "Reservation changed or belongs to another session")
            if override:
                require("expires_at" not in current or expired(current),
                        "A reservation with a running lease can only be released by its holder. Wait for it to expire.")
            else:
                require(current["owner"] == owner, "Reservation changed or belongs to another session")
                if not expired(current):
                    self.authorize(current, token, "release")
            if merge_revision:
                require(self.repo.run("merge-base", "--is-ancestor", current["base"], merge_revision).returncode == 0
                        and current["base"] != merge_revision, "Merge revision must descend from the reservation base")
                merged[task_id] = merge_revision
            del claims[task_id]
            return {"released": True, "id": task_id, "outcome": outcome}, True

        note = f"\n\nOverride by {owner}: {override}" if override else ""
        result = self.update(finish, f"Release {task_id} ({claim_id}): {outcome}{note}")
        if result["released"]:
            self.repo.run("config", "--local", "--unset", token_key(claim_id))
        return result

    def task_claim(self, task_id, claim_id, allow_expired=False):
        _, claims, _ = self.read()
        claim = claims.get(valid_id(task_id))
        require(claim and claim["claim"] == claim_id, "Task reservation is missing or has changed")
        require(allow_expired or not expired(claim),
                f"Reservation for {task_id} expired at {claim.get('expires_at')}. "
                "Renew it with renew-claim before continuing.")
        return claim


def decode_state(content):
    state = read_json(content, "coordination state")
    shape(state, ["schema", "claims", "completed"], ["receipts"], "coordination state")
    require(type(state["schema"]) is int and state["schema"] == 1, "Unsupported coordination schema")
    require(isinstance(state["claims"], dict) and isinstance(state["completed"], dict), "Invalid coordination state")
    require(isinstance(state.get("receipts", {}), dict), "Invalid coordination receipts")
    require(not state["completed"].keys() & state["claims"].keys(), "Completed tasks cannot have active claims")
    for task_id, revision in state["completed"].items():
        valid_id(task_id)
        require(isinstance(revision, str) and OID.fullmatch(revision), "Invalid merged task revision")
    known = []
    for key, claim in state["claims"].items():
        shape(claim, ["id", "claim", "owner", "branch", "base", "paths", "resources", "created_at"],
              ["github", "lease", "expires_at", "token_hash"], "claim")
        require(valid_id(claim["id"]) == key, "Invalid coordination task ID")
        require(isinstance(claim["claim"], str) and CLAIM_ID.fullmatch(claim["claim"]), "Invalid claim ID")
        require(isinstance(claim["base"], str) and OID.fullmatch(claim["base"]), "Invalid claim base")
        require(isinstance(claim["branch"], str) and BRANCH.fullmatch(claim["branch"]), "Invalid task branch")
        required_text(claim["owner"], "claim owner", 100)
        if "lease" in claim or "expires_at" in claim:
            require(type(claim.get("lease")) is int and LEASE_LIMITS[0] <= claim["lease"] <= LEASE_LIMITS[1]
                    and "expires_at" in claim, "Invalid reservation lease")
            timestamp(claim["expires_at"], "lease expiry")
        if "token_hash" in claim:
            require(isinstance(claim["token_hash"], str) and TOKEN_HASH.fullmatch(claim["token_hash"]),
                    "Invalid reservation token hash")
        if "github" in claim:
            identity = claim["github"]
            shape(identity, ["id", "login"], [], "GitHub claim identity")
            require(type(identity["id"]) is int and identity["id"] > 0, "Invalid GitHub user ID")
            required_text(identity["login"], "GitHub login", 100)
        scope(claim)
        require(all(claim["branch"] != other["branch"] and claim["claim"] != other["claim"]
                    and not conflicts(claim, other) for other in known), "Overlapping claims in ledger")
        known.append(claim)
    return state


def add_worktree(repo, claim, destination):
    path = Path(destination).resolve()
    require(not path.exists(), f"Worktree destination already exists: {path}")
    repo.git("worktree", "add", "-b", claim["branch"], "--", str(path), claim["base"])
    return {"branch": claim["branch"], "base": claim["base"], "worktree": str(path)}


def check_scope(repo, claim, against, remote="origin"):
    branch = repo.git("symbolic-ref", "--short", "HEAD").decode().strip()
    require(branch == claim["branch"], f"Use the reserved branch {claim['branch']}")
    base = repo.revision(against or claim["base"])
    require(repo.run("merge-base", "--is-ancestor", claim["base"], base).returncode == 0
            and repo.run("merge-base", "--is-ancestor", base, "HEAD").returncode == 0,
            "Comparison base must descend from the reservation base and be an ancestor of HEAD")
    differences = (
        repo.git("diff", "--name-only", "--no-renames", "-z", base, "--")
        + repo.git("diff", "--cached", "--name-only", "--no-renames", "-z", base, "--")
        + repo.git("ls-files", "--others", "--exclude-standard", "-z")
    )
    paths = sorted({os.fsdecode(path) for path in differences.split(b"\0") if path})
    outside = [path for path in paths if not includes(claim, path)]
    require(not outside, "Paths outside the reservation: " + ", ".join(map(repr, outside))
            + ". Reserve them with: " + extend_command(claim, outside, remote) + " (or revert them)")
    return {"base": base, "paths": paths}


def extend_command(claim, paths, remote="origin"):
    flags = " ".join(f"--path {quote_arg(path)}" for path in paths)
    return f"symbiosis extend-claim {claim['id']} --remote {remote} --claim-id {claim['claim']} {flags}"


def quote_arg(value):
    return value if re.fullmatch(r"[A-Za-z0-9_./-]+", value) else "'" + value.replace("'", "'\\''") + "'"
