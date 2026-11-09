"""Repo Symbiosis command line. Python 3.10+ and Git are the only dependencies."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "scripts"

from . import __version__, agent_history, delivery, github_gate, memory, privacy, setup_tools
from .agent_memory import Snapshot
from .repository import Repository, SymbiosisError, inventory, json_bytes, read_json, require


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repo", default=".", help="Target Git working tree")
    cli.add_argument("--version", action="version", version=__version__)
    commands = cli.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Create repository policy and optionally a pinned GitHub workflow")
    init.add_argument("--base", required=True)
    init.add_argument("--mode", choices=["cooperative", "github-app"], default="cooperative")
    init.add_argument("--checker", help="Published wheel URL with #sha256 hash")
    doctor = commands.add_parser("doctor", help="Check repository and local runtime prerequisites")
    doctor.add_argument("--remote", default="origin")
    doctor.add_argument("--strict", action="store_true")
    gate = commands.add_parser("gate", help="Check a GitHub pull request or merge group")
    gate.add_argument("--event", default=os.getenv("GITHUB_EVENT_PATH"))
    gate.add_argument("--event-name", choices=["pull_request", "merge_group"], default=os.getenv("GITHUB_EVENT_NAME"))
    gate.add_argument("--candidate", default=os.getenv("GITHUB_SHA", "HEAD"))
    gate.add_argument("--remote", default="origin")
    gate.add_argument("--coordination-remote", help="Coordination remote, defaulting to --remote")
    manifest = commands.add_parser("manifest", help="Create the manifest for a reserved change")
    manifest.add_argument("task")
    manifest.add_argument("--run", required=True)
    manifest.add_argument("--claim-id", required=True)
    manifest.add_argument("--remote", default="origin")
    scanner = commands.add_parser("scan-memory", help="Scan staged or committed memory for recognizable credentials")
    scanner.add_argument("--base", help="Compare committed memory to this revision")
    scanner.add_argument("--candidate", default="HEAD")
    audit = commands.add_parser("audit", help="Read coordination receipts")
    audit.add_argument("--remote", default="origin")
    audit.add_argument("--limit", type=int, default=50)

    scan = commands.add_parser("inventory", help="List analysis shards at a Git revision")
    scan.add_argument("--rev", default="HEAD")
    scan.add_argument("--since", help="List only changes since this revision")
    scan.add_argument("--shard-size", type=int, default=100)
    scan.add_argument("--shard", type=int, help="Return files for this numbered shard")
    stale = commands.add_parser("stale", help="List memory facts whose source files changed since a base")
    stale.add_argument("--base", required=True, help="Revision the memory was last checked against")
    stale.add_argument("--candidate", default="HEAD")
    stale.add_argument("--strict", action="store_true", help="Exit 1 when any fact is outdated")
    coverage = commands.add_parser("coverage", help="Compare current files to reviewed evidence")
    coverage.add_argument("--rev", default="HEAD")
    coverage.add_argument("--details", action="store_true")
    record = commands.add_parser("record", help="Add a new immutable memory event")
    record.add_argument("input", help="Event JSON file or - for stdin")
    record.add_argument("--rev", default="HEAD", help="Source commit described by this event")
    verify = commands.add_parser("verify", help="Check graph consistency and evidence")
    verify.add_argument("--rev", default="HEAD", help="Fallback snapshot for evidence from a squash merge")
    verify.add_argument("--base", help="Check that every event in this base still exists unmodified")
    verify.add_argument("--strict", action="store_true", help="Fail when historical evidence is unavailable")
    journal = commands.add_parser("agent-commit", help="Commit a new journal event on a reserved agent branch")
    journal.add_argument("input", help="Agent event JSON file or - for stdin")
    journal.add_argument("--remote", required=True, help="Shared coordination remote")
    journal.add_argument("--claim-id", required=True)
    journal.add_argument("--token", help="Holder token, if this clone did not make the claim")
    lineage = commands.add_parser("verify-history", help="Check that a committed integration preserves journal and Git history")
    lineage.add_argument("--base", required=True, help="Trusted protected base commit")
    lineage.add_argument("--candidate", default="HEAD", help="Integration candidate commit")
    lineage.add_argument("--preserve", action="append", default=[], help="Original approved PR head, repeatable")
    lineage.add_argument("--require-run", action="append", default=[], help="Run that must newly complete, repeatable")
    search = commands.add_parser("search", help="Find events by text")
    search.add_argument("query")
    search.add_argument("--rev", default="HEAD")
    search.add_argument("--history", action="store_true")
    search.add_argument("--limit", type=int, default=20)
    trace = commands.add_parser("trace", help="Explore incoming and outgoing graph relationships")
    trace.add_argument("node")
    trace.add_argument("--rev", default="HEAD")
    trace.add_argument("--hops", type=int, default=2)
    trace.add_argument("--limit", type=int, default=100)

    shared = commands.add_parser("memory", help="Query committed project memory without Codex")
    shared.add_argument("--ref", default="HEAD", help="Local Git revision, such as origin/main or a pinned SHA")
    readers = shared.add_subparsers(dest="reader", required=True)
    readers.add_parser("overview", help="Show the onboarding entry point, graph size, and evidence coverage")
    find = readers.add_parser("search", help="Find committed events by text")
    find.add_argument("query")
    find.add_argument("--history", action="store_true")
    find.add_argument("--limit", type=int, default=10)
    event = readers.add_parser("event", help="Read one event, including a page of evidence and graph facts")
    event.add_argument("event_id")
    event.add_argument("--offset", type=int, default=0)
    event.add_argument("--limit", type=int, default=20)
    timeline = readers.add_parser("run", help="Read an agent run's committed timeline and delegation parent")
    timeline.add_argument("run_id")
    timeline.add_argument("--offset", type=int, default=0)
    timeline.add_argument("--limit", type=int, default=20)
    graph = readers.add_parser("trace", help="Trace committed graph relationships")
    graph.add_argument("node")
    graph.add_argument("--hops", type=int, default=2)
    graph.add_argument("--limit", type=int, default=30)
    doc = readers.add_parser("doc", help="Read a page of the committed Markdown knowledge pack")
    doc.add_argument("path")
    doc.add_argument("--start-line", type=int, default=1)
    doc.add_argument("--limit", type=int, default=80)

    plan = commands.add_parser("plan", help="Schedule non-overlapping delivery waves")
    plan.add_argument("input", help="Backlog JSON file or - for stdin")
    plan.add_argument("--capacity", type=int, default=4)
    plan.add_argument("--remote", help="Exclude active reservations on this Git remote")
    for name, help_text in (
        ("claims", "List active reservations"),
        ("claim", "Atomically reserve a task's write paths"),
        ("release-claim", "Release a reservation after a verified outcome"),
        ("worktree", "Create the reservation's isolated Git worktree"),
        ("check-scope", "Compare changes to the reservation"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--remote", required=True)
        if name != "claims":
            command.add_argument("task")
        if name in ("release-claim", "worktree", "check-scope"):
            command.add_argument("--claim-id", required=True)
        if name in ("claim", "release-claim"):
            command.add_argument("--owner", required=True)
        if name == "claim":
            command.add_argument("--plan", required=True)
            command.add_argument("--base", required=True)
            command.add_argument("--branch", required=True)
            command.add_argument("--lease", default=delivery.DEFAULT_LEASE,
                                 help="How long the reservation lasts without renewal, such as 30m, 4h, 2d or none")
        if name == "release-claim":
            command.add_argument("--outcome", choices=["merged", "abandoned"], required=True)
            command.add_argument("--merge-rev", help="Verified integration commit when the outcome is merged")
            command.add_argument("--token", help="Holder token, if this clone did not make the claim")
            command.add_argument("--override", metavar="REASON",
                                 help="Release a held or expired reservation without its holder token")
        if name == "worktree":
            command.add_argument("--path", required=True)
        if name == "check-scope":
            command.add_argument("--against", help="An updated, verified protected base included in this branch")
    renew = commands.add_parser("renew-claim", help="Extend a reservation's lease or hold it until merge")
    renew.add_argument("task")
    renew.add_argument("--remote", required=True)
    renew.add_argument("--claim-id", required=True)
    renew.add_argument("--token", help="Holder token, if this clone did not make the claim")
    renewal = renew.add_mutually_exclusive_group()
    renewal.add_argument("--lease", help="New lease length, such as 30m, 4h or 2d")
    renewal.add_argument("--hold", action="store_true", help="Stop the lease clock until the reservation is released")
    extend = commands.add_parser("extend-claim", help="Add paths or resources to an existing reservation")
    extend.add_argument("task")
    extend.add_argument("--remote", required=True)
    extend.add_argument("--claim-id", required=True)
    extend.add_argument("--path", action="append", default=[], help="Write path to add, repeatable")
    extend.add_argument("--resource", action="append", default=[], help="Shared resource to add, repeatable")
    extend.add_argument("--token", help="Holder token, if this clone did not make the claim")
    return cli


def step_summary(markdown):
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if not destination:
        return
    try:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    except OSError as error:
        print(f"symbiosis: could not write the step summary: {error}", file=sys.stderr)


def input_bytes(path):
    return sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes()


def run(args):
    command = args.command
    if command == "gate":
        require(args.event and args.event_name, "Provide GitHub event name and payload")
        repo = Repository(args.repo, committed_read=True)
        value, code = github_gate.run(repo, args.event_name, read_json(input_bytes(args.event), "GitHub event"),
                                      repo.revision(args.candidate), args.remote,
                                      args.coordination_remote or args.remote)
        step_summary(github_gate.summary_markdown(value))
        if code:
            messages = "; ".join(row["message"] for row in value["problems"])
            print(f"symbiosis: gate found {len(value['problems'])} problem(s): {messages}", file=sys.stderr)
        return value, code
    if command == "stale":
        repo = Repository(args.repo, committed_read=True)
        report = memory.outdated(repo, repo.revision(args.base), repo.revision(args.candidate))
        return report, 1 if args.strict and report["outdated"] else 0
    if command == "memory":
        repo = Repository(args.repo, committed_read=True)
        snapshot = Snapshot(repo, repo.revision(args.ref))
        if args.reader == "overview":
            return snapshot.overview()
        if args.reader == "search":
            return snapshot.search(args.query, args.history, args.limit)
        if args.reader == "event":
            return snapshot.event(args.event_id, args.offset, args.limit)
        if args.reader == "run":
            return snapshot.run(args.run_id, args.offset, args.limit)
        if args.reader == "trace":
            return snapshot.trace(args.node, args.hops, args.limit)
        return snapshot.document(args.path, args.start_line, args.limit)
    if command == "verify-history":
        repo = Repository(args.repo, committed_read=True)
        return agent_history.verify_history(
            repo, repo.revision(args.base), repo.revision(args.candidate),
            [repo.revision(ref) for ref in args.preserve], args.require_run,
        )
    if command == "plan":
        require(1 <= args.capacity <= 100, "Capacity must be between 1 and 100")
        tasks, completed = delivery.backlog(input_bytes(args.input))
        ledger = delivery.Ledger(Repository(args.repo), args.remote) if args.remote else None
        _, active, merged = ledger.read() if ledger else (None, {}, {})
        return delivery.schedule(tasks, completed | merged.keys(), delivery.live(active), args.capacity)
    repo = Repository(args.repo)
    if command == "init":
        return setup_tools.initialize(repo, args.base, args.mode, args.checker)
    if command == "doctor":
        report = setup_tools.doctor(repo, args.remote)
        return report, 1 if args.strict and report["problems"] else 0
    if command == "manifest":
        return setup_tools.write_manifest(repo, args.remote, args.task, args.run, args.claim_id)
    if command == "scan-memory":
        report = privacy.scan(repo, repo.revision(args.base) if args.base else None,
                              repo.revision(args.candidate) if args.base else None)
        return report, 1 if report["findings"] else 0
    if command == "audit":
        require(1 <= args.limit <= 1000, "Limit must be between 1 and 1000")
        revision, state = delivery.Ledger(repo, args.remote).state()
        rows = state.get("receipts", {})
        receipts = sorted(rows.items(), key=lambda row: (row[1].get("recorded_at", ""), row[0]), reverse=True)
        return {"revision": revision, "total": len(rows), "receipts": dict(receipts[:args.limit])}
    if command == "inventory":
        require(1 <= args.shard_size <= 5000, "Shard size must be between 1 and 5000")
        return inventory(repo, repo.revision(args.rev), repo.revision(args.since) if args.since else None,
                         args.shard_size, args.shard)
    if command == "record":
        return memory.record(repo, repo.revision(args.rev), read_json(input_bytes(args.input), "event"))
    if command == "agent-commit":
        return agent_history.journal_commit(repo, read_json(input_bytes(args.input), "agent event"),
                                            args.remote, args.claim_id, args.token)
    if command in ("verify", "coverage", "search", "trace"):
        events = memory.load(repo)
        if command == "verify":
            result = memory.verify(repo, events, repo.revision(args.rev), repo.revision(args.base) if args.base else None)
            return result, 1 if args.strict and result["unverifiable"] else 0
        if command == "coverage":
            return memory.coverage(repo, events, repo.revision(args.rev), args.details)
        require(1 <= args.limit <= 1000, "Limit must be between 1 and 1000")
        tree = repo.tree(repo.revision(args.rev))
        if command == "search":
            return memory.search(events, args.query, args.history, args.limit, tree)
        require(0 <= args.hops <= 10, "Hops must be between 0 and 10")
        return memory.trace(events, args.node, args.hops, args.limit, tree)

    ledger = delivery.Ledger(repo, args.remote)
    if command == "claims":
        revision, claims, completed = ledger.read()
        expired = sorted(set(claims) - set(delivery.live(claims)))
        return {"revision": revision, "claims": claims, "completed": completed, "expired": expired}
    if command == "claim":
        tasks, completed = delivery.backlog(input_bytes(args.plan))
        require(args.task in tasks, f"Unknown task {args.task}")
        return ledger.claim(tasks[args.task], completed, args.owner, repo.revision(args.base), args.branch, args.lease)
    if command == "release-claim":
        require(bool(args.merge_rev) == (args.outcome == "merged"), "Supply --merge-rev for a merged outcome")
        revision = repo.revision(args.merge_rev) if args.merge_rev else None
        return ledger.release(args.task, args.owner, args.claim_id, args.outcome, revision, args.token, args.override)
    if command == "renew-claim":
        return ledger.renew(args.task, args.claim_id, args.token, args.lease, args.hold)
    if command == "extend-claim":
        return ledger.extend(args.task, args.claim_id, args.path, args.resource, args.token)
    claim = ledger.task_claim(args.task, args.claim_id)
    if command == "worktree":
        return delivery.add_worktree(repo, claim, args.path)
    return delivery.check_scope(repo, claim, args.against, args.remote)


def main():
    try:
        result = run(parser().parse_args())
        value, exit_code = result if isinstance(result, tuple) else (result, 0)
        sys.stdout.buffer.write(json_bytes(value))
        return exit_code
    except (SymbiosisError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"symbiosis: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
