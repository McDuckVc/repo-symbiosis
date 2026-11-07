"""Benchmark Git inventory and cold memory lookup on a caller-supplied repository."""

import argparse
import json
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from repo_symbiosis import memory
from repo_symbiosis.agent_memory import Snapshot
from repo_symbiosis.repository import Repository, classify, inventory, json_bytes, require


def measured(count, operation):
    times = []
    for _ in range(count):
        start = time.perf_counter()
        result = operation()
        times.append(time.perf_counter() - start)
    return result, round(statistics.median(times), 4)


def sample_memory(repo, revision, root, count):
    tree = repo.tree(revision)
    paths = [path for path, entry in sorted(tree.items()) if classify(path, entry)[0] == "source-or-other"]
    require(len(paths) >= count, "Choose fewer benchmark records or a larger source tree")
    records = {}
    for number, path in enumerate(paths[::max(1, len(paths) // count)][:count], 1):
        event_id = f"benchmark-{number:05}"
        event = {
            "id": event_id, "kind": "analysis", "summary": f"Synthetic benchmark record {number}",
            "schema": 1, "revision": revision, "recorded_at": "2026-01-01T00:00:00+00:00",
            "evidence": [{"path": path, "blob": tree[path]["blob"]}],
            "nodes": [{"id": f"file:sample-{number}", "title": path[:200]}],
        }
        destination = root / f"{event_id}.json"
        destination.write_bytes(json_bytes(event))
        records[memory.event_path(event)] = destination.as_posix()
    hashed = repo.git("hash-object", "-w", "--stdin-paths",
                      data=("\n".join(records.values()) + "\n").encode("utf-8")).decode().splitlines()
    require(len(hashed) == len(records), "Git omitted a benchmark blob")
    repo.git("read-tree", revision)
    repo.git("update-index", "--index-info",
             data="".join(f"100644 {oid}\t{path}\n" for path, oid in zip(records, hashed)).encode())
    tree_id = repo.git("write-tree").decode().strip()
    return repo.git("-c", "user.name=Symbiosis Benchmark", "-c", "user.email=benchmark@example.invalid",
                    "commit-tree", tree_id, "-p", revision, "-m", "Synthetic memory benchmark").decode().strip()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repo", required=True)
    cli.add_argument("--rev", default="HEAD")
    cli.add_argument("--since", default="HEAD^")
    cli.add_argument("--records", type=int, default=500)
    cli.add_argument("--repeats", type=int, default=3)
    args = cli.parse_args()
    require(1 <= args.records <= 10000 and 1 <= args.repeats <= 20, "Invalid benchmark size")
    source = Repository(args.repo, committed_read=True)
    revision, since = source.revision(args.rev), source.revision(args.since)
    full, full_time = measured(args.repeats, lambda: inventory(
        Repository(source.root, committed_read=True), revision, None, 100, None,
    ))
    delta, delta_time = measured(args.repeats, lambda: inventory(
        Repository(source.root, committed_read=True), revision, since, 100, None,
    ))
    with tempfile.TemporaryDirectory(prefix="symbiosis-benchmark-") as temporary:
        root = Path(temporary)
        clone = root / "source"
        subprocess.run(["git", "clone", "--local", "--no-checkout", "--template=", str(source.root), str(clone)],
                       capture_output=True, check=True)
        snapshot = sample_memory(Repository(clone), revision, root, args.records)
        query, query_time = measured(args.repeats, lambda: Snapshot(
            Repository(clone, committed_read=True), snapshot,
        ).search("benchmark", limit=10))
    print(json.dumps({
        "revision": revision, "since": since, "platform": platform.system(), "python": platform.python_version(),
        "git": source.git("--version").decode().strip(), "repeats": args.repeats,
        "tracked_paths": len(source.tree(revision)), "shards": len(full["shards"]),
        "changed_paths": sum(shard["files"] for shard in delta["shards"]) + len(delta["deleted"]) + len(delta["excluded"]),
        "synthetic_records": args.records, "query_matches_returned": len(query["results"]),
        "median_seconds": {"full_inventory": full_time, "incremental_inventory": delta_time, "cold_memory_search": query_time},
    }, indent=2))


if __name__ == "__main__":
    main()
