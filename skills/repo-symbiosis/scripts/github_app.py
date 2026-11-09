"""Apply authenticated GitHub events to the shared task ledger."""

import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

from . import delivery, github_gate
from .github_api import APIError
from .repository import read_json, require


LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
COMMAND = re.compile(r"/symbiosis (claim|abandon|transfer @([A-Za-z0-9][A-Za-z0-9-]{0,38}))\Z")
WRITERS = {"write", "maintain", "admin"}
MANAGERS = {"maintain", "admin"}


def now():
    return datetime.now(timezone.utc).isoformat()


def positive(value):
    require(type(value) is int and value > 0, "Expected a positive GitHub ID")
    return value


def identity(user):
    require(isinstance(user["login"], str) and LOGIN.fullmatch(user["login"]), "Invalid GitHub login")
    return {"id": positive(user["id"]), "login": user["login"]}


def selected_job(event, payload, allowed):
    action = payload.get("action")
    details = {}
    if event == "issue_comment" and action == "created":
        issue = payload["issue"]
        comment = payload["comment"]
        match = COMMAND.fullmatch(comment["body"].strip())
        if not match or "pull_request" in issue or issue.get("state") != "open":
            return None
        author = identity(comment["user"])
        require(author == identity(payload["sender"]), "Command sender differs from the comment author")
        details = {"issue": positive(issue["number"]), "comment": positive(comment["id"]),
                   "actor": author, "command": "transfer" if match[2] else match[1], "target": match[2]}
    elif event == "pull_request" and action == "closed":
        details = {"pr": positive(payload["number"])}
    elif event == "deployment_status":
        details = {"deployment": positive(payload["deployment"]["id"]),
                   "status": positive(payload["deployment_status"]["id"])}
    else:
        return None
    repository = payload["repository"]
    repo_id = positive(repository["id"])
    require(repo_id in allowed, "Repository is outside the App allowlist")
    name = repository["full_name"]
    base = repository["default_branch"]
    require(isinstance(name, str) and REPOSITORY.fullmatch(name), "Invalid GitHub repository")
    require(isinstance(base, str) and github_gate.BASE.fullmatch(base), "Invalid GitHub default branch")
    return {"event": event, "repo": name, "repo_id": repo_id, "base": base,
            "installation": positive(payload["installation"]["id"]), **details}


def receipt(state, key, details):
    receipts = state.setdefault("receipts", {})
    if key in receipts:
        return False
    receipts[key] = {**details, "recorded_at": now()}
    return True


def can(user, roles):
    return user["permission"] in roles or user["role"] in roles


def command(api, job, base):
    actor = api.user(job["actor"]["login"])
    require(identity(actor)["id"] == job["actor"]["id"] and can(actor, WRITERS),
            "GitHub write access is required to issue a task command")
    task_id = f"issue-{job['issue']}"
    key = f"comment-{job['comment']}"
    kind = job["command"]
    if kind == "claim":
        tasks, already_done = delivery.backlog(api.file(github_gate.BACKLOG, base))
        require(task_id in tasks, "Add this issue to the protected backlog first")
        task = tasks[task_id]
        branch = f"symbiosis/{task_id}-{uuid.uuid4().hex[:10]}"
        proposed = {"id": task_id, "owner": f"github:{actor['login']}", "github": identity(actor),
                    "base": base, "paths": sorted(task["paths"]), "resources": sorted(task.get("resources", [])),
                    "branch": branch, "claim": uuid.uuid4().hex, "created_at": now()}
    elif kind == "transfer":
        require(can(actor, MANAGERS), "A maintainer must transfer the reservation")
        target = api.user(job["target"])
        require(can(target, WRITERS), "The new holder needs repository write access")
        replacement = uuid.uuid4().hex

    def apply(state):
        if key in state.get("receipts", {}):
            return False
        claims, done = state["claims"], state["completed"]
        existing = claims.get(task_id)
        if kind == "claim":
            if existing and existing.get("github", {}).get("id") == actor["id"]:
                return False
            require(task_id not in already_done and task_id not in done, "Task is already merged")
            dependencies = set(task.get("depends_on", []))
            require(not dependencies - (already_done | done.keys()), "Task prerequisites are still open")
            for dependency in sorted(dependencies & done.keys()):
                require(api.contains(done[dependency], base), "A prerequisite merge is missing from the protected base")
            require(not any(delivery.conflicts(proposed, claim) for claim in delivery.live(claims).values()),
                    "Task overlaps an active reservation")
            claims[task_id] = proposed
        elif existing:
            holder = existing.get("github", {}).get("id")
            require(holder is not None, "Resolve legacy cooperative reservations before switching modes")
            if kind == "transfer":
                existing.update(github=identity(target), owner=f"github:{target['login']}", claim=replacement)
            else:
                require(holder == actor["id"] or can(actor, MANAGERS), "Only the holder or a maintainer can abandon work")
                owner = job["repo"].split("/", 1)[0]
                params = urlencode({"state": "open", "head": f"{owner}:{existing['branch']}", "per_page": 1})
                require(not api.call("GET", "/pulls?" + params), "Close the active pull request before abandoning the claim")
                del claims[task_id]
        else:
            return False
        return receipt(state, key, {"kind": kind, "task": task_id, "issue": job["issue"], "actor": identity(actor),
                                    "claim": claims.get(task_id, existing)["claim"]})

    api.update(apply, f"Symbiosis {kind} for {task_id} ({key})")


