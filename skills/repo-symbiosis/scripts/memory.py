"""Store immutable project events and retrieve their current graph and history."""

import json
import os
import re
import tempfile
from collections import defaultdict, deque
from datetime import datetime, timezone
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from urllib.parse import urlsplit

from . import privacy
from .repository import MEMORY, OID, SymbiosisError, classify, json_bytes, path_name, read_json, require


EVENT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,79}\Z")
NODE_ID = re.compile(r"[a-z][a-z0-9_-]*:[^\s\x00-\x1f\x7f]{1,240}\Z")
KINDS = {"analysis", "agent", "decision", "task", "failure", "release", "retirement"}
RUNS = f"{MEMORY}/agent-runs"
TERMINAL_PHASES = {"handoff", "completed"}


def valid_id(value):
    require(isinstance(value, str) and EVENT_ID.fullmatch(value), f"Invalid ID: {value!r}")
    return value


def required_text(value, label, limit=4000):
    require(isinstance(value, str) and value.strip() and len(value) <= limit,
            f"{label} must be a nonempty string of up to {limit} characters")
    return value


def shape(value, required, optional, label):
    require(isinstance(value, dict) and set(required) <= value.keys()
            and value.keys() <= set(required) | set(optional), f"Invalid fields for {label}")


def items(value, key):
    result = value.get(key, [])
    require(isinstance(result, list), f"{key} must be an array")
    return result


def validate_agent(event):
    agent = event.get("agent")
    shape(agent, ["run", "phase", "provider", "rationale"], ["previous", "parent", "branch", "checks", "handoff"], "agent")
    run = valid_id(agent["run"])
    prefix = f"agent-{run}-"
    require(event["id"].startswith(prefix) and re.fullmatch(r"[0-9]{4}", event["id"][len(prefix):]),
            f"Agent event ID must use {prefix}<four-digit sequence>")
    sequence = int(event["id"][-4:])
    phase = agent["phase"]
    require(phase in ("started", "checkpoint", "blocked", "handoff", "completed"), "Invalid agent phase")
    require(sequence >= 1 and (sequence == 1) == (phase == "started"), "Sequence 0001 must start the agent run")
    require("task" in event and "actor" in event, "Agent events require a task and actor")
    actor = event["actor"]
    require(actor == actor.strip() and len(actor.splitlines()) == 1 and not re.search(r"[\x00-\x1f\x7f]", actor),
            "Agent actor must be a trimmed single line")
    valid_id(agent["provider"])
    required_text(agent["rationale"], "agent rationale")
    if "branch" in agent:
        require(isinstance(agent["branch"], str) and re.fullmatch(r"symbiosis/[a-z0-9][a-z0-9-]{0,100}", agent["branch"]),
                "Invalid agent task branch")
    if phase == "started":
        require("previous" not in agent, "A run starts without a previous event")
        if "parent" in agent:
            valid_id(agent["parent"])
    else:
        require("parent" not in agent and agent.get("previous") == f"{prefix}{sequence - 1:04}",
                "Provide the preceding event ID. Parent links belong on the start event.")
    checks = items(agent, "checks")
    require(len(checks) <= 20, "Record up to 20 check summaries per event")
    for check in checks:
        required_text(check, "check summary", 500)
    if phase == "completed":
        require(checks, "Completion needs at least one verification summary")
    if phase in ("blocked", "handoff") or "handoff" in agent:
        required_text(agent.get("handoff"), "handoff and next steps")


