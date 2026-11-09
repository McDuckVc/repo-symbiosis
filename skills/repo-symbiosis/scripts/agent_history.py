"""Append attributed agent journal commits and verify their Git ancestry."""

from . import delivery, memory
from .repository import require


def ancestor(repo, before, after):
    return repo.run("merge-base", "--is-ancestor", before, after).returncode == 0


def journal_origin(repo, revision, path):
    commits = repo.git("log", "--full-history", "--diff-filter=A", "--format=%H", revision, "--", path).decode().splitlines()
    require(len(commits) == 1, f"Expected one original journal commit for {path}. Check full history.")
    return commits[0]


def attribution(event):
    agent = event["agent"]
    return (
        f"Symbiosis-Task: {event['task']}",
        f"Symbiosis-Run: {agent['run']}",
        f"Symbiosis-Event: {event['id']}",
        f"Symbiosis-Agent: {event['actor']}",
        f"Symbiosis-Provider: {agent['provider']}",
    )


def journal_commit(repo, event, remote, claim_id, token=None):
    memory.validate(event, stored=False)
    require(event["kind"] == "agent", "agent-commit requires an agent event")
    task = event["task"]
    ledger = delivery.Ledger(repo, remote)
    # A lapsed lease that nobody has taken over can still be renewed by its holder.
    claim = ledger.task_claim(task, claim_id, allow_expired=True)
    ledger.authorize(claim, token, "record work for")
    branch = repo.git("symbolic-ref", "--short", "HEAD").decode().strip()
    require(branch == claim["branch"], f"Use the reserved branch {claim['branch']}")
    agent = event["agent"]
    require(agent.setdefault("branch", branch) == branch, "Agent event branch differs from the reservation")
    path = memory.event_path(event)
    require(delivery.includes(claim, path), f"Agent journal is outside the reservation: {path}")

    head = repo.revision("HEAD")
    require(ancestor(repo, claim["base"], head), "Task branch must descend from its reservation base")
    tree = repo.tree(head)
    events = memory.load(repo)
    previous = events.get(event["id"])
    if path in tree:
        committed = memory.stored_event(path, repo.blob(tree[path]["blob"]))
        require(previous == committed, f"Historical event modified or removed: {path}")
    for key in ("parent", "previous"):
        if key in agent:
            linked = events.get(agent[key])
            link_path = memory.event_path(linked) if linked else None
            require(link_path and link_path in tree
                    and linked == memory.stored_event(link_path, repo.blob(tree[link_path]["blob"])),
                    f"Commit the {key} event before appending {event['id']}")

    memory.record(repo, previous["revision"] if previous else head, event)
    if path in tree:
        result = {"branch": branch, "commit": journal_origin(repo, head, path), "path": path, "created": False}
    else:
        trailers = "\n".join(attribution(event))
        repo.git("add", "--", path)
        repo.git("commit", "--only", "-m", f"Record agent {agent['phase']} for {task}", "-m", trailers, "--", path)
        result = {"branch": branch, "commit": repo.revision("HEAD"), "path": path, "created": True}
    # Each journal entry renews the lease. A completed run holds the reservation until merge or release.
    lease = ledger.renew(task, claim_id, token, hold=agent["phase"] == "completed")
    return {**result, "lease_expires_at": lease.get("expires_at")}


def verify_history(repo, base, candidate, preserved, required_runs):
    require(ancestor(repo, base, candidate), "Integration candidate must contain the original protected base")
    for revision in preserved:
        require(ancestor(repo, revision, candidate), f"Integration candidate is missing original commit {revision}")
    old = memory.committed_paths(repo, base)
    current = memory.committed_paths(repo, candidate)
    for path, blob in old.items():
        require(path in current, f"Historical event removed: {path}")
        require(current[path] == blob, f"Historical event rewritten: {path}")

    events = memory.load_committed(repo, candidate)
    memory.project(events)
    added = [event for event in events.values() if event["kind"] == "agent" and memory.event_path(event) not in old]
    revisions = {event["revision"] for event in added}
    for revision in sorted(revisions):
        require(ancestor(repo, revision, candidate), f"Integration candidate is missing agent work commit {revision}")
    for event in added:
        path = memory.event_path(event)
        origin = journal_origin(repo, candidate, path)
        original_blob = repo.git("rev-parse", "--verify", f"{origin}:{path}").decode().strip()
        require(original_blob == current[path], f"Agent event changed after its original journal commit: {path}")
        parent = repo.revision(f"{origin}^")
        require(ancestor(repo, event["revision"], parent), f"Agent source commit must precede its journal event: {path}")
        for key in ("parent", "previous"):
            if key in event["agent"]:
                linked = memory.event_path(events[event["agent"][key]])
                original_link = repo.run("rev-parse", "--verify", f"{parent}:{linked}")
                require(original_link.returncode == 0 and original_link.stdout.decode().strip() == current[linked],
                        f"The {key} event must be committed before {path}")
        changed = repo.git("diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z", origin)
        require(changed == path.encode("ascii") + b"\0", f"Agent journal commit must add only its event: {path}")
        message = repo.git("-c", "i18n.logOutputEncoding=UTF-8", "show", "-s", "--format=%B", origin).decode("utf-8")
        require(set(attribution(event)) <= set(message.splitlines()), f"Missing journal commit attribution: {path}")
    runs = memory.agent_runs(events) if required_runs else {}
    for run in required_runs:
        memory.valid_id(run)
        require(run in runs and runs[run][-1] in added and runs[run][-1]["agent"]["phase"] == "completed",
                f"Integration candidate needs a new completed journal for agent run {run}")
    return {
        "base": base, "candidate": candidate, "preserved": preserved, "events": len(events),
        "new_agent_events": len(added), "completed_runs": sorted(set(required_runs)),
    }