def close_pr(api, job, base):
    number = job["pr"]
    pr = api.call("GET", f"/pulls/{number}")
    if (pr["state"] != "closed" or pr["base"]["ref"] != job["base"]
            or pr["base"]["repo"]["id"] != job["repo_id"]
            or (pr["head"].get("repo") or {}).get("id") != job["repo_id"]):
        return
    _, state = api.ledger()
    active = next((claim for claim in state["claims"].values() if claim["branch"] == pr["head"]["ref"]), None)
    if not active or active.get("github", {}).get("id") != pr["user"]["id"]:
        return
    head = github_gate.oid(pr["head"]["sha"])
    path = f"{github_gate.CHANGES}{active['id']}.json"
    try:
        entry = github_gate.manifest(path, read_json(api.file(path, head), path))
    except APIError as error:
        if error.status == 404:
            return
        raise
    if entry["claim"] != active["claim"]:
        return
    outcome = "merged" if pr["merged"] else "abandoned"
    revision = github_gate.oid(pr["merge_commit_sha"]) if pr["merged"] else None
    if revision:
        require(api.contains(head, revision) and api.contains(revision, base),
                "GitHub merge does not preserve the approved head on the protected base")
    key = f"pr-{number}-{outcome}-{entry['claim']}"

    def apply(current):
        if key in current.get("receipts", {}) or current["claims"].get(entry["task"]) != active:
            return False
        del current["claims"][entry["task"]]
        if revision:
            current["completed"][entry["task"]] = revision
        return receipt(current, key, {"kind": outcome, "task": entry["task"], "run": entry["run"],
                                      "pr": number, "head": head, "merge": revision, "actor": active["github"]})

    api.update(apply, f"Symbiosis reconcile PR #{number} as {outcome}")


def deploy(api, job, base):
    deployment = job["deployment"]
    status = job["status"]
    details = api.call("GET", f"/deployments/{deployment}")
    result = api.call("GET", f"/deployments/{deployment}/statuses/{status}")
    if result["state"] not in ("success", "failure", "error", "inactive"):
        return
    revision = github_gate.oid(details["sha"])
    if not api.contains(revision, base):
        return
    environment = details["environment"]
    require(isinstance(environment, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. /-]{0,99}", environment),
            "Deployment environment label is outside the supported format")
    key = f"deployment-{deployment}-{status}"
    api.update(lambda state: receipt(state, key, {
        "kind": "deployment", "deployment": deployment, "status": status, "revision": revision,
        "environment": environment, "result": result["state"],
    }), f"Symbiosis record deployment {deployment} status {status}")


def process(api, job):
    base = api.ref(job["base"])
    settings = github_gate.policy(read_json(api.file(github_gate.POLICY, base), github_gate.POLICY))
    require(settings["base"] == job["base"] and settings["mode"] == "github-app",
            "Repository needs a protected GitHub App policy on its default branch")
    if job["event"] == "issue_comment":
        command(api, job, base)
    elif job["event"] == "pull_request":
        close_pr(api, job, base)
    else:
        deploy(api, job, base)