def validate(event, stored):
    required = ["id", "kind", "summary"]
    if stored:
        required += ["schema", "revision", "recorded_at"]
    shape(event, required, ["actor", "task", "status", "release", "agent", "evidence", "nodes", "edges", "supersedes", "links"], "event")
    valid_id(event["id"])
    require(isinstance(event["kind"], str) and event["kind"] in KINDS, "Unknown event kind")
    required_text(event["summary"], "summary")
    if stored:
        require(type(event["schema"]) is int and event["schema"] == 1, "Unsupported event schema")
        require(isinstance(event["revision"], str) and OID.fullmatch(event["revision"]), "Invalid revision")
        try:
            parsed = datetime.fromisoformat(event["recorded_at"])
            require(parsed.tzinfo is not None, "Event timestamp needs a timezone")
        except (TypeError, ValueError) as error:
            raise SymbiosisError("Invalid event timestamp") from error
    if "actor" in event:
        required_text(event["actor"], "actor", 100)
    if "task" in event:
        valid_id(event["task"])
    if event["kind"] == "task":
        require("task" in event and event.get("status") in (
            "planned", "claimed", "draft", "review-ready", "merged", "abandoned"
        ), "Task events require a task ID and valid status")
        if event["status"] == "merged":
            require(items(event, "links"), "Merged task events need a PR link")
    else:
        require("status" not in event, "status is reserved for task events")
    if event["kind"] == "release":
        release = event.get("release")
        shape(release, ["phase", "digest", "environment"], ["previous"], "release")
        require(release["phase"] in ("frozen", "staged", "promoted"), "Invalid release phase")
        require(isinstance(release["digest"], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", release["digest"]),
                "Release needs a SHA-256 artifact digest")
        valid_id(release["environment"])
        if release["phase"] == "frozen":
            require("previous" not in release, "A frozen candidate starts a release chain")
        else:
            valid_id(release.get("previous"))
            require(items(event, "links"), "Staging and production events require deployment evidence links")
    else:
        require("release" not in event, "release is reserved for release events")
    replaces = [valid_id(value) for value in items(event, "supersedes")]
    require(len(replaces) == len(set(replaces)) and event["id"] not in replaces, "Invalid supersedes list")
    if event["kind"] == "agent":
        require(not replaces, "Agent events are append-only")
        validate_agent(event)
    else:
        require("agent" not in event, "agent is reserved for agent events")

    nodes = items(event, "nodes")
    edges = items(event, "edges")
    evidence = items(event, "evidence")
    links = items(event, "links")
    for source in evidence:
        shape(source, ["path", "blob"] if stored else ["path"], ["start", "end", "reviewed"], "evidence")
        path_name(source["path"])
        if stored:
            require(isinstance(source["blob"], str) and OID.fullmatch(source["blob"]), "Invalid evidence blob")
        if "reviewed" in source:
            require(type(source["reviewed"]) is bool, "reviewed must be a boolean")
        if "start" in source or "end" in source:
            start = source.get("start")
            end = source.get("end")
            require(type(start) is int and type(end) is int and 1 <= start <= end, "Invalid evidence lines")
    for node in nodes:
        shape(node, ["id", "title"], ["summary"], "node")
        require(isinstance(node["id"], str) and NODE_ID.fullmatch(node["id"]), "Invalid graph node ID")
        required_text(node["title"], "node title", 200)
        if "summary" in node:
            required_text(node["summary"], "node summary")
    for edge in edges:
        shape(edge, ["from", "to", "type"], [], "edge")
        for key in ("from", "to"):
            require(isinstance(edge[key], str) and NODE_ID.fullmatch(edge[key]), "Invalid graph edge endpoint")
        valid_id(edge["type"])
    for link in links:
        url = urlsplit(required_text(link, "link", 2000))
        require(url.scheme == "https" and url.hostname and not (url.username or url.password or url.query),
                "Evidence links must use HTTPS and omit credentials and query strings")
    if event["kind"] == "analysis" or nodes or edges:
        require(evidence or links, "Graph facts require evidence")
    if event["kind"] == "retirement":
        require(replaces and not (nodes or edges), "Retirement needs superseded events and no graph assertions")


def event_path(event):
    directory = f"{RUNS}/{event['agent']['run']}" if event["kind"] == "agent" else MEMORY
    return f"{directory}/{event['id']}.json"


def stored_event(path, content):
    event = read_json(content, path)
    validate(event, stored=True)
    require(path == event_path(event), f"Memory location and event ID differ: {path}")
    return event


def event_map(records):
    events = {}
    for path, content in records:
        event = stored_event(path, content)
        require(event["id"] not in events, f"Duplicate event ID: {event['id']}")
        events[event["id"]] = event
    return events


def load(repo):
    directory = repo.root / MEMORY
    require(directory.resolve() == directory, f"{MEMORY} must be a real directory in the repository")
    if not directory.exists():
        return {}
    require(directory.is_dir(), f"{MEMORY} must be a directory")
    directories = [directory]
    runs = repo.root / RUNS
    if runs.exists() or runs.is_symlink():
        require(runs.resolve() == runs and runs.is_dir(), f"{RUNS} must be a real directory")
        for run in sorted(runs.iterdir()):
            valid_id(run.name)
            require(run.resolve() == run and run.is_dir(), f"Invalid agent run directory: {run.name}")
            directories.append(run)

    def records():
        for folder in directories:
            for path in sorted(folder.iterdir()):
                if path == runs or path.name.startswith(".pending-"):
                    continue
                require(path.suffix == ".json" and path.is_file() and not path.is_symlink(),
                        f"Invalid memory file: {path.name}")
                yield path.relative_to(repo.root).as_posix(), path.read_bytes()

    return event_map(records())


def committed_paths(repo, revision):
    records = {}
    for path, entry in repo.tree(revision).items():
        if path.startswith(f"{MEMORY}/"):
            parts = path.removeprefix(f"{MEMORY}/").split("/")
            require((len(parts) == 1 or len(parts) == 3 and parts[0] == "agent-runs"
                     and EVENT_ID.fullmatch(parts[1])) and parts[-1].endswith(".json") and entry["mode"] == "100644",
                    f"Invalid committed memory path: {path}")
            records[path] = entry["blob"]
    return records


def load_committed(repo, revision):
    records = committed_paths(repo, revision)
    blobs = repo.blobs(records.values())
    return event_map((path, blobs[oid]) for path, oid in sorted(records.items()))


def agent_runs(events):
    runs = defaultdict(list)
    links = {}
    for event in events.values():
        if event["kind"] != "agent":
            continue
        event_id = event["id"]
        agent = event["agent"]
        run = agent["run"]
        runs[run].append(event)
        references = []
        if "parent" in agent:
            parent = events.get(agent["parent"], {}).get("agent")
            require(parent and parent["run"] != run, f"Invalid delegation parent for {event_id}")
            references.append(agent["parent"])
        if "previous" in agent:
            previous = events.get(agent["previous"], {})
            prior = previous.get("agent")
            require(prior and prior["run"] == run and prior["phase"] not in TERMINAL_PHASES
                    and all(prior.get(key) == agent.get(key) for key in ("provider", "branch"))
                    and all(previous.get(key) == event.get(key) for key in ("actor", "task")),
                    f"Invalid previous event for {event_id}")
            references.append(agent["previous"])
        links[event_id] = references
    try:
        TopologicalSorter(links).prepare()
    except CycleError as error:
        raise SymbiosisError("Agent delegation cycle: " + " -> ".join(error.args[1])) from error
    return {run: sorted(records, key=lambda event: event["id"]) for run, records in runs.items()}


def active_events(events):
    superseded = {}
    for event in events.values():
        for previous in items(event, "supersedes"):
            require(previous in events, f"{event['id']} supersedes missing event {previous}")
            require(events[previous]["kind"] != "agent", f"Agent journal event cannot be superseded: {previous}")
            require(previous not in superseded, f"Concurrent replacements for {previous}")
            superseded[previous] = event["id"]
    checked = set()
    for start in superseded:
        trail = set()
        event_id = start
        while event_id in superseded and event_id not in checked:
            require(event_id not in trail, f"Supersession cycle involving {event_id}")
            trail.add(event_id)
            event_id = superseded[event_id]
        checked.update(trail)
    return {event_id: event for event_id, event in events.items() if event_id not in superseded}


def project(events):
    agent_runs(events)
    nodes = {}
    edges = {}
    for event in events.values():
        release = event.get("release")
        if not release or "previous" not in release:
            continue
        parent = events.get(release["previous"], {})
        prior = parent.get("release", {})
        phase = {"staged": "frozen", "promoted": "staged"}[release["phase"]]
        require(prior.get("phase") == phase and prior.get("digest") == release["digest"]
                and parent["revision"] == event["revision"], f"Release evidence mismatch in {event['id']}")
    for event_id, event in active_events(events).items():
        for node in items(event, "nodes"):
            row = nodes.setdefault(node["id"], {**node, "events": []})
            require({k: v for k, v in row.items() if k != "events"} == node,
                    f"Conflicting definitions for {node['id']}")
            if event_id not in row["events"]:
                row["events"].append(event_id)
        for edge in items(event, "edges"):
            key = (edge["from"], edge["type"], edge["to"])
            row = edges.setdefault(key, {**edge, "events": []})
            if event_id not in row["events"]:
                row["events"].append(event_id)
    for source, relation, target in edges:
        require(source in nodes and target in nodes, f"Dangling graph edge: {source} {relation} {target}")
    return nodes, edges


def record(repo, revision, event):
    validate(event, stored=False)
    path_to_event = event_path(event)
    destination = repo.root / path_to_event
    directory = destination.parent
    events = load(repo)
    tree = repo.tree(revision)
    line_counts = {}
    for source in items(event, "evidence"):
        path = source["path"]
        require(path in tree, f"Evidence path absent from {revision[:12]}: {path}")
        entry = tree[path]
        _, reason = classify(path, entry)
        require(reason is None, f"Excluded evidence {path}: {reason}")
        if "end" in source:
            require(entry["size"] <= 2 * 1024 * 1024, f"Use file-level evidence for large file {path}")
            blob = entry["blob"]
            if blob not in line_counts:
                content = repo.blob(blob)
                line_counts[blob] = content.count(b"\n") + (not content.endswith(b"\n")) if content and b"\0" not in content else 0
            require(source["end"] <= line_counts[blob], f"Evidence lines outside text file {path}")
        source["blob"] = entry["blob"]
    event.update(schema=1, revision=revision, recorded_at=datetime.now(timezone.utc).isoformat())
    event_id = event["id"]
    content = json_bytes(event)
    privacy.reject(privacy.inspect(path_to_event, content))

    def same(previous):
        return all(previous.get(k) == event.get(k)
                   for k in (previous.keys() | event.keys()) - {"recorded_at"})

    if event_id in events:
        require(same(events[event_id]), f"Event {event_id} already exists with different content")
        return {"path": path_to_event, "created": False}
    events[event_id] = event
    project(events)
    directory.mkdir(parents=True, exist_ok=True)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".pending-", delete=False) as handle:
            pending = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            if os.name == "nt":
                os.rename(pending, destination)
            else:
                os.link(pending, destination)
            created = True
        except FileExistsError:
            require(not destination.is_symlink() and same(load(repo)[event_id]),
                    f"Concurrent event {event_id} has different content")
            created = False
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
    return {"path": path_to_event, "created": created}


