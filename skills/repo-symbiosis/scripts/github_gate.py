"""Verify a GitHub PR or merge-group candidate from trusted Git objects."""

import re

from . import agent_history, delivery, memory, privacy
from .repository import OID, read_json, require


POLICY = ".symbiosis/policy.json"
BACKLOG = ".symbiosis/backlog.json"
CHANGES = ".symbiosis/changes/"
BASE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._/-]{0,127}\Z")


def policy(value):
    memory.shape(value, ["schema", "base", "mode"], [], "release policy")
    require(type(value["schema"]) is int and value["schema"] == 1, "Unsupported policy schema")
    require(isinstance(value["base"], str) and BASE.fullmatch(value["base"]), "Invalid protected base branch")
    require(value["mode"] in ("cooperative", "github-app"), "Invalid policy mode")
    return value


def manifest(path, value):
    memory.shape(value, ["task", "run", "claim"], [], "change manifest")
    task = memory.valid_id(value["task"])
    memory.valid_id(value["run"])
    require(path == f"{CHANGES}{task}.json", "Change manifest path must match its task")
    require(isinstance(value["claim"], str) and delivery.CLAIM_ID.fullmatch(value["claim"]), "Invalid manifest claim ID")
    return value


def committed_json(repo, revision, path):
    entry = repo.tree(revision).get(path)
    require(entry and entry["mode"] == "100644" and entry["size"] <= 256 * 1024,
            f"Expected a small committed JSON file at {path}")
    return read_json(repo.blob(entry["blob"]), path)


def manifest_changes(repo, base, candidate):
    old, new = repo.tree(base), repo.tree(candidate)
    changes = {}
    for path in repo.changed(base, candidate):
        if path.startswith(CHANGES):
            require(path not in old and path in new, f"Historical change manifest was changed: {path}")
            changes[path] = manifest(path, committed_json(repo, candidate, path))
    return changes


def oid(value):
    require(isinstance(value, str) and OID.fullmatch(value), "Expected a full Git commit ID")
    return value


def claim_for(entry, claims, settings):
    claim = claims.get(entry["task"])
    require(claim and claim["claim"] == entry["claim"], f"No matching active reservation for {entry['task']}")
    if settings["mode"] == "github-app":
        require(claim.get("github"), "The GitHub App must issue the reservation")
    return claim


def verify_head(repo, base, head, entry, claim):
    roots = repo.git("merge-base", "--all", base, head).decode().splitlines()
    require(len(roots) == 1, "PR must have one merge base with the protected branch")
    start = roots[0]
    require(agent_history.ancestor(repo, claim["base"], start), "PR is missing its reservation base")
    expected = {f"{CHANGES}{entry['task']}.json": entry}
    require(manifest_changes(repo, start, head) == expected, "Each PR must add exactly its reserved change manifest")
    paths = repo.changed(start, head)
    outside = [path for path in paths if not delivery.includes(claim, path)]
    require(not outside, "Paths outside the reservation: " + ", ".join(map(repr, outside)))
    events = memory.load_committed(repo, head)
    run = memory.agent_runs(events).get(entry["run"])
    require(run and run[0]["task"] == entry["task"] and run[0]["agent"].get("branch") == claim["branch"],
            "The completed run must belong to the reserved task and branch")
    agent_history.verify_history(repo, start, head, [], [entry["run"]])
    privacy.reject(privacy.scan(repo, start, head)["findings"])
    return paths


def remote_head(repo, remote, branch):
    require(delivery.REMOTE.fullmatch(remote), "Use a configured Git remote name")
    ref = f"refs/heads/{branch}"
    result = repo.git("ls-remote", "--refs", remote, ref).decode().splitlines()
    require(len(result) == 1, f"Cannot resolve the active PR branch {branch}")
    head, observed = result[0].split("\t", 1)
    require(ref == observed, "Unexpected PR branch reference")
    return oid(head)


def run(repo, event_name, payload, candidate, remote, coordination_remote):
    pr = payload.get("pull_request") if event_name == "pull_request" else None
    group = payload.get("merge_group") if event_name == "merge_group" else None
    require(isinstance(pr or group, dict), "Expected a GitHub pull_request or merge_group payload")
    if pr:
        base = oid(pr["base"]["sha"])
        base_ref = pr["base"]["ref"]
        require(pr["base"]["repo"]["id"] == pr["head"]["repo"]["id"],
                "Symbiosis reservations require same-repository task branches")
    else:
        base = oid(group["base_sha"])
        base_ref = group["base_ref"].removeprefix("refs/heads/")
        require(oid(group["head_sha"]) == candidate, "Candidate differs from the GitHub merge group")
    settings = policy(committed_json(repo, base, POLICY))
    require(base_ref == settings["base"], "GitHub event targets a different protected branch")
    _, claims, _ = delivery.Ledger(repo, coordination_remote).read()
    changes = manifest_changes(repo, base, candidate)
    require(changes, "Candidate needs at least one reserved change manifest")
    if pr:
        require(len(changes) == 1, "Pull request candidate must contain one change manifest")
    heads, runs, permitted = [], [], set()
    for entry in changes.values():
        claim = claim_for(entry, claims, settings)
        if pr:
            require(pr["head"]["ref"] == claim["branch"], "PR head branch differs from the reservation")
            if settings["mode"] == "github-app":
                require(pr["user"]["id"] == claim["github"]["id"], "PR author differs from the verified claim holder")
            head = oid(pr["head"]["sha"])
        else:
            head = remote_head(repo, remote, claim["branch"])
        permitted.update(verify_head(repo, base, head, entry, claim))
        heads.append(head)
        runs.append(entry["run"])
    require(not (set(repo.changed(base, candidate)) - permitted), "Candidate changes paths outside its checked PRs")
    result = agent_history.verify_history(repo, base, candidate, heads, runs)
    privacy.reject(privacy.scan(repo, base, candidate)["findings"])
    return result
