"""Verify a GitHub PR or merge-group candidate from trusted Git objects."""

import re

from . import agent_history, delivery, memory, privacy
from .repository import OID, SymbiosisError, read_json, require


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
    require(not delivery.expired(claim), f"Reservation for {entry['task']} expired at {claim.get('expires_at')}")
    if settings["mode"] == "github-app":
        require(claim.get("github"), "The GitHub App must issue the reservation")
    return claim


class Report:
    """Collect every gate problem, each with a fix, so an agent can repair them in one pass."""

    def __init__(self):
        self.problems = []
        self.warnings = []
        self.seen = set()

    def problem(self, check, message, fix=None, **details):
        self.problems.append({"check": check, "message": message, **details, **({"fix": fix} if fix else {})})

    def attempt(self, check, details, action, *args):
        try:
            return action(*args)
        except SymbiosisError as error:
            self.problem(check, str(error), **details)
            return None

    def privacy(self, findings, task=None):
        for finding in findings:
            key = (finding["path"], finding["line"], finding["rule"])
            if key in self.seen:
                continue
            self.seen.add(key)
            self.problem("privacy", f"Memory privacy check failed ({finding['rule']})",
                         "Remove the value from the memory file. Rotate it if it is a real credential.",
                         path=finding["path"], line=finding["line"], **({"task": task} if task else {}))


def journal_command(claim, remote):
    return f"symbiosis agent-commit <event.json> --remote {remote} --claim-id {claim['claim']}"


def verify_head(repo, base, head, entry, claim, report, remote="origin"):
    task = entry["task"]
    roots = repo.git("merge-base", "--all", base, head).decode().splitlines()
    if len(roots) != 1:
        report.problem("history", "PR must have one merge base with the protected branch",
                       "Merge the protected base into the task branch", task=task)
        return set()
    start = roots[0]
    if not agent_history.ancestor(repo, claim["base"], start):
        report.problem("history", "PR is missing its reservation base",
                       f"Merge reservation base {claim['base']} into {claim['branch']}", task=task)
    expected = {f"{CHANGES}{task}.json": entry}
    found = report.attempt("manifest", {"task": task}, manifest_changes, repo, start, head)
    if found is not None and found != expected:
        report.problem("manifest", "Each PR must add exactly its reserved change manifest",
                       f"Commit only {CHANGES}{task}.json, created with: symbiosis manifest {task} "
                       f"--run {entry['run']} --remote {remote} --claim-id {claim['claim']}", task=task)
    paths = repo.changed(start, head)
    outside = [path for path in paths if not delivery.includes(claim, path)]
    if outside:
        report.problem("scope", "Paths outside the reservation: " + ", ".join(map(repr, outside)),
                       delivery.extend_command(claim, outside, remote) + " (or revert them)", task=task, paths=outside)
    events = report.attempt("journal", {"task": task}, memory.load_committed, repo, head)
    runs = report.attempt("journal", {"task": task}, memory.agent_runs, events) if events is not None else None
    if runs is not None:
        run = runs.get(entry["run"])
        if not (run and run[0]["task"] == task and run[0]["agent"].get("branch") == claim["branch"]):
            report.problem("journal", "The completed run must belong to the reserved task and branch",
                           f"Record run {entry['run']} on {claim['branch']} with: {journal_command(claim, remote)}",
                           task=task)
        else:
            report.attempt("journal", {"task": task, "fix": "Append a completed journal event with: "
                                       + journal_command(claim, remote)},
                           agent_history.verify_history, repo, start, head, [], [entry["run"]])
    report.privacy(privacy.scan(repo, start, head)["findings"], task)
    return set(paths)


def remote_head(repo, remote, branch):
    require(delivery.REMOTE.fullmatch(remote), "Use a configured Git remote name")
    ref = f"refs/heads/{branch}"
    result = repo.git("ls-remote", "--refs", remote, ref).decode().splitlines()
    require(len(result) == 1, f"Cannot resolve the active PR branch {branch}")
    head, observed = result[0].split("\t", 1)
    require(ref == observed, "Unexpected PR branch reference")
    return oid(head)