def verify(repo, events, revision, base):
    nodes, edges = project(events)
    if base is not None:
        records = committed_paths(repo, base)
        blobs = repo.blobs(records.values())
        for path, oid in records.items():
            current = repo.root / path
            require(current.is_file() and not current.is_symlink(), f"Historical event removed: {path}")
            require(current.read_bytes().replace(b"\r\n", b"\n") == blobs[oid].replace(b"\r\n", b"\n"),
                    f"Historical event rewritten: {path}")

    paths = {}
    commits = set()
    for event in events.values():
        for source in items(event, "evidence"):
            commit = event["revision"]
            commits.add(commit)
            key = f"{commit}:{source['path']}"
            paths.setdefault(key, []).append((event["id"], source))
    unverifiable = []
    if paths:
        queries = [*sorted(commits), *paths]
        output = repo.git("cat-file", "--batch-check=%(objectname) %(objecttype)",
                          data="".join(f"{key}\n" for key in queries).encode("utf-8")).split(b"\n")[:-1]
        require(len(output) == len(queries), "Git did not return every evidence lookup")
        results = dict(zip(queries, output, strict=True))
        tree = repo.tree(revision)
        for key, answers in paths.items():
            found = results[key]
            commit = key.partition(":")[0]
            for event_id, source in answers:
                expected = f"{source['blob']} blob".encode()
                if found.endswith(b" missing"):
                    require(results[commit] == f"{commit} missing".encode(),
                            f"Evidence path absent from its original revision in {event_id}: {source['path']}")
                    if tree.get(source["path"], {}).get("blob") != source["blob"]:
                        unverifiable.append({"event": event_id, "path": source["path"]})
                else:
                    require(results[commit] == f"{commit} commit".encode() and found == expected,
                            f"Evidence hash mismatch in {event_id}: {source['path']}")
    return {"events": len(events), "nodes": len(nodes), "edges": len(edges), "unverifiable": unverifiable}


