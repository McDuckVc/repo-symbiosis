"""Self-hosted webhook ingress and durable background worker for Repo Symbiosis."""

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path

from . import github_app
from .github_api import APIError, GitHub
from .repository import SymbiosisError, json_bytes, read_json, require


DELIVERY = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z", re.I)
BODY_LIMIT = 1024 * 1024
MAX_ATTEMPTS = 8
LEASE = 600


class Queue:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
                ready INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                lease TEXT, error TEXT
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status, ready)")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def add(self, delivery, job):
        with closing(self.connect()) as db:
            db.execute("INSERT OR IGNORE INTO jobs(id, payload, status, ready) VALUES (?, ?, 'queued', ?)",
                       (delivery, json.dumps(job), int(time.time())))

    def take(self):
        now = int(time.time())
        token = uuid.uuid4().hex
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE jobs SET status = 'failed', lease = NULL, error = 'retry budget exhausted'
                          WHERE status = 'running' AND ready <= ? AND attempts >= ?""", (now, MAX_ATTEMPTS))
            row = db.execute("""SELECT id, payload, attempts FROM jobs
                                WHERE status IN ('queued', 'running') AND ready <= ?
                                ORDER BY ready, id LIMIT 1""", (now,)).fetchone()
            if row:
                delivery, payload, attempts = row
                db.execute("""UPDATE jobs SET status = 'running', ready = ?, attempts = ?, lease = ?
                              WHERE id = ?""", (now + LEASE, attempts + 1, token, delivery))
            db.execute("COMMIT")
        return (delivery, json.loads(payload), attempts + 1, token) if row else None

    def finish(self, delivery, token, attempts, error=None, retry=False):
        status = "queued" if error and retry and attempts < MAX_ATTEMPTS else "failed" if error else "done"
        ready = int(time.time()) + min(2 ** attempts * 15, 900) if status == "queued" else 0
        with closing(self.connect()) as db:
            db.execute("""UPDATE jobs SET status = ?, ready = ?, error = ?, lease = NULL,
                          payload = CASE WHEN ? = 'done' THEN '{}' ELSE payload END
                          WHERE id = ? AND lease = ?""", (status, ready, error, status, delivery, token))

    def status(self):
        with closing(self.connect()) as db:
            counts = dict(db.execute("SELECT status, count(*) FROM jobs GROUP BY status"))
            failed = db.execute("SELECT id, error, attempts FROM jobs WHERE status = 'failed' ORDER BY id LIMIT 50")
            return {"counts": counts, "failures": [{"id": i, "error": e, "attempts": a} for i, e, a in failed]}

    def retry(self, delivery):
        require(DELIVERY.fullmatch(delivery), "Expected a GitHub delivery UUID")
        with closing(self.connect()) as db:
            count = db.execute("""UPDATE jobs SET status = 'queued', attempts = 0, ready = ?, error = NULL
                                  WHERE id = ? AND status = 'failed'""",
                               (int(time.time()), delivery.lower())).rowcount
        require(count == 1, "Delivery is not in the failed queue")


def webhook(queue, secret, allowed):
    def app(environ, respond):
        path = environ["PATH_INFO"]
        if environ["REQUEST_METHOD"] == "GET" and path == "/health":
            return reply(respond, "200 OK", {"status": "ok"})
        if environ["REQUEST_METHOD"] != "POST" or path != "/github/webhook":
            return reply(respond, "404 Not Found", {"status": "unknown route"})
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
            require(0 < length <= BODY_LIMIT, "Invalid webhook body length")
            body = environ["wsgi.input"].read(length)
            require(len(body) == length, "Incomplete webhook")
            expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
            signature = environ.get("HTTP_X_HUB_SIGNATURE_256", "")
            if not hmac.compare_digest(signature, expected):
                return reply(respond, "401 Unauthorized", {"status": "invalid signature"})
            delivery = environ.get("HTTP_X_GITHUB_DELIVERY", "")
            require(DELIVERY.fullmatch(delivery), "Invalid delivery ID")
            payload = read_json(body, "webhook")
            require(isinstance(payload, dict), "Expected a webhook JSON object")
            job = github_app.selected_job(environ.get("HTTP_X_GITHUB_EVENT"), payload, allowed)
            if job:
                queue.add(delivery.lower(), job)
        except sqlite3.Error:
            return reply(respond, "503 Service Unavailable", {"status": "queue unavailable"})
        except (SymbiosisError, KeyError, ValueError, TypeError, AttributeError):
            return reply(respond, "400 Bad Request", {"status": "invalid or unapproved event"})
        return reply(respond, "202 Accepted", {"status": "accepted"})
    return app


def reply(respond, status, payload):
    body = json_bytes(payload)
    respond(status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
    return [body]


def run_job(queue, app_id, key, allowed):
    item = queue.take()
    if item is None:
        return False
    delivery, job, attempts, token = item
    try:
        require(job["repo_id"] in allowed, "Repository has been removed from the allowlist")
        github_app.process(GitHub(app_id, key, job), job)
    except APIError as error:
        queue.finish(delivery, token, attempts, str(error),
                     retry=error.status in (403, 404, 409, 429) or error.status >= 500)
    except (SymbiosisError, KeyError, ValueError, TypeError):
        queue.finish(delivery, token, attempts, "Invalid configuration, permission, or GitHub event")
    else:
        queue.finish(delivery, token, attempts)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Durable SQLite path shared by webhook and worker")
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("serve")
    server.add_argument("--listen", default="127.0.0.1")
    server.add_argument("--port", default=8080, type=int)
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    sub.add_parser("status")
    retry = sub.add_parser("retry")
    retry.add_argument("delivery")
    args = parser.parse_args()
    try:
        queue = Queue(args.db)
        if args.command == "status":
            sys.stdout.buffer.write(json_bytes(queue.status()))
        elif args.command == "retry":
            queue.retry(args.delivery)
        else:
            raw = os.getenv("SYMBIOSIS_GITHUB_REPOSITORY_IDS", "")
            allowed = {github_app.positive(int(part)) for part in raw.split(",") if part.strip()}
            require(allowed, "Set SYMBIOSIS_GITHUB_REPOSITORY_IDS")
            if args.command == "serve":
                from waitress import serve

                secret = os.environ["SYMBIOSIS_GITHUB_WEBHOOK_SECRET"].encode()
                require(len(secret) >= 32, "Use at least 32 bytes for the GitHub webhook secret")
                serve(webhook(queue, secret, allowed), host=args.listen, port=args.port,
                      max_request_body_size=BODY_LIMIT, threads=4)
            else:
                app_id = github_app.positive(int(os.environ["SYMBIOSIS_GITHUB_APP_ID"]))
                key = Path(os.environ["SYMBIOSIS_GITHUB_PRIVATE_KEY_FILE"]).read_bytes()
                while True:
                    worked = run_job(queue, app_id, key, allowed)
                    if args.once:
                        break
                    if not worked:
                        time.sleep(2)
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, KeyError, ImportError, SymbiosisError, ValueError, sqlite3.Error) as error:
        print(f"symbiosis-app: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