def event_context(event_name, payload, candidate):
    pr = payload.get("pull_request") if event_name == "pull_request" else None
    group = payload.get("merge_group") if event_name == "merge_group" else None
    require(isinstance(pr or group, dict), "Expected a GitHub pull_request or merge_group payload")
    if pr:
        require(pr["base"]["repo"]["id"] == pr["head"]["repo"]["id"],
                "Symbiosis reservations require same-repository task branches")
        return pr, oid(pr["base"]["sha"]), pr["base"]["ref"]
    require(oid(group["head_sha"]) == candidate, "Candidate differs from the GitHub merge group")
    return None, oid(group["base_sha"]), group["base_ref"].removeprefix("refs/heads/")


def run(repo, event_name, payload, candidate, remote, coordination_remote):
    report = Report()
    try:
        pr, base, base_ref = event_context(event_name, payload, candidate)
        settings = policy(committed_json(repo, base, POLICY))
        require(base_ref == settings["base"], "GitHub event targets a different protected branch")
        _, claims, _ = delivery.Ledger(repo, coordination_remote).read()
        changes = manifest_changes(repo, base, candidate)
        require(changes, "Candidate needs at least one reserved change manifest")
        if pr:
            require(len(changes) == 1, "Pull request candidate must contain one change manifest")
    except SymbiosisError as error:
        report.problem("setup", str(error))
        return finish(report)
    except (KeyError, TypeError) as error:
        report.problem("setup", f"Malformed GitHub event payload: {error!r}")
        return finish(report)

    heads, runs, permitted = [], [], set()
    for entry in changes.values():
        task = entry["task"]
        claim = report.attempt("reservation", {"task": task, "fix": (
            f"Renew the reservation with: symbiosis renew-claim {task} --remote {coordination_remote} "
            f"--claim-id {entry['claim']}, or claim the task again and update its manifest")},
            claim_for, entry, claims, settings)
        if claim is None:
            continue
        if pr:
            if pr["head"]["ref"] != claim["branch"]:
                report.problem("branch", "PR head branch differs from the reservation",
                               f"Open the PR from {claim['branch']}", task=task)
            if settings["mode"] == "github-app" and pr["user"]["id"] != claim["github"]["id"]:
                report.problem("identity", "PR author differs from the verified claim holder",
                               f"Open the PR as @{claim['github']['login']} or transfer the reservation", task=task)
            head = oid(pr["head"]["sha"])
        else:
            head = report.attempt("branch", {"task": task}, remote_head, repo, remote, claim["branch"])
            if head is None:
                continue
        permitted.update(verify_head(repo, base, head, entry, claim, report, coordination_remote))
        heads.append(head)
        runs.append(entry["run"])

    result = None
    if len(heads) == len(changes):
        outside = sorted(set(repo.changed(base, candidate)) - permitted)
        if outside:
            report.problem("scope", "Candidate changes paths outside its checked PRs",
                           "Remove the changes or add them through a reserved PR", paths=outside)
        result = report.attempt("history", {}, agent_history.verify_history, repo, base, candidate, heads, runs)
    report.privacy(privacy.scan(repo, base, candidate)["findings"])
    stale = report.attempt("memory", {}, memory.outdated, repo, base, candidate)
    for row in (stale or {}).get("outdated", []):
        report.warnings.append({"check": "outdated-memory", "message": f"{row['event']} describes changed files",
                                "event": row["event"], "paths": row["paths"],
                                "fix": "Record a new analysis event that supersedes it"})
    return finish(report, result)


def finish(report, result=None):
    if report.problems:
        return {"ok": False, "problems": report.problems, "warnings": report.warnings}, 1
    return {"ok": True, **(result or {}), "warnings": report.warnings}, 0


def summary_markdown(value):
    def cell(text):
        return str(text).replace("|", "\\|").replace("\n", " ")

    lines = ["## Symbiosis gate: " + ("passed" if value["ok"] else f"{len(value['problems'])} problem(s)"), ""]
    for title, rows in (("Problems", value.get("problems", [])), ("Warnings", value.get("warnings", []))):
        if rows:
            lines += [f"### {title}", "", "| Check | Task | Problem | Fix |", "| --- | --- | --- | --- |"]
            lines += [f"| {cell(row['check'])} | {cell(row.get('task', ''))} | {cell(row['message'])} | "
                      f"{cell(row.get('fix', ''))} |" for row in rows]
            lines.append("")
    return "\n".join(lines) + "\n"
