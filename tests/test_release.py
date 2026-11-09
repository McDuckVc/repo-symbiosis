"""Exercise release bootstrapping, privacy, and App trust boundaries."""

import copy
import base64
import hashlib
import hmac
import io
import json
import importlib.util
import unittest
import uuid
from unittest.mock import patch

from test_symbiosis import RepoFixture
from datetime import datetime, timedelta, timezone

from scripts import app_server, delivery, github_app, github_gate, privacy
from scripts.github_api import APIError, GitHub
from scripts.repository import SymbiosisError


class ReleaseTests(RepoFixture):
    def test_init_is_idempotent_and_doctor_reports_install(self):
        self.git(self.repo, "remote", "add", "origin", str(self.repo))
        wheel = "https://github.com/example/symbiosis/releases/download/v0.1.0/repo_symbiosis-0.1.0-py3-none-any.whl#sha256="
        options = ("init", "--base", "main", "--checker", wheel + "a" * 64)
        self.assertEqual(len(self.cli(*options)["created"]), 2)
        self.assertEqual(self.cli(*options)["created"], [])
        workflow = (self.repo / ".github/workflows/symbiosis.yml").read_text()
        self.assertIn("merge_group:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertTrue(self.cli("doctor")["workflow_present"])
        self.cli("init", "--base", "main", "--mode", "github-app", failure="Existing file differs")
        self.cli("init", "--base", "main", "--checker", "https://example.invalid/payload", failure="Checker must")

    def test_scanner_checks_staged_content_and_masks_secret(self):
        doc = self.repo / "docs/symbiosis/README.md"
        doc.parent.mkdir(parents=True)
        fake = "ghp_" + "Z" * 36
        doc.write_text(f"# Handoff\ncredential: {fake}\n", encoding="utf-8")
        self.git(self.repo, "add", "--", "docs/symbiosis/README.md")
        doc.write_text("# Safe worktree copy\n", encoding="utf-8")
        result = self.raw_cli("scan-memory")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(fake, result.stdout + result.stderr)
        findings = json.loads(result.stdout)["findings"]
        self.assertEqual(findings, [{"path": "docs/symbiosis/README.md", "line": 2, "rule": "github-token"}])
        self.cli("record", "-", payload={"id": "unsafe", "kind": "decision", "summary": fake}, failure="privacy check")
        self.git(self.repo, "add", "--", "docs/symbiosis/README.md")
        self.assertEqual(self.cli("scan-memory")["findings"], [])

    def test_webhook_validates_signature_and_deduplicates_delivery(self):
        queue = app_server.Queue(self.root / "state.sqlite")
        secret = b"s" * 32
        app = app_server.webhook(queue, secret, {10})
        job = {"action": "created", "issue": {"number": 2, "state": "open"}, "sender": {"id": 2, "login": "alice"},
               "comment": {"id": 3, "body": "/symbiosis claim", "user": {"id": 2, "login": "alice"}},
               "repository": {"id": 10, "full_name": "sample/project", "default_branch": "main"},
               "installation": {"id": 9}}
        data = json.dumps(job).encode()
        event_id = str(uuid.uuid4())
        signature = "sha256=" + hmac.new(secret, data, hashlib.sha256).hexdigest()

        def request(signed):
            seen = []
            app({
                "PATH_INFO": "/github/webhook", "REQUEST_METHOD": "POST", "CONTENT_LENGTH": str(len(data)),
                "HTTP_X_HUB_SIGNATURE_256": signed, "HTTP_X_GITHUB_DELIVERY": event_id,
                "HTTP_X_GITHUB_EVENT": "issue_comment", "wsgi.input": io.BytesIO(data),
            }, lambda status, _: seen.append(status))
            return seen[0]

        self.assertEqual(request("sha256=invalid"), "401 Unauthorized")
        self.assertIsNone(queue.take())
        self.assertEqual(request(signature), "202 Accepted")
        self.assertEqual(request(signature), "202 Accepted")
        delivery, selected, attempts, token = queue.take()
        self.assertEqual(selected["actor"]["id"], 2)
        self.assertEqual(selected["command"], "claim")
        self.assertNotIn("body", selected)
        self.assertIsNone(queue.take())
        queue.finish(delivery, token, attempts, "Permission", retry=False)
        self.assertEqual(queue.status()["counts"], {"failed": 1})
        queue.retry(delivery)
        delivery, _, attempts, token = queue.take()
        queue.finish(delivery, token, attempts)
        self.assertEqual(queue.status()["counts"], {"done": 1})
        self.assertEqual(request(signature), "202 Accepted")
        self.assertIsNone(queue.take())


BASE = "a" * 40
HEAD = "b" * 40
MERGE = "c" * 40


class FakeGitHub:
    def __init__(self):
        self.state = {"schema": 1, "claims": {}, "completed": {}}
        self.files = {
            github_gate.POLICY: {"schema": 1, "base": "main", "mode": "github-app"},
            github_gate.BACKLOG: {"tasks": [
                {"id": "issue-2", "title": "Work", "paths": ["src/", ".symbiosis/changes/issue-2.json"]},
            ]},
        }
        self.users = {
            "alice": {"id": 2, "login": "alice", "permission": "write", "role": "write"},
            "bob": {"id": 3, "login": "bob", "permission": "write", "role": "write"},
            "maintainer": {"id": 4, "login": "maintainer", "permission": "write", "role": "maintain"},
        }
        self.responses = {}

    def ref(self, branch):
        return MERGE

    def file(self, path, revision):
        return json.dumps(self.files[path]).encode()

    def user(self, login):
        return self.users[login]

    def contains(self, before, after):
        return before == after or (before, after) == (HEAD, MERGE)

    def ledger(self):
        return BASE, copy.deepcopy(self.state)

    def call(self, method, path, body=None):
        return copy.deepcopy(self.responses[path])

    def update(self, operation, message):
        next_state = copy.deepcopy(self.state)
        if operation(next_state):
            self.state = next_state


class PrivacyRuleTests(unittest.TestCase):
    def rules(self, text):
        return [row["rule"] for row in privacy.inspect("docs/symbiosis/notes.md", text.encode())]

    def test_each_credential_format_is_detected(self):
        # Samples are assembled at runtime so the repository never contains token-shaped literals.
        samples = {
            "google-api-key": "key " + "AIza" + "B" * 35,
            "stripe-key": "sk_" + "live_" + "C" * 24,
            "gitlab-token": "glpat" + "-" + "D" * 20,
            "npm-token": "npm" + "_" + "E" * 36,
            "pypi-token": "pypi" + "-AgEIcHlwaS5vcmc" + "F" * 60,
            "huggingface-token": "hf" + "_" + "G" * 34,
            "sendgrid-key": "SG" + "." + "H" * 22 + "." + "I" * 43,
            "telegram-bot-token": "123456789" + ":AA" + "J" * 33,
            "slack-webhook": "https://hooks.slack.com" + "/services/T0001/B0002/" + "K" * 24,
            "azure-storage-key": "Account" + "Key=" + "L" * 88,
            "jwt": "eyJ" + "M" * 20 + ".eyJ" + "N" * 20 + "." + "O" * 20,
            "bearer-header": "Authorization: " + "Bearer " + "P" * 32,
            "env-secret": "OPENROUTER_" + "API_KEY=" + "q1w2e3r4t5y6u7i8",
        }
        for rule, text in samples.items():
            with self.subTest(rule=rule):
                self.assertEqual(self.rules(text), [rule])

    def test_placeholders_and_references_are_ignored(self):
        for text in (
            "API_TOKEN=${API_TOKEN}",
            "API_TOKEN=<from the secret manager>",
            "SECRET_KEY=your-secret-key-here",
            "DB_PASSWORD=changeme-in-production",
            "API_TOKEN=REDACTED_REDACTED",
            "TOKEN=short",
            "MAX_TOKENS=4096",
            "export GITHUB_TOKEN=\"$GITHUB_TOKEN\"",
            "Use the Authorization header with a bearer token from the vault.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.rules(text), [])

    def test_bare_secret_environment_names_are_detected(self):
        for name in ("API_KEY", "PASSWORD", "TOKEN", "ACCESS_KEY"):
            with self.subTest(name=name):
                self.assertEqual(self.rules(f"export {name}=" + "q1w2e3r4t5y6u7i8"), ["env-secret"])


class AppTests(unittest.TestCase):
    def test_app_scope_extension_checks_identity_claim_and_overlap(self):
        api = FakeGitHub()
        api.files[github_gate.BACKLOG]["tasks"].append({"id": "issue-3", "title": "Other", "paths": ["reports/"]})
        job = {"event": "issue_comment", "repo": "sample/project", "repo_id": 10, "base": "main",
               "issue": 2, "comment": 100, "actor": {"id": 2, "login": "alice"}, "command": "claim"}
        github_app.process(api, job)
        github_app.process(api, {**job, "issue": 3, "comment": 101})
        original = copy.deepcopy(api.state["claims"]["issue-2"])
        holder = {"id": 4, "login": "maintainer"}
        payload = {"action": "created", "issue": {"number": 2, "state": "open"},
                   "comment": {"body": f"/symbiosis extend {original['claim']} docs/team guide.md", "id": 102, "user": holder},
                   "sender": holder, "repository": {"id": 10, "full_name": "sample/project", "default_branch": "main"},
                   "installation": {"id": 5}}
        extension = github_app.selected_job("issue_comment", payload, {10})
        self.assertEqual((extension["command"], extension["path"]), ("extend", "docs/team guide.md"))

        with self.assertRaisesRegex(SymbiosisError, "maintainer"):
            github_app.process(api, {**extension, "actor": job["actor"]})
        with self.assertRaisesRegex(SymbiosisError, "changed"):
            github_app.process(api, {**extension, "claim": "0" * 32})
        with self.assertRaisesRegex(SymbiosisError, "Extension overlaps issue-3"):
            github_app.process(api, {**extension, "path": "REPORTS/result.md"})
        with self.assertRaisesRegex(SymbiosisError, "literals"):
            github_app.process(api, {**extension, "path": "src/*.py"})
        self.assertEqual(api.state["claims"]["issue-2"], original)

        github_app.process(api, extension)
        github_app.process(api, {**extension, "path": "docs/replayed.md"})
        active = api.state["claims"]["issue-2"]
        self.assertEqual(active, {**original, "paths": sorted(original["paths"] + [extension["path"]])})
        self.assertEqual(api.state["receipts"]["comment-102"]["path"], extension["path"])
        self.assertIn("issue #2", delivery.extend_command(active, ["docs/another.md"]))

    @unittest.skipUnless(importlib.util.find_spec("cryptography"), "Optional App crypto dependency is not installed")
    def test_app_jwt_and_repository_scoped_installation_token(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        with patch("scripts.github_api.request", return_value={"token": "local-test"}) as request:
            GitHub(17, pem, {"installation": 27, "repo_id": 10, "repo": "sample/project"})
        method, route, signed, scope = request.call_args.args
        self.assertEqual((method, route), ("POST", "/app/installations/27/access_tokens"))
        self.assertEqual(scope["repository_ids"], [10])
        header, claims, signature = signed.split(".")
        decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        self.assertEqual(json.loads(decode(claims))["iss"], "17")
        key.public_key().verify(decode(signature), f"{header}.{claims}".encode("ascii"),
                                padding.PKCS1v15(), hashes.SHA256())

    def test_identity_transfer_and_pr_reconciliation(self):
        api = FakeGitHub()
        task = "issue-2"
        job = {"event": "issue_comment", "repo": "sample/project", "repo_id": 10, "base": "main", "issue": 2,
               "comment": 100, "actor": {"id": 2, "login": "alice"}, "command": "claim"}
        github_app.process(api, job)
        claimed = api.state["claims"][task]["claim"]
        github_app.process(api, job)
        self.assertEqual(api.state["claims"][task]["claim"], claimed)
        with self.assertRaisesRegex(SymbiosisError, "maintainer"):
            github_app.process(api, {**job, "comment": 101, "command": "transfer", "target": "bob"})
        github_app.process(api, {**job, "comment": 102, "command": "transfer", "target": "bob",
                                 "actor": {"id": 4, "login": "maintainer"}})
        active = api.state["claims"][task]
        self.assertNotEqual(active["claim"], claimed)
        self.assertEqual(active["github"]["id"], 3)
        api.files[".symbiosis/changes/issue-2.json"] = {"task": task, "run": "run-2", "claim": active["claim"]}
        api.responses["/pulls/7"] = {
            "state": "closed", "base": {"ref": "main", "repo": {"id": 10}},
            "head": {"ref": active["branch"], "repo": {"id": 10}, "sha": HEAD},
            "user": {"id": 2}, "merged": True, "merge_commit_sha": MERGE,
        }
        pr_job = {**job, "event": "pull_request", "pr": 7}
        github_app.process(api, pr_job)
        self.assertIn(task, api.state["claims"])
        api.responses["/pulls/7"]["user"]["id"] = 3

        def concurrent_extension(operation, message):
            # The PR-close handler already read the old claim when this update starts.
            api.state["claims"][task]["paths"].append("docs/after-review.md")
            saved_update(operation, message)

        saved_update = api.update
        with patch.object(api, "update", side_effect=concurrent_extension):
            github_app.process(api, pr_job)
        github_app.process(api, pr_job)
        self.assertEqual(api.state["claims"], {})
        self.assertEqual(api.state["completed"], {task: MERGE})
        self.assertEqual(len(api.state["receipts"]), 3)

        api.responses["/deployments/1"] = {"sha": MERGE, "environment": "staging"}
        api.responses["/deployments/1/statuses/3"] = {"state": "success"}
        deployment = {**job, "event": "deployment_status", "deployment": 1, "status": 3}
        github_app.process(api, deployment)
        github_app.process(api, deployment)
        self.assertEqual(api.state["receipts"]["deployment-1-3"]["revision"], MERGE)
        self.assertEqual(len(api.state["receipts"]), 4)

    def test_ref_write_rechecks_when_another_writer_wins(self):
        api = GitHub.__new__(GitHub)
        first = {"schema": 1, "claims": {}, "completed": {}}
        second = {**first, "receipts": {"other": {"recorded_at": "1"}}}
        writes = []

        def call(method, path, body=None):
            writes.append((method, path, body))
            if path == "/git/trees":
                return {"sha": HEAD}
            if path == "/git/commits":
                return {"sha": MERGE}
            if len([row for row in writes if row[0] == "PATCH"]) == 1:
                raise APIError(422)

        with (
            patch.object(api, "ledger", side_effect=[(BASE, copy.deepcopy(first)), (HEAD, copy.deepcopy(second))]),
            patch.object(api, "ref", return_value=HEAD),
            patch.object(api, "contains", return_value=True),
            patch.object(api, "call", side_effect=call),
        ):
            api.update(lambda state: github_app.receipt(state, "mine", {"kind": "test"}), "Receipt")
        snapshots = [json.loads(body["tree"][0]["content"]) for _, path, body in writes if path == "/git/trees"]
        self.assertEqual(set(snapshots[-1]["receipts"]), {"other", "mine"})
        self.assertTrue(all(body["force"] is False for method, _, body in writes if method == "PATCH"))

    def test_receipt_pruning_keeps_recent_replay_guards(self):
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        stamp = lambda days, i: (now - timedelta(days=days, seconds=i)).isoformat()
        state = {"receipts": {f"old-{i}": {"recorded_at": stamp(30, i)} for i in range(300)}}
        state["receipts"].update({f"new-{i}": {"recorded_at": stamp(1, i)} for i in range(delivery.MAX_RECEIPTS)})
        state["receipts"]["broken"] = {"recorded_at": "not-a-time"}
        self.assertEqual(delivery.prune_receipts(state, now), 301)
        self.assertEqual(set(state["receipts"]), {f"new-{i}" for i in range(delivery.MAX_RECEIPTS)})

        recent = {"receipts": {f"r-{i}": {"recorded_at": stamp(2, i)} for i in range(delivery.MAX_RECEIPTS + 50)}}
        self.assertEqual(delivery.prune_receipts(recent, now), 0)
        self.assertEqual(len(recent["receipts"]), delivery.MAX_RECEIPTS + 50)

        small = {"receipts": {"only": {"recorded_at": stamp(400, 0)}}}
        self.assertEqual(delivery.prune_receipts(small, now), 0)
        self.assertEqual(delivery.prune_receipts({"schema": 1}, now), 0)

    def test_app_ledger_write_prunes_old_receipts(self):
        api = GitHub.__new__(GitHub)
        old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        state = {"schema": 1, "claims": {}, "completed": {}, "receipts": {
            f"deployment-{i}-{i}": {"kind": "deployment", "recorded_at": (old + timedelta(seconds=i)).isoformat()}
            for i in range(delivery.MAX_RECEIPTS + 200)
        }}
        writes = []

        def call(method, path, body=None):
            writes.append((method, path, body))
            return {"sha": HEAD} if path == "/git/trees" else {"sha": MERGE} if path == "/git/commits" else None

        with (
            patch.object(api, "ledger", return_value=(BASE, copy.deepcopy(state))),
            patch.object(api, "call", side_effect=call),
        ):
            api.update(lambda current: github_app.receipt(current, "mine", {"kind": "test"}), "Receipt")
        written = json.loads(next(body for _, path, body in writes if path == "/git/trees")["tree"][0]["content"])
        self.assertEqual(len(written["receipts"]), delivery.MAX_RECEIPTS)
        self.assertIn("mine", written["receipts"])
        self.assertNotIn("deployment-0-0", written["receipts"])