def coverage(repo, events, revision, details):
    reviewed = defaultdict(set)
    for event in active_events(events).values():
        for source in items(event, "evidence"):
            if source.get("reviewed"):
                reviewed[source["path"]].add(source["blob"])
    counts = dict(covered=0, stale=0, missing=0, excluded=0)
    paths = {key: [] for key in counts}
    for path, entry in sorted(repo.tree(revision).items()):
        _, reason = classify(path, entry)
        if reason:
            key = "excluded"
        elif entry["blob"] in reviewed[path]:
            key = "covered"
        else:
            key = "stale" if reviewed[path] else "missing"
        counts[key] += 1
        if details:
            paths[key].append({"path": path, "reason": reason} if reason else path)
    return {"revision": revision, "counts": counts, **({"paths": paths} if details else {})}


def freshness(event, tree):
    evidence = items(event, "evidence")
    changed = sorted({source["path"] for source in evidence
                      if tree.get(source["path"], {}).get("blob") != source["blob"]})
    if evidence:
        state = "stale" if changed else "current"
    else:
        state = "external" if items(event, "links") else "unpinned"
    return {"state": state, "changed_paths": changed}


def outdated(repo, base, candidate):
    """List active facts whose evidence files change between base and candidate and no longer match."""
    changed = set(repo.changed(base, candidate))
    tree = repo.tree(candidate)
    events = active_events(load_committed(repo, candidate))
    rows = []
    for event_id, event in sorted(events.items()):
        touched = sorted(set(freshness(event, tree)["changed_paths"]) & changed)
        if touched:
            rows.append({"event": event_id, "summary": event["summary"], "paths": touched})
    return {"base": base, "candidate": candidate, "checked": len(events), "outdated": rows}


