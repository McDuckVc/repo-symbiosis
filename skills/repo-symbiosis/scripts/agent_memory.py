"""Read the project knowledge pack and graph from a single committed Git revision."""

from . import memory
from .repository import path_name, require


DOCS = "docs/symbiosis/"
EVENT_SECTIONS = ("evidence", "nodes", "edges")


class Snapshot:
    def __init__(self, repo, revision):
        self.repo = repo
        self.revision = revision
        self.tree = repo.tree(revision)
        self.events = memory.load_committed(repo, revision)

    def overview(self):
        nodes, edges = memory.project(self.events)
        docs = sorted(path for path, entry in self.tree.items()
                      if path.startswith(DOCS) and path.endswith(".md") and entry["mode"] == "100644")
        entrypoint = f"{DOCS}README.md"
        return {
            "revision": self.revision,
            "entrypoint": entrypoint if entrypoint in docs else None,
            "documents": docs[:100],
            "document_count": len(docs),
            "events": len(self.events),
            "active_events": len(memory.active_events(self.events)),
            "agent_runs": len(memory.agent_runs(self.events)),
            "nodes": len(nodes),
            "edges": len(edges),
            "coverage": memory.coverage(self.repo, self.events, self.revision, details=False)["counts"],
        }

    def search(self, query, history=False, limit=10):
        memory.required_text(query, "query", 500)
        require(1 <= limit <= 30, "Limit must be between 1 and 30")
        matches = memory.search(self.events, query, history, limit, self.tree)
        results = []
        for event in matches:
            fields = ("id", "kind", "summary", "task", "status", "recorded_at", "freshness")
            row = {key: event[key] for key in fields if key in event}
            if "agent" in event:
                row.update(run=event["agent"]["run"], phase=event["agent"]["phase"])
            results.append(row)
        return {"revision": self.revision, "results": results}

    def run(self, run_id, offset=0, limit=20):
        memory.valid_id(run_id)
        require(offset >= 0 and 1 <= limit <= 100, "Offset must be nonnegative and limit between 1 and 100")
        runs = memory.agent_runs(self.events)
        require(run_id in runs, f"Unknown agent run: {run_id}")
        events = runs[run_id]
        start = events[0]
        end = offset + limit
        return {
            "revision": self.revision,
            "run": run_id,
            "task": start["task"],
            "actor": start["actor"],
            "provider": start["agent"]["provider"],
            "branch": start["agent"].get("branch"),
            "parent": start["agent"].get("parent"),
            "phase": events[-1]["agent"]["phase"],
            "events": [
                {**{key: event[key] for key in ("id", "summary", "revision", "recorded_at")},
                 "phase": event["agent"]["phase"], "path": memory.event_path(event)}
                for event in events[offset:end]
            ],
            "next_offset": end if end < len(events) else None,
        }

    def event(self, event_id, offset=0, limit=20):
        memory.valid_id(event_id)
        require(offset >= 0 and 1 <= limit <= 100, "Offset must be nonnegative and limit between 1 and 100")
        require(event_id in self.events, f"Unknown memory event: {event_id}")
        event = self.events[event_id]
        counts = {key: len(memory.items(event, key)) for key in EVENT_SECTIONS}
        end = offset + limit
        page = {key: value for key, value in event.items() if key not in EVENT_SECTIONS}
        page.update({key: memory.items(event, key)[offset:end] for key in EVENT_SECTIONS})
        return {
            "revision": self.revision,
            "path": memory.event_path(event),
            "event": page,
            "active": event_id in memory.active_events(self.events),
            "freshness": memory.freshness(event, self.tree),
            "section_counts": counts,
            "next_offset": end if end < max(counts.values()) else None,
        }

    def trace(self, node, hops=2, limit=30):
        memory.required_text(node, "node", 256)
        require(0 <= hops <= 5 and 1 <= limit <= 50, "Hops must be 0–5 and limit 1–50")
        result = memory.trace(self.events, node, hops, limit, self.tree)
        edges = result["edges"][:150]
        shown = {event for row in result["nodes"] + edges for event in row["events"]}
        return {
            "revision": self.revision,
            "nodes": result["nodes"],
            "edges": edges,
            "freshness": {event: result["freshness"][event] for event in sorted(shown)},
            "truncated": result["truncated"] or len(edges) < len(result["edges"]),
        }

    def document(self, path, start_line=1, limit=80):
        path_name(path)
        require(path.startswith(DOCS) and path.endswith(".md"), f"Only Markdown under {DOCS} is available")
        require(start_line >= 1 and 1 <= limit <= 150, "Start line must be positive and limit between 1 and 150")
        entry = self.tree.get(path)
        require(entry and entry["mode"] == "100644", f"Unknown knowledge document: {path}")
        require(entry["size"] <= 2 * 1024 * 1024, "Knowledge document exceeds the 2 MiB read limit")
        lines = self.repo.blob(entry["blob"]).decode("utf-8-sig").splitlines(keepends=True)
        offset = start_line - 1
        end = min(offset + limit, len(lines))
        content = "".join(lines[offset:end])
        # Cap dense single-line documents as well as ordinary line pages.
        require(len(content) <= 24000, "Document page exceeds 24,000 characters. Request fewer lines or read the Git blob.")
        return {
            "revision": self.revision,
            "path": path,
            "blob": entry["blob"],
            "start_line": start_line,
            "line_count": len(lines),
            "next_line": end + 1 if end < len(lines) else None,
            "content": content,
        }
