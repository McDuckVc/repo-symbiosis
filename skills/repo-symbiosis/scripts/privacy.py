"""Detect recognizable credentials in Git-tracked memory without printing values."""

import re

from .repository import MEMORY, path_name, require


RULES = {
    "private-key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "service-token": re.compile(r"\b(?:xox[baprs]-[A-Za-z0-9-]{12,}|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,})\b"),
    "url-credentials": re.compile(r"(?i)\b(?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s/@:]+:[^\s/@]+@"),
    "literal-secret": re.compile(
        r"""(?i)\b(?:password|client_secret|api[_-]?key|access_token|secret_access_key)["']?\s*[:=]\s*["'][^"'\s<>$`{}]{12,}["']"""
    ),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"),
    "stripe-key": re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}\b"),
    "gitlab-token": re.compile(r"\bglpat-[0-9A-Za-z_-]{20,}"),
    "npm-token": re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    "pypi-token": re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}"),
    "huggingface-token": re.compile(r"\bhf_[A-Za-z0-9]{34,}\b"),
    "sendgrid-key": re.compile(r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}"),
    "telegram-bot-token": re.compile(r"\b[0-9]{8,10}:AA[A-Za-z0-9_-]{33}"),
    "slack-webhook": re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]{16,}"),
    "azure-storage-key": re.compile(r"(?i)\bAccountKey=[A-Za-z0-9+/]{80,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "bearer-header": re.compile(r"""(?i)\bauthorization["']?\s*[:=]\s*["']?bearer\s+[A-Za-z0-9._~+/=-]{20,}"""),
    # Unquoted environment output such as API_TOKEN=abc..., skipping variables and obvious placeholders.
    "env-secret": re.compile(
        r"""^\s*(?:export\s+)?[A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|ACCESS_KEY)"""
        r"""[A-Z0-9_]*\s*=\s*(?!["'$<{])(?!(?i:\S*(?:example|placeholder|changeme|redacted|dummy|your[_-]|xxxx)))"""
        r"""[^\s#]{12,}"""
    ),
}
MAX_MEMORY_BYTES = 2 * 1024 * 1024


def memory_path(path):
    return path.startswith(f"{MEMORY}/") or path.startswith("docs/symbiosis/")


def inspect(path, data):
    path_name(path)
    if len(data) > MAX_MEMORY_BYTES:
        return [{"path": path, "line": None, "rule": "memory-file-too-large"}]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        return [{"path": path, "line": None, "rule": "memory-must-be-utf8"}]
    if "\0" in text:
        return [{"path": path, "line": None, "rule": "memory-must-be-text"}]
    return [{"path": path, "line": number, "rule": name}
            for number, line in enumerate(text.splitlines(), 1)
            for name, pattern in RULES.items() if pattern.search(line)]


def reject(findings):
    if findings:
        first = findings[0]
        location = f"{first['path']}:{first['line']}" if first["line"] else first["path"]
        require(False, f"Memory privacy check failed at {location} ({first['rule']}); {len(findings)} finding(s)")


def scan(repo, base=None, candidate=None):
    findings = []
    checked = 0
    if candidate:
        tree = repo.tree(candidate)
        changed = repo.changed(base, candidate)
        for path in changed:
            if memory_path(path) and path in tree:
                entry = tree[path]
                checked += 1
                if entry["mode"] != "100644":
                    findings.append({"path": path, "line": None, "rule": "memory-must-be-a-regular-file"})
                elif entry["size"] > MAX_MEMORY_BYTES:
                    findings.append({"path": path, "line": None, "rule": "memory-file-too-large"})
                else:
                    findings.extend(inspect(path, repo.blob(entry["blob"])))
    else:
        listing = repo.git("diff", "--cached", "--raw", "--no-abbrev", "--diff-filter=ACMT", "--no-renames", "-z", "--")
        fields = listing.split(b"\0")[:-1]
        for index in range(0, len(fields), 2):
            path = fields[index + 1].decode("utf-8", "surrogateescape")
            if not memory_path(path):
                continue
            _, mode, _, oid, _ = fields[index].decode("ascii").split()
            checked += 1
            if mode != "100644":
                findings.append({"path": path, "line": None, "rule": "memory-must-be-a-regular-file"})
            elif int(repo.git("cat-file", "-s", oid)) > MAX_MEMORY_BYTES:
                findings.append({"path": path, "line": None, "rule": "memory-file-too-large"})
            else:
                findings.extend(inspect(path, repo.blob(oid)))
    return {"checked": checked, "findings": findings}
