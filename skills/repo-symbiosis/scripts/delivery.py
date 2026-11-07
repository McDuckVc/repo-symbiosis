"""Plan delivery waves and coordinate path reservations through a Git remote."""

import os
import re
import uuid
from datetime import datetime, timezone
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

from . import privacy
from .memory import items, required_text, shape, valid_id
from .repository import OID, SymbiosisError, json_bytes, path_name, read_json, require


REF = "refs/heads/symbiosis-coordination"
CLAIM_ID = re.compile(r"[a-f0-9]{32}\Z")
REMOTE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*\Z")
BRANCH = re.compile(r"symbiosis/[a-z0-9][a-z0-9-]{0,100}\Z")


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
    path = path.casefold()
    return any(path == item.casefold() or item.endswith("/") and path.startswith(item.casefold())
               for item in claim["paths"])


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
        for _ in range(6):
            old, state = self.state()
            result, changed = change(state["claims"], state["completed"])
            if not changed:
                return result
            content = json_bytes(state)
            privacy.reject(privacy.inspect("claims.json", content))
            blob = self.repo.git("hash-object", "-w", "--stdin", data=content).strip()
            tree = self.repo.git("mktree", data=b"100644 blob " + blob + b"\tclaims.json\n").decode().strip()
            parents = ["-p", old] if old else []
            commit = self.repo.git("commit-tree", tree, *parents, "-m", message).decode().strip()
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
        raise SymbiosisError("Coordination branch is busy. Retry the same request after checking current claims.")

    def claim(self, task, completed, owner, base, branch):
        required_text(owner, "claim owner", 100)
        require(BRANCH.fullmatch(branch), "Use a unique symbiosis/<task-and-session> branch")
        paths, resources = scope(task)
        requested = {"id": task["id"], "owner": owner, "base": base, "branch": branch,
                     "paths": sorted(paths), "resources": sorted(resources)}
        claim = {**requested, "claim": uuid.uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat()}

        def reserve(claims, merged):
            done = completed | merged.keys()
            dependencies = set(task.get("depends_on", []))
            require(task["id"] not in done, f"{task['id']} is already complete")
            require(not dependencies - done, f"{task['id']} has unfinished dependencies")
            for dependency in sorted(dependencies & merged.keys()):
                require(self.repo.run("merge-base", "--is-ancestor", merged[dependency], base).returncode == 0,
                        f"Reservation base does not contain {dependency}. Fetch the protected base and retry.")
            current = claims.get(task["id"])
            if current and all(current[key] == value for key, value in requested.items()):
                return current, False
            for other in claims.values():
                require(branch != other["branch"] and not conflicts(claim, other),
                        f"Reservation overlaps {other['id']} ({other['owner']})")
            claims[task["id"]] = claim
            return claim, True

        return self.update(reserve, f"Reserve {task['id']} ({claim['claim']})")

    def release(self, task_id, owner, claim_id, outcome, merge_revision):
        valid_id(task_id)
        required_text(owner, "claim owner", 100)
        require(CLAIM_ID.fullmatch(claim_id), "Invalid claim ID")
        require(outcome in ("merged", "abandoned") and bool(merge_revision) == (outcome == "merged"),
                "Merged outcomes need a merge revision")

        def finish(claims, merged):
            current = claims.get(task_id)
            if not current:
                return {"released": False, "id": task_id}, False
            require(current["claim"] == claim_id and current["owner"] == owner,
                    "Reservation changed or belongs to another session")
            if merge_revision:
                require(self.repo.run("merge-base", "--is-ancestor", current["base"], merge_revision).returncode == 0
                        and current["base"] != merge_revision, "Merge revision must descend from the reservation base")
                merged[task_id] = merge_revision
            del claims[task_id]
            return {"released": True, "id": task_id, "outcome": outcome}, True

        return self.update(finish, f"Release {task_id} ({claim_id}): {outcome}")

    def task_claim(self, task_id, claim_id):
        _, claims, _ = self.read()
        claim = claims.get(valid_id(task_id))
        require(claim and claim["claim"] == claim_id, "Task reservation is missing or has changed")
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
        shape(claim, ["id", "claim", "owner", "branch", "base", "paths", "resources", "created_at"], ["github"], "claim")
        require(valid_id(claim["id"]) == key, "Invalid coordination task ID")
        require(isinstance(claim["claim"], str) and CLAIM_ID.fullmatch(claim["claim"]), "Invalid claim ID")
        require(isinstance(claim["base"], str) and OID.fullmatch(claim["base"]), "Invalid claim base")
        require(isinstance(claim["branch"], str) and BRANCH.fullmatch(claim["branch"]), "Invalid task branch")
        required_text(claim["owner"], "claim owner", 100)
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


def check_scope(repo, claim, against):
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
    require(not outside, "Paths outside the reservation: " + ", ".join(map(repr, outside)))
    return {"base": base, "paths": paths}
