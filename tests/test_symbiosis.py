"""Black-box coverage and deterministic races against real Git repositories."""

import asyncio
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "repo-symbiosis" / "scripts"
sys.path.insert(0, str(SCRIPTS.parent))

from scripts import delivery, github_gate
from scripts.delivery import MAX_RECEIPTS, REF, Ledger, digest, includes
from scripts.repository import Repository, SymbiosisError
from scripts.setup_tools import mcp_supported


def field(value, camel, snake):
    """Read an MCP result field under its 1.x (camelCase) or 2.x (snake_case) name."""
    return getattr(value, camel) if hasattr(value, camel) else getattr(value, snake)


def structured(result):
    return field(result, "structuredContent", "structured_content")


def journal_event(run, task, sequence, phase, **details):
    return {
        "id": f"agent-{run}-{sequence:04}",
        "kind": "agent",
        "summary": f"{phase.capitalize()} {task}",
        "task": task,
        "actor": f"worker/{run}",
        "agent": {
            "run": run, "phase": phase, "provider": "codex",
            "rationale": "Keep the task isolated so teammates can review its results.",
            **({"previous": f"agent-{run}-{sequence - 1:04}"} if sequence > 1 else {}),
            **details,
        },
    }


class RepoFixture(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory(prefix="symbiosis-test-")
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        self.repo = self.root / "project"
        self.repo.mkdir()
        self.git(self.repo, "init", "--template=", "-b", "main")
        self.identity(self.repo)
        (self.repo / "backend").mkdir()
        self.source = self.repo / "backend" / "pay ment.py"
        self.source.write_text("def pay():\n    return 1\n", encoding="utf-8")
        (self.repo / ".env").write_text("TOKEN=test-placeholder\n", encoding="utf-8")
        self.commit("Initial")
        self.base = self.git(self.repo, "rev-parse", "HEAD")

    def git(self, directory, *args):
        output = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, check=False)
        self.assertEqual(output.returncode, 0, output.stderr)
        return output.stdout.strip()

    def identity(self, repo):
        self.git(repo, "config", "user.name", "Symbiosis Test")
        self.git(repo, "config", "user.email", "test@example.invalid")
        self.git(repo, "config", "commit.gpgsign", "false")

    def commit(self, message):
        self.git(self.repo, "add", "--all")
        self.git(self.repo, "commit", "-m", message)

    def raw_cli(self, *args, payload=None, repo=None, env=None):
        command = [sys.executable, "-B", str(SCRIPTS / "symbiosis.py"), "--repo", str(repo or self.repo), *args]
        return subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                              capture_output=True, text=True, check=False,
                              env={**os.environ, **env} if env else None)

    def cli(self, *args, payload=None, failure=None, repo=None, env=None):
        result = self.raw_cli(*args, payload=payload, repo=repo, env=env)
        if failure:
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(failure, result.stderr)
            return result
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


