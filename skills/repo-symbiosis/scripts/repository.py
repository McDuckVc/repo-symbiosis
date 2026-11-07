"""Read immutable Git snapshots and classify files for repository analysis."""

import json
import os
import re
import subprocess
from io import BytesIO
from pathlib import Path, PurePosixPath


MEMORY = ".symbiosis/events"
OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class SymbiosisError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise SymbiosisError(message)


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("ascii")


def read_json(data, label):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"{label}: duplicate key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as error:
        raise SymbiosisError(f"{label}: {error}") from error


def path_name(value):
    require(isinstance(value, str) and value and not re.search(r'[\\:\x00-\x1f\x7f]', value),
            f"Invalid repository path: {value!r}")
    require(not any(part in ("", ".", "..", ".git") or part.casefold() == ".git"
                    for part in value.split("/")), f"Invalid repository path: {value!r}")
    return value


class Repository:
    def __init__(self, directory, *, committed_read=False):
        self._env = None
        if committed_read:
            self._env = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1")
            for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_NAMESPACE",
                        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
                self._env.pop(key, None)
        self.root = Path(directory).resolve()
        self.root = Path(os.fsdecode(self.git("rev-parse", "--show-toplevel").rstrip(b"\r\n")))
        self._trees = {}

    def run(self, *args, data=None):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            input=data, capture_output=True, timeout=120, check=False, env=self._env,
            stdin=subprocess.DEVNULL if self._env is not None and data is None else None,
        )

    def git(self, *args, data=None):
        result = self.run(*args, data=data)
        if result.returncode:
            detail = result.stderr.decode(errors="replace").strip()
            raise SymbiosisError(f"git {args[0]} failed: {detail or result.returncode}")
        return result.stdout

    def revision(self, value):
        return self.git("rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}").decode().strip()

    def tree(self, revision):
        if revision not in self._trees:
            raw = self.git("ls-tree", "-rlz", "--full-tree", revision)
            files = {}
            for row in raw.split(b"\0"):
                if not row:
                    continue
                info, name = row.split(b"\t", 1)
                mode, kind, oid, size = info.decode().split()
                files[name.decode("utf-8", "surrogateescape")] = {
                    "blob": oid, "mode": mode, "kind": kind, "size": int(size) if size != "-" else None,
                }
            self._trees[revision] = files
        return self._trees[revision]

    def blob(self, oid):
        require(isinstance(oid, str) and OID.fullmatch(oid), "Invalid blob ID")
        return self.git("cat-file", "blob", oid)

    def blobs(self, object_ids):
        ids = list(dict.fromkeys(object_ids))
        if not ids:
            return {}
        require(all(OID.fullmatch(oid) for oid in ids), "Invalid blob ID")
        stream = BytesIO(self.git("cat-file", "--batch", data=("\n".join(ids) + "\n").encode("ascii")))
        result = {}
        for oid in ids:
            fields = stream.readline().split()
            require(len(fields) == 3 and fields[:2] == [oid.encode("ascii"), b"blob"],
                    f"Missing or invalid Git blob {oid}")
            size = int(fields[2])
            content = stream.read(size)
            require(len(content) == size and stream.read(1) == b"\n", f"Incomplete Git blob {oid}")
            result[oid] = content
        return result

    def changed(self, before, after):
        old, new = self.tree(before), self.tree(after)
        return sorted(path for path in old.keys() | new.keys() if old.get(path) != new.get(path))


def classify(path, entry):
    parts = path.casefold().split("/")
    name = parts[-1]
    extension = PurePosixPath(name).suffix
    try:
        path_name(path)
        path.encode("utf-8")
    except (SymbiosisError, UnicodeError):
        return "excluded", "path requires manual review"
    if parts[0] == ".symbiosis" or parts[:2] == ["docs", "symbiosis"]:
        return "excluded", "generated project memory"
    if entry["mode"] in ("120000", "160000"):
        return "excluded", "symlink or submodule boundary"
    if (
        name == ".env" or name.startswith(".env.") and not name.endswith((".example", ".sample", ".template"))
        or extension in {".key", ".pem", ".pfx", ".p12", ".jks", ".sqlite", ".sqlite3", ".db"}
        or name in {"credentials", "credentials.json", "id_rsa", "id_ed25519", ".npmrc", ".netrc"}
    ):
        return "excluded", "possible credentials or local data"
    if any(part in {"node_modules", ".venv", "__pycache__"} for part in parts[:-1]):
        return "excluded", "generated dependency files"
    if extension in {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".mp3", ".mp4", ".mov", ".zip",
        ".gz", ".pdf", ".woff", ".woff2", ".exe", ".dll", ".so", ".jar", ".wasm", ".parquet",
    }:
        return "excluded", "binary or archive"
    if parts[:2] == [".github", "workflows"] or name in {"jenkinsfile", ".gitlab-ci.yml", "azure-pipelines.yml"}:
        return "ci", None
    if (
        extension in {".tf", ".tfvars", ".bicep"} or name.startswith(("dockerfile", "docker-compose", "compose."))
        or any(part in {"infra", "terraform", "deploy", "k8s", "helm", "pulumi"} for part in parts[:-1])
    ):
        return "deployment", None
    if extension in {".sql", ".prisma"} or any(part in {"migration", "migrations", "schema"} for part in parts[:-1]):
        return "data", None
    if any(part in {"test", "tests", "__tests__", "spec", "specs"} for part in parts[:-1]) or (
        name.startswith("test_") or ".test." in name or ".spec." in name or name.endswith("_test.go")
    ):
        return "test", None
    if extension in {".md", ".rst", ".adoc"}:
        return "documentation", None
    if extension in {".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".lock", ".xml"} or name in {
        "makefile", "gemfile", "requirements.txt", "go.mod", "go.sum",
    }:
        return "configuration", None
    return "source-or-other", None


def inventory(repo, revision, since, size, shard):
    current = repo.tree(revision)
    paths = sorted(current) if since is None else repo.changed(since, revision)
    groups = {}
    deleted = []
    excluded = []
    for path in paths:
        if path not in current:
            deleted.append(path)
            continue
        entry = current[path]
        category, reason = classify(path, entry)
        if reason:
            excluded.append({"path": path, "reason": reason})
            continue
        area = path.split("/", 1)[0] if "/" in path else "(root)"
        groups.setdefault(area, []).append({"path": path, "category": category, **entry})

    batches = []
    for area, files in groups.items():
        for index in range(0, len(files), size):
            batches.append((area, files[index:index + size]))

    result = {"revision": revision, "since": since}
    if shard is not None:
        require(1 <= shard <= len(batches), f"Shard must be between 1 and {len(batches)}")
        area, files = batches[shard - 1]
        return {**result, "shard": shard, "area": area, "files": files}
    return {
        **result,
        "shards": [{"id": i, "area": area, "files": len(files)}
                   for i, (area, files) in enumerate(batches, 1)],
        "deleted": deleted,
        "excluded": excluded,
    }