def search(events, query, history, limit, tree):
    selected = events if history else active_events(events)
    terms = query.casefold().split()
    require(terms, "Search query must contain text")
    ranked = sorted(selected.values(), key=lambda event: (event["recorded_at"], event["id"]), reverse=True)
    matches = [event for event in ranked
               if all(term in json.dumps(event, ensure_ascii=False).casefold() for term in terms)][:limit]
    return [{**event, "freshness": freshness(event, tree)} for event in matches]


def trace(events, origin, hops, limit, tree):
    nodes, edges = project(events)
    require(origin in nodes, f"Unknown graph node: {origin}")
    adjacency = defaultdict(list)
    for edge in edges.values():
        adjacency[edge["from"]].append(edge["to"])
        adjacency[edge["to"]].append(edge["from"])
    queue = deque([(origin, 0)])
    visited = {origin}
    truncated = False
    while queue:
        node, distance = queue.popleft()
        if distance >= hops:
            continue
        for neighbor in sorted(set(adjacency[node]) - visited):
            if len(visited) >= limit:
                truncated = True
                break
            visited.add(neighbor)
            queue.append((neighbor, distance + 1))
    included = [nodes[node] for node in sorted(visited)]
    connections = [edge for (source, _, target), edge in sorted(edges.items())
                   if source in visited and target in visited]
    event_ids = {event_id for row in included + connections for event_id in row["events"]}
    return {"nodes": included, "edges": connections, "truncated": truncated,
            "freshness": {event_id: freshness(events[event_id], tree) for event_id in sorted(event_ids)}}