class MemoryTests(RepoFixture):
    def event(self, event_id, **extra):
        return {
            "id": event_id,
            "kind": "analysis",
            "summary": "Pägement checkout",
            "evidence": [{"path": "backend/pay ment.py", "start": 1, "end": 2, "reviewed": True}],
            "nodes": [{"id": "component:checkout", "title": "Checkout"}, {"id": "symbol:pay", "title": "Pay"}],
            "edges": [{"from": "component:checkout", "to": "symbol:pay", "type": "calls"}],
            **extra,
        }

    def test_pinned_inventory_and_memory_refresh(self):
        self.source.write_text("dirty checkout\n", encoding="utf-8")
        snapshot = self.cli("inventory", "--shard-size", "1")
        self.assertEqual(snapshot["revision"], self.base)
        self.assertEqual(snapshot["excluded"][0]["path"], ".env")
        shard = self.cli("inventory", "--shard-size", "1", "--shard", "1")
        self.assertEqual(shard["files"][0]["path"], "backend/pay ment.py")

        old = self.event("analysis-checkout-01")
        self.assertTrue(self.cli("record", "-", payload=old)["created"])
        self.assertFalse(self.cli("record", "-", payload=old)["created"])
        self.cli("record", "-", payload={**old, "summary": "Different"}, failure="different content")
        self.cli("record", "-", payload=self.event("bad-lines", evidence=[
            {"path": "backend/pay ment.py", "start": 1, "end": 3}
        ]), failure="outside text file")
        self.assertEqual(self.cli("verify", "--strict")["edges"], 1)
        self.assertEqual(self.cli("coverage")["counts"]["covered"], 1)
        self.assertEqual(self.cli("search", "pägement")[0]["id"], old["id"])

        self.source.write_text("def pay():\n    return 1\n", encoding="utf-8")
        self.commit("Baseline memory")
        protected = self.git(self.repo, "rev-parse", "HEAD")
        self.source.write_text("def pay():\n    return 2\n", encoding="utf-8")
        self.commit("Change behavior")
        changes = self.cli("inventory", "--since", protected, "--shard", "1")
        self.assertEqual([row["path"] for row in changes["files"]], ["backend/pay ment.py"])
        self.assertEqual(self.cli("coverage")["counts"]["stale"], 1)
        trace = self.cli("trace", "symbol:pay")
        self.assertEqual(trace["freshness"][old["id"]]["state"], "stale")
        self.assertEqual(len(trace["nodes"]), 2)

        new = self.event("analysis-checkout-02", summary="Updated checkout", supersedes=[old["id"]])
        self.cli("record", "-", payload=new)
        self.assertEqual(self.cli("coverage")["counts"]["covered"], 1)
        self.assertEqual(self.cli("search", "checkout")[0]["id"], new["id"])
        self.assertEqual(len(self.cli("search", "checkout", "--history")), 2)
        self.assertEqual(self.cli("verify", "--base", protected, "--strict")["events"], 2)
        event = self.repo / ".symbiosis" / "events" / f"{old['id']}.json"
        original = event.read_text(encoding="utf-8")
        event.write_text(original.replace("checkout", "rewritten", 1), encoding="utf-8")
        self.cli("verify", "--base", protected, failure="Historical event rewritten")

    def test_stale_lists_facts_made_outdated_by_a_change(self):
        self.cli("record", "-", payload=self.event("analysis-checkout-01"))
        self.commit("Record checkout analysis")
        base = self.git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(self.cli("stale", "--base", base)["outdated"], [])
        self.source.write_text("def pay():\n    return 3\n", encoding="utf-8")
        self.commit("Change payment")
        report = self.cli("stale", "--base", base)
        self.assertEqual(report["outdated"], [{"event": "analysis-checkout-01", "summary": "Pägement checkout",
                                               "paths": ["backend/pay ment.py"]}])
        self.assertEqual(self.raw_cli("stale", "--base", base, "--strict").returncode, 1)
        self.cli("record", "-", payload=self.event("analysis-checkout-02", supersedes=["analysis-checkout-01"]))
        self.commit("Refresh checkout analysis")
        self.assertEqual(self.cli("stale", "--base", base)["outdated"], [])

    def test_release_chain_matches_revision_and_digest(self):
        digest = "sha256:" + "a" * 64

        def event(phase, previous=None, environment="staging", artifact=digest):
            record = {"phase": phase, "environment": environment, "digest": artifact}
            if previous:
                record["previous"] = previous
            return {"id": f"release-{phase}", "kind": "release", "summary": f"{phase} checkout",
                    "release": record, "links": ["https://example.invalid/deploy"]}

        self.cli("record", "-", payload=event("frozen"))
        self.cli("record", "-", payload=event("staged", "release-frozen", artifact="sha256:" + "b" * 64),
                 failure="Release evidence mismatch")
        self.cli("record", "-", payload=event("staged", "release-frozen"))
        self.cli("record", "-", payload=event("promoted", "release-staged", "production"))
        self.assertEqual(self.cli("verify", "--strict")["events"], 3)

    def publish_agent_memory(self):
        doc = self.repo / "docs" / "symbiosis" / "README.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("# Checkout\n\nApproved handoff.\n", encoding="utf-8")
        self.cli("record", "-", payload=self.event("analysis-checkout-01"))
        self.commit("Publish memory")
        return self.git(self.repo, "rev-parse", "HEAD"), doc

    def test_agent_memory_matches_a_fresh_clone_and_ignores_drafts(self):
        revision, doc = self.publish_agent_memory()
        other = self.root / "agent clone"
        self.git(self.root, "clone", "--template=", "--local", str(self.repo), str(other))
        doc.write_text("Draft handoff\n", encoding="utf-8")
        self.cli("record", "-", payload=self.event("analysis-draft", summary="Draft handoff"))

        options = ("memory", "--ref", revision)
        overview = self.cli(*options, "overview")
        self.assertEqual(self.cli("memory", "--ref", "origin/main", "overview", repo=other), overview)
        self.assertEqual(overview["revision"], revision)
        self.assertEqual(overview["entrypoint"], "docs/symbiosis/README.md")
        self.assertEqual(self.cli(*options, "search", "draft")["results"], [])
        self.assertEqual(self.cli(*options, "search", "checkout")["results"][0]["id"], "analysis-checkout-01")
        details = self.cli(*options, "event", "analysis-checkout-01", "--limit", "1")
        self.assertEqual(details["event"]["evidence"][0]["path"], "backend/pay ment.py")
        self.assertEqual(details["next_offset"], 1)
        self.assertEqual(self.cli(*options, "trace", "symbol:pay")["edges"][0]["type"], "calls")
        page = self.cli(*options, "doc", overview["entrypoint"], "--limit", "1")
        self.assertEqual((page["content"].splitlines(), page["next_line"]), (["# Checkout"], 2))
        self.cli(*options, "doc", ".env", failure="Only Markdown")

    def test_agent_handoff_links_a_new_run_and_closes_the_original(self):
        task = "checkout"
        start = journal_event("checkout-first", task, 1, "started")
        handoff = journal_event("checkout-first", task, 2, "handoff", handoff="Run the duplicate-payment test.")
        followup = journal_event("checkout-next", task, 1, "started", parent=handoff["id"], provider="antigravity")
        for event in (start, handoff, followup):
            self.cli("record", "-", payload=event)
        self.cli("record", "-", payload=journal_event("checkout-first", task, 3, "checkpoint"),
                 failure="Invalid previous event")
        self.commit("Journal a handoff")
        first = self.cli("memory", "run", "checkout-first")
        next_run = self.cli("memory", "run", "checkout-next")
        self.assertEqual(first["phase"], "handoff")
        self.assertEqual(next_run["parent"], handoff["id"])
        self.assertEqual(next_run["provider"], "antigravity")

    @unittest.skipUnless(mcp_supported(), "Optional MCP SDK (mcp>=1.28,<3) is not installed")
    def test_agent_memory_over_stdio(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self.publish_agent_memory()
        self.cli("record", "-", payload=journal_event("checkout-agent", "checkout", 1, "started"))
        self.commit("Publish agent kickoff")
        revision = self.git(self.repo, "rev-parse", "HEAD")

        async def inspect():
            server = StdioServerParameters(
                command=sys.executable,
                args=["-B", str(SCRIPTS / "memory_mcp.py"), "--repo", str(self.repo), "--ref", "refs/heads/main"],
            )
            async with stdio_client(server) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    self.assertEqual({tool.name for tool in tools}, {
                        "memory_overview", "memory_search", "memory_event", "memory_run",
                        "memory_trace", "memory_read_doc",
                    })
                    self.assertTrue(all(field(tool.annotations, "readOnlyHint", "read_only_hint") for tool in tools))
                    result = await session.call_tool("memory_overview")
                    self.assertFalse(field(result, "isError", "is_error"), result.content)
                    self.assertEqual(structured(result)["revision"], revision)
                    found = await session.call_tool("memory_search", {"query": "checkout"})
                    self.assertEqual({item["id"] for item in structured(found)["results"]},
                                     {"analysis-checkout-01", "agent-checkout-agent-0001"})
                    event = await session.call_tool("memory_event", {"event_id": "analysis-checkout-01"})
                    self.assertTrue(structured(event)["active"])
                    run = await session.call_tool("memory_run", {"run_id": "checkout-agent"})
                    self.assertEqual(structured(run)["events"][0]["id"], "agent-checkout-agent-0001")
                    result = await session.call_tool("memory_trace", {"node": "component:checkout"})
                    self.assertEqual(structured(result)["freshness"]["analysis-checkout-01"]["state"], "current")
                    doc = await session.call_tool("memory_read_doc", {"path": "docs/symbiosis/README.md"})
                    self.assertIn("Approved handoff", structured(doc)["content"])
                    refused = await session.call_tool("memory_read_doc", {"path": ".env"})
                    self.assertTrue(field(refused, "isError", "is_error"))
                    self.assertIn("Only Markdown under docs/symbiosis/", refused.content[0].text)

                    self.source.write_text("def pay():\n    return 2\n", encoding="utf-8")
                    self.commit("Refresh the source")
                    refreshed = await session.call_tool("memory_trace", {"node": "component:checkout"})
                    self.assertNotEqual(structured(refreshed)["revision"], revision)
                    self.assertEqual(structured(refreshed)["freshness"]["analysis-checkout-01"]["state"], "stale")

        asyncio.run(asyncio.wait_for(inspect(), timeout=30))


class DeliveryTests(RepoFixture):
    def setUp(self):
        super().setUp()
        self.bare = self.root / "remote.git"
        self.git(self.root, "init", "--template=", "--bare", "-b", "main", str(self.bare))
        self.git(self.repo, "remote", "add", "origin", self.bare.as_uri())
        self.git(self.repo, "push", "origin", "main")
        self.second = self.root / "other clone"
        self.git(self.root, "clone", "--template=", self.bare.as_uri(), str(self.second))
        self.identity(self.second)

    @staticmethod
    def task(task_id, path, **more):
        return {"id": task_id, "title": task_id, "paths": [path], **more}

    def race(self, first, second):
        barrier = threading.Barrier(2, timeout=15)
        attempts = {}

        class RacingRepo(Repository):
            def run(self, *args, data=None):
                if args[:3] == ("-c", "remote.origin.mirror=false", "push"):
                    attempts[self.root] = attempts.get(self.root, 0) + 1
                    if attempts[self.root] == 1:
                        barrier.wait()
                return super().run(*args, data=data)

        def reserve(directory, task):
            try:
                return Ledger(RacingRepo(directory), "origin").claim(
                    task, set(), task["id"], self.base, "symbiosis/" + task["id"]
                )
            except SymbiosisError as error:
                return str(error)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(reserve, self.repo, first)
            b = pool.submit(reserve, self.second, second)
            return [a.result(timeout=30), b.result(timeout=30)], list(attempts.values())

    def test_two_clones_race_on_first_overlapping_claim(self):
        results, attempts = self.race(self.task("checkout", "backend/"), self.task("retry", "Backend/pay ment.py"))
        self.assertEqual(sum(isinstance(row, dict) for row in results), 1, results)
        self.assertIn("Reservation overlaps", next(row for row in results if isinstance(row, str)))
        self.assertEqual(attempts, [1, 1])
        self.assertEqual(len(self.cli("claims", "--remote", "origin")["claims"]), 1)
        self.assertEqual(self.git(self.bare, "rev-list", "--count", REF), "1")

    def test_disjoint_claims_retry_and_both_survive(self):
        results, attempts = self.race(self.task("checkout", "backend/"), self.task("reporting", "reports/"))
        self.assertTrue(all(isinstance(row, dict) for row in results), results)
        self.assertEqual(sorted(attempts), [1, 2])
        self.assertEqual(set(self.cli("claims", "--remote", "origin")["claims"]), {"checkout", "reporting"})
        self.assertEqual(self.git(self.bare, "rev-list", "--count", REF), "2")

    def test_reservation_paths_are_case_sensitive(self):
        claim = {"paths": ["backend/pay ment.py", "docs/"]}
        self.assertTrue(includes(claim, "backend/pay ment.py"))
        self.assertTrue(includes(claim, "docs/guide.md"))
        self.assertFalse(includes(claim, "Backend/pay ment.py"))
        self.assertFalse(includes(claim, "backend/Pay ment.py"))
        self.assertFalse(includes(claim, "Docs/guide.md"))
        self.assertFalse(includes(claim, "docs"))

    def seed_ledger(self, receipts=None, claims=None):
        state = {"schema": 1, "claims": claims or {}, "completed": {}, "receipts": receipts or {}}
        content = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("ascii")
        blob = subprocess.run(["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"], input=content,
                              capture_output=True, check=True).stdout.decode().strip()
        tree = subprocess.run(["git", "-C", str(self.repo), "mktree"],
                              input=f"100644 blob {blob}\tclaims.json\n".encode(),
                              capture_output=True, check=True).stdout.decode().strip()
        commit = self.git(self.repo, "commit-tree", tree, "-m", "Seed coordination ledger")
        self.git(self.repo, "push", "origin", f"{commit}:{REF}")
        return len(content)

    def test_receipts_are_pruned_before_the_ledger_outgrows_its_limit(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        old = {f"deployment-{i}-{i}": {"kind": "deployment", "deployment": i, "status": i, "revision": "0" * 40,
                                       "environment": "production", "result": "success",
                                       "recorded_at": (start + timedelta(seconds=i)).isoformat()}
               for i in range(1, 9001)}
        self.assertGreater(self.seed_ledger(old), 2 * 1024 * 1024)
        plan = self.root / "backlog.json"
        plan.write_text(json.dumps({"tasks": [self.task("alpha", "backend/")]}), encoding="utf-8")
        claim = self.cli("claim", "alpha", "--remote", "origin", "--plan", str(plan), "--owner", "dev-run",
                         "--base", self.base, "--branch", "symbiosis/alpha")
        self.assertEqual(claim["id"], "alpha")
        audit = self.cli("audit", "--remote", "origin", "--limit", "1000")
        self.assertEqual(audit["total"], MAX_RECEIPTS)
        self.assertIn("deployment-9000-9000", audit["receipts"])
        self.assertNotIn("deployment-1-1", audit["receipts"])
        previous = json.loads(self.git(self.bare, "show", f"{REF}~1:claims.json"))
        self.assertEqual(len(previous["receipts"]), 9000)

    def plan_file(self, *tasks):
        file = self.root / "lease-plan.json"
        file.write_text(json.dumps({"tasks": list(tasks)}), encoding="utf-8")
        return file

    def claim(self, task, plan, *extra, repo=None, branch=None):
        return self.cli("claim", task, "--remote", "origin", "--plan", str(plan), "--owner", task,
                        "--base", self.base, "--branch", branch or f"symbiosis/{task}", *extra, repo=repo)

    def test_many_agents_claim_at_once(self):
        count = 10
        plan = self.plan_file(*(self.task(f"task-{i}", f"area-{i}/") for i in range(count)))
        clones = []
        for i in range(count):
            clone = self.root / f"agent clone {i}"
            self.git(self.root, "clone", "--template=", "--quiet", self.bare.as_uri(), str(clone))
            self.identity(clone)
            clones.append(clone)
        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
            results = list(pool.map(lambda i: self.raw_cli(
                "claim", f"task-{i}", "--remote", "origin", "--plan", str(plan), "--owner", f"agent-{i}",
                "--base", self.base, "--branch", f"symbiosis/task-{i}", repo=clones[i]), range(count)))
        self.assertEqual([row.returncode for row in results], [0] * count, [row.stderr for row in results])
        self.assertEqual(len(self.cli("claims", "--remote", "origin")["claims"]), count)
        self.assertEqual(self.git(self.bare, "rev-list", "--count", REF), str(count))

    def test_lease_parsing_and_expiry(self):
        self.assertEqual(delivery.lease_seconds("30m"), 1800)
        self.assertEqual(delivery.lease_seconds("4h"), 14400)
        self.assertIsNone(delivery.lease_seconds("none"))
        for bad in ("1m", "31d", "4 hours", "0h", ""):
            with self.assertRaises(SymbiosisError):
                delivery.lease_seconds(bad)
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        state = {"claims": {
            "old": {"expires_at": (now - timedelta(seconds=1)).isoformat()},
            "new": {"expires_at": (now + timedelta(hours=1)).isoformat()},
            "held": {},
        }}
        self.assertEqual(delivery.prune_expired(state, now), ["old"])
        self.assertEqual(set(state["claims"]), {"new", "held"})
        with self.assertRaisesRegex(SymbiosisError, "expired"):
            github_gate.claim_for({"task": "old", "claim": "a" * 32},
                                  {"old": {"claim": "a" * 32, "expires_at": (now - timedelta(days=1)).isoformat()}},
                                  {"mode": "cooperative"})

    def expired_claim(self, task_id, path, token, branch=None):
        moment = datetime.now(timezone.utc) - timedelta(hours=1)
        return {"id": task_id, "claim": "c" * 32, "owner": "crashed-agent", "base": self.base,
                "branch": branch or f"symbiosis/{task_id}-old", "paths": [path], "resources": [],
                "created_at": (moment - timedelta(hours=4)).isoformat(), "lease": 4 * 3600,
                "expires_at": moment.isoformat(), "token_hash": digest(token)}

    def test_expired_reservation_stops_blocking_and_is_cleared(self):
        self.seed_ledger(claims={"crashed": self.expired_claim("crashed", "backend/", "lost-token")})
        listing = self.cli("claims", "--remote", "origin")
        self.assertEqual(listing["expired"], ["crashed"])
        plan = self.plan_file(self.task("rescue", "backend/pay ment.py"))
        self.assertEqual(self.cli("plan", str(plan), "--remote", "origin")["waves"], [["rescue"]])
        rescue = self.claim("rescue", plan)
        self.assertEqual(set(self.cli("claims", "--remote", "origin")["claims"]), {"rescue"})
        self.assertIn("Cleared expired reservations: crashed", self.git(self.bare, "log", "-1", "--format=%B", REF))
        self.cli("renew-claim", "crashed", "--remote", "origin", "--claim-id", "c" * 32, "--token", "lost-token",
                 failure="missing or has changed")
        self.assertIn("expires_at", rescue)

    def test_holder_can_revive_a_lapsed_lease_nobody_took(self):
        self.seed_ledger(claims={"slow": self.expired_claim("slow", "backend/", "slow-token")})
        shared = ("--remote", "origin", "--claim-id", "c" * 32)
        self.cli("check-scope", "slow", *shared, failure="expired at")
        self.cli("renew-claim", "slow", *shared, failure="Only the session holding slow")
        renewed = self.cli("renew-claim", "slow", *shared, "--token", "slow-token", "--lease", "2h")
        self.assertEqual(renewed["lease"], 7200)
        self.assertEqual(self.cli("claims", "--remote", "origin")["expired"], [])

    def test_journal_renews_the_lease_and_completion_holds_it(self):
        run = "lease-run"
        plan = self.plan_file({**self.task("lease", "backend/"), "paths": [
            "backend/", f".symbiosis/events/agent-runs/{run}/"]})
        claim = self.claim("lease", plan, "--lease", "1h")
        shared = ("--remote", "origin", "--claim-id", claim["claim"])
        worktree = self.root / "lease worktree"
        self.cli("worktree", "lease", *shared, "--path", str(worktree))
        started = self.cli("agent-commit", "-", *shared, payload=journal_event(run, "lease", 1, "started"),
                           repo=worktree)
        self.assertGreater(datetime.fromisoformat(started["lease_expires_at"]),
                           datetime.fromisoformat(claim["expires_at"]))
        done = journal_event(run, "lease", 2, "completed", checks=["Checked the lease flow."])
        self.assertIsNone(self.cli("agent-commit", "-", *shared, payload=done, repo=worktree)["lease_expires_at"])
        held = self.cli("claims", "--remote", "origin")["claims"]["lease"]
        self.assertNotIn("expires_at", held)
        self.assertEqual(held["claim"], claim["claim"])

    def test_extend_claim_keeps_the_reservation(self):
        plan = self.plan_file(self.task("alpha", "backend/pay ment.py"), self.task("beta", "reports/"))
        alpha = self.claim("alpha", plan)
        self.claim("beta", plan, repo=self.second)
        shared = ("--remote", "origin", "--claim-id", alpha["claim"])
        worktree = self.root / "extend worktree"
        self.cli("worktree", "alpha", *shared, "--path", str(worktree))
        (worktree / "docs").mkdir()
        (worktree / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
        failed = self.cli("check-scope", "alpha", *shared, repo=worktree, failure="outside the reservation")
        self.assertIn(f"extend-claim alpha --remote origin --claim-id {alpha['claim']} --path docs/guide.md",
                      failed.stderr)
        self.cli("extend-claim", "alpha", *shared, "--path", "reports/x.py", failure="Extension overlaps beta")
        self.cli("extend-claim", "alpha", *shared, "--path", "docs/", repo=self.second,
                 failure="Only the session holding alpha")
        extended = self.cli("extend-claim", "alpha", *shared, "--path", "docs/", "--resource", "docs-site")
        self.assertEqual((extended["claim"], extended["branch"]), (alpha["claim"], alpha["branch"]))
        self.assertEqual(extended["paths"], ["backend/pay ment.py", "docs/"])
        self.assertEqual(extended["resources"], ["docs-site"])
        self.assertEqual(self.cli("check-scope", "alpha", *shared, repo=worktree)["paths"], ["docs/guide.md"])
        again = self.cli("extend-claim", "alpha", *shared, "--path", "docs/more.md")
        self.assertEqual(again["paths"], extended["paths"])

    def test_only_the_holder_changes_a_live_reservation(self):
        plan = self.plan_file(self.task("alpha", "backend/"))
        alpha = self.claim("alpha", plan)
        self.assertEqual(len(alpha["token"]), 32)
        shared = ("--remote", "origin", "--claim-id", alpha["claim"])
        release = ("release-claim", "alpha", *shared, "--owner", "alpha", "--outcome", "abandoned")
        self.cli(*release, repo=self.second, failure="Only the session holding alpha")
        self.cli(*release, "--override", "Agent crashed", repo=self.second, failure="running lease")
        self.cli("renew-claim", "alpha", *shared, "--hold", repo=self.second,
                 env={"SYMBIOSIS_CLAIM_TOKEN": alpha["token"]})
        self.assertNotIn("expires_at", self.cli("claims", "--remote", "origin")["claims"]["alpha"])
        self.assertTrue(self.cli(*release, "--override", "Run finished but its PR was closed",
                                 repo=self.second)["released"])
        self.assertIn("Override by alpha: Run finished but its PR was closed",
                      self.git(self.bare, "log", "-1", "--format=%B", REF))

    def test_account_token_lets_any_session_of_that_account_resume(self):
        plan = self.plan_file(self.task("alpha", "backend/"))
        account = {"SYMBIOSIS_CLAIM_TOKEN": "account-a-" + "k" * 32}
        alpha = self.claim_with_env("alpha", plan, account)
        self.assertEqual(alpha["token_source"], "environment")
        self.assertNotIn("token", alpha)
        self.assertNotIn(account["SYMBIOSIS_CLAIM_TOKEN"], self.git(self.repo, "config", "--list", "--local"))
        shared = ("--remote", "origin", "--claim-id", alpha["claim"])
        fresh = self.root / "new cloud session"
        self.git(self.root, "clone", "--template=", "--quiet", self.bare.as_uri(), str(fresh))
        self.identity(fresh)
        self.cli("renew-claim", "alpha", *shared, "--lease", "2h", repo=fresh,
                 env={"SYMBIOSIS_CLAIM_TOKEN": "account-b-" + "k" * 32}, failure="Only the session holding alpha")
        self.assertEqual(self.cli("renew-claim", "alpha", *shared, "--lease", "2h", repo=fresh, env=account)["lease"], 7200)
        self.claim_with_env("beta", self.plan_file(self.task("beta", "web/")), {"SYMBIOSIS_CLAIM_TOKEN": "short"},
                            failure="at least 32 characters")

    def claim_with_env(self, task, plan, env, failure=None):
        return self.cli("claim", task, "--remote", "origin", "--plan", str(plan), "--owner", task,
                        "--base", self.base, "--branch", f"symbiosis/{task}", env=env, failure=failure)

    def test_plan_scope_and_release(self):
        plan = {"tasks": [
            self.task("alpha", "backend/", resources=["api"]),
            self.task("beta", "reports/", resources=["api"]),
            self.task("gamma", "web/", depends_on=["alpha"]),
            self.task("delta", "ci/"),
        ]}
        file = self.root / "backlog.json"
        file.write_text(json.dumps(plan), encoding="utf-8")
        self.assertEqual(self.cli("plan", str(file))["waves"], [["alpha", "delta"], ["beta", "gamma"]])
        self.cli("plan", "-", payload={"tasks": [self.task("cycle", "web/", depends_on=["cycle"])]},
                 failure="depends on itself")
        options = ("--remote", "origin", "--plan", str(file), "--owner", "dev-run", "--base", self.base)
        self.cli("claim", "gamma", *options, "--branch", "symbiosis/gamma", failure="unfinished dependencies")
        reservation = self.cli("claim", "alpha", *options, "--branch", "symbiosis/alpha")
        self.assertEqual(self.cli("claim", "alpha", *options, "--branch", "symbiosis/alpha"), reservation)
        self.assertEqual(self.cli("plan", str(file), "--remote", "origin")["waves"], [["delta"]])
        shared = ("--remote", "origin", "--claim-id", reservation["claim"])
        worktree = self.root / "task checkout"
        self.cli("worktree", "alpha", *shared, "--path", str(worktree))
        (worktree / "backend" / "new.py").write_text("new\n", encoding="utf-8")
        self.assertEqual(self.cli("check-scope", "alpha", *shared, repo=worktree)["paths"], ["backend/new.py"])
        (worktree / "outside.txt").write_text("outside\n", encoding="utf-8")
        self.cli("check-scope", "alpha", *shared, repo=worktree, failure="outside the reservation")
        self.cli("release-claim", "alpha", *shared, "--owner", "someone-else", "--outcome", "abandoned",
                 failure="belongs to another")
        self.assertTrue(self.cli("release-claim", "alpha", *shared, "--owner", "dev-run",
                                 "--outcome", "abandoned")["released"])
        self.assertFalse(self.cli("release-claim", "alpha", *shared, "--owner", "dev-run",
                                  "--outcome", "abandoned")["released"])
        new_claim = self.cli("claim", "alpha", *options, "--branch", "symbiosis/alpha-next")
        self.assertNotEqual(new_claim["claim"], reservation["claim"])
        self.cli("release-claim", "alpha", *shared, "--owner", "dev-run", "--outcome", "abandoned",
                 failure="Reservation changed")
        latest = ("--remote", "origin", "--claim-id", new_claim["claim"])
        self.source.write_text("def pay():\n    return 2\n", encoding="utf-8")
        self.commit("Merge alpha")
        merged = self.git(self.repo, "rev-parse", "HEAD")
        self.cli("release-claim", "alpha", *latest, "--owner", "dev-run", "--outcome", "merged",
                 "--merge-rev", merged)
        self.assertEqual(self.cli("claims", "--remote", "origin")["completed"], {"alpha": merged})
        self.cli("claim", "alpha", *options, "--branch", "symbiosis/alpha-replay", failure="already complete")
        self.cli("claim", "gamma", *options, "--branch", "symbiosis/gamma", failure="base does not contain alpha")
        self.assertEqual(self.cli("claim", "gamma", *options[:-1], merged,
                                  "--branch", "symbiosis/gamma")["id"], "gamma")

    def test_parallel_agent_journals_keep_original_commits(self):
        self.cli("init", "--base", "main")
        kickoff = journal_event("coordinator", "dispatch", 1, "started")
        path = self.cli("record", "-", payload=kickoff)["path"]
        self.commit("Approve the dispatch context")
        base = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "push", "origin", "main")
        self.git(self.second, "fetch", "origin")
        self.git(self.second, "merge", "--ff-only", "origin/main")

        agents = [
            (self.repo, "checkout", "checkout-agent", "backend/", "backend/pay ment.py"),
            (self.second, "reports", "reports-agent", "reports/", "reports/job.py"),
        ]
        backlog = {"tasks": [
            {**self.task(task, area), "paths": [
                area, f".symbiosis/events/agent-runs/{run}/", f".symbiosis/changes/{task}.json",
            ]}
            for _, task, run, area, _ in agents
        ]}
        plan = self.root / "agent-plan.json"
        plan.write_text(json.dumps(backlog), encoding="utf-8")

        def work(spec):
            clone, task, run, area, source = spec
            worktree = self.root / f"agent worktree {task}"
            branch = f"symbiosis/{task}"
            claim = self.cli("claim", task, "--remote", "origin", "--plan", str(plan), "--owner", task,
                             "--base", base, "--branch", branch, repo=clone)
            lease = ("--remote", "origin", "--claim-id", claim["claim"])
            self.cli("worktree", task, *lease, "--path", str(worktree), repo=clone)
            provider = "antigravity" if task == "reports" else "codex"
            start = journal_event(run, task, 1, "started", provider=provider, parent=kickoff["id"])
            done = journal_event(run, task, 2, "completed", provider=provider,
                                 checks=["Verified the changed source in the task worktree."])
            self.cli("agent-commit", "-", *lease, payload=done, repo=worktree, failure="Commit the previous event")
            initial = self.cli("agent-commit", "-", *lease, payload=start, repo=worktree)
            manifest = self.cli("manifest", task, "--run", run, *lease, repo=worktree)["manifest"]
            source_file = worktree / source
            source_file.parent.mkdir(parents=True, exist_ok=True)
            source_file.write_text(f"# {task} implementation\n", encoding="utf-8")
            self.git(worktree, "add", "--", source, manifest)
            self.git(worktree, "commit", "-m", f"Implement {task}")
            code = self.git(worktree, "rev-parse", "HEAD")
            pending = worktree / area / "pending.txt"
            pending.write_text("Separate staged changes\n", encoding="utf-8")
            pending_path = pending.relative_to(worktree).as_posix()
            self.git(worktree, "add", "--", pending_path)

            done["evidence"] = [{"path": source}]
            final = self.cli("agent-commit", "-", *lease, payload=done, repo=worktree)
            head = final["commit"]
            self.assertEqual(self.git(worktree, "diff", "--cached", "--name-only"), pending_path)
            self.assertEqual(self.git(worktree, "diff-tree", "--no-commit-id", "--name-only", "-r", head), final["path"])
            self.assertIn(f"Symbiosis-Agent: worker/{run}", self.git(worktree, "show", "-s", "--format=%B", head))
            retry = self.cli("agent-commit", "-", *lease, payload=start, repo=worktree)
            self.assertEqual((retry["commit"], retry["created"]), (initial["commit"], False))
            self.cli("agent-commit", "-", *lease, payload={**done, "summary": "Overwrite"},
                     repo=worktree, failure="different content")
            self.git(worktree, "push", "origin", f"HEAD:refs/heads/{branch}")
            return head, code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            heads = list(pool.map(work, agents))

        self.git(self.repo, "fetch", "origin")
        for index, (head, _) in enumerate(heads):
            self.git(self.repo, "merge", "--no-ff", "--no-edit", head)
            if index == 0:
                pr_candidate = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "push", "origin", "main")
        gate = ["verify-history", "--base", base]
        for (head, _), (_, _, run, _, _) in zip(heads, agents, strict=True):
            gate.extend(["--preserve", head, "--require-run", run])
        verified = self.cli(*gate)
        self.assertEqual(verified["new_agent_events"], 4)
        self.assertEqual(verified["completed_runs"], ["checkout-agent", "reports-agent"])

        github_pr = {"pull_request": {
            "base": {"sha": base, "ref": "main", "repo": {"id": 100}},
            "head": {"sha": heads[0][0], "ref": "symbiosis/checkout", "repo": {"id": 100}},
            "user": {"id": 101},
        }}
        pr_options = ("gate", "--event", "-", "--event-name", "pull_request", "--candidate")
        passed = self.cli(*pr_options, pr_candidate, payload=github_pr)
        self.assertEqual(passed["completed_runs"], ["checkout-agent"])
        self.assertEqual((passed["ok"], passed["warnings"]), (True, []))
        group = {"merge_group": {
            "base_sha": base, "base_ref": "refs/heads/main", "head_sha": verified["candidate"],
        }}
        self.assertEqual(self.cli("gate", "--event", "-", "--event-name", "merge_group",
                                  "--candidate", verified["candidate"], payload=group)["completed_runs"],
                         verified["completed_runs"])

        worktree = self.root / "agent worktree checkout"
        (worktree / "outside.txt").write_text("Outside the reservation\n", encoding="utf-8")
        self.git(worktree, "add", "--", "outside.txt")
        self.git(worktree, "commit", "--only", "-m", "Out-of-scope change", "--", "outside.txt")
        rogue = self.git(worktree, "rev-parse", "HEAD")
        github_pr["pull_request"]["head"]["sha"] = rogue
        self.cli(*pr_options, rogue, payload=github_pr, failure="Paths outside the reservation")

        leak = worktree / "docs" / "symbiosis" / "notes.md"
        leak.parent.mkdir(parents=True)
        leak.write_text("token: " + "ghp_" + "Q" * 36 + "\n", encoding="utf-8")
        self.git(worktree, "add", "--", "docs/symbiosis/notes.md")
        self.git(worktree, "commit", "--only", "-m", "Leak into memory", "--", "docs/symbiosis/notes.md")
        rogue = self.git(worktree, "rev-parse", "HEAD")
        github_pr["pull_request"]["head"]["sha"] = rogue
        summary = self.root / "step-summary.md"
        failed = self.raw_cli(*pr_options, rogue, payload=github_pr, env={"GITHUB_STEP_SUMMARY": str(summary)})
        self.assertEqual(failed.returncode, 1, failed.stderr)
        report = json.loads(failed.stdout)
        self.assertFalse(report["ok"])
        self.assertEqual([row["check"] for row in report["problems"]], ["scope", "privacy"])
        scope_problem, privacy_problem = report["problems"]
        self.assertEqual(scope_problem["paths"], ["docs/symbiosis/notes.md", "outside.txt"])
        claim_id = self.cli("claims", "--remote", "origin")["claims"]["checkout"]["claim"]
        self.assertIn(f"extend-claim checkout --remote origin --claim-id {claim_id} "
                      "--path docs/symbiosis/notes.md --path outside.txt", scope_problem["fix"])
        self.assertEqual((privacy_problem["path"], privacy_problem["line"]), ("docs/symbiosis/notes.md", 1))
        self.assertNotIn("Q" * 36, failed.stdout + failed.stderr)
        self.assertIn("gate found 2 problem(s)", failed.stderr)
        self.assertIn("## Symbiosis gate: 2 problem(s)", summary.read_text(encoding="utf-8"))

        squash = self.git(self.repo, "commit-tree", "HEAD^{tree}", "-p", base, "-m", "Squashed candidate")
        self.cli(*gate, "--candidate", squash, failure="missing original commit")
        self.cli("verify-history", "--base", base, "--candidate", squash, failure="missing agent work commit")

        fresh = self.root / "fresh reviewer"
        self.git(self.root, "clone", "--template=", "--local", str(self.bare), str(fresh))
        for (_, code), (_, _, run, _, _) in zip(heads, agents, strict=True):
            record = self.cli("memory", "--ref", "origin/main", "run", run, repo=fresh)
            self.assertEqual(record["parent"], kickoff["id"])
            self.assertEqual(record["phase"], "completed")
            self.assertEqual(record["events"][-1]["revision"], code)
        self.assertEqual(self.cli(*gate, repo=fresh), verified)

        new_event_file = self.repo / ".symbiosis/events/agent-runs/reports-agent/agent-reports-agent-0002.json"
        new_event_file.write_text(new_event_file.read_text(encoding="utf-8").replace("Completed reports", "Edited reports"),
                                  encoding="utf-8")
        self.commit("Attempt to edit a new journal event after its original commit")
        self.cli(*gate, failure="Agent event changed after its original journal commit")

        event_file = self.repo / path
        event_file.write_text(event_file.read_text(encoding="utf-8").replace("Started dispatch", "Edited dispatch"),
                              encoding="utf-8")
        self.commit("Attempt to overwrite historical journal")
        self.cli(*gate, failure="Historical event rewritten")


if __name__ == "__main__":
    unittest.main()
