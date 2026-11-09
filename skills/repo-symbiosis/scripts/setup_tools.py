"""Create safe repository scaffolding and inspect local prerequisites."""

import importlib.metadata
import re
import sys

from . import __version__, delivery, github_gate
from .repository import SymbiosisError, json_bytes, read_json, require


WORKFLOW = ".github/workflows/symbiosis.yml"
CHECKOUT = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON = "5fda3b95a4ea91299a34e894583c3862153e4b97"
WHEEL = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/download/"
    r"[A-Za-z0-9_.-]+/repo_symbiosis-[A-Za-z0-9_.-]+\.whl#sha256=[0-9a-f]{64}\Z"
)


def workflow(base, wheel):
    require(isinstance(wheel, str) and WHEEL.fullmatch(wheel),
            "Checker must be a GitHub release wheel URL with its #sha256 hash")
    return f"""name: Symbiosis
'on':
  pull_request:
    branches:
      - '{base}'
  merge_group:
    types: [checks_requested]
permissions:
  contents: read
jobs:
  history-and-scope:
    name: Symbiosis / history and scope
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@{CHECKOUT}
        with:
          fetch-depth: 0
      - uses: actions/setup-python@{SETUP_PYTHON}
        with:
          python-version: '3.12'
      - name: Install pinned checker
        run: python -m pip --isolated install --no-index --no-deps '{wheel}'
      - name: Verify GitHub candidate
        run: symbiosis gate
""".encode()


def target(repo, path):
    destination = repo.root / path
    require(destination.resolve() == destination and not destination.is_symlink(), f"Unsafe setup path: {path}")
    return destination


def prepare(repo, files):
    destinations = {path: target(repo, path) for path in files}
    for path, destination in destinations.items():
        if destination.exists():
            require(destination.is_file() and destination.read_bytes().replace(b"\r\n", b"\n") == files[path],
                    f"Existing file differs: {path}. Review and update it explicitly.")
    created = []
    for path, destination in destinations.items():
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(files[path])
            created.append(path)
    return {"created": created, "existing": [path for path in files if path not in created]}


def initialize(repo, base, mode, wheel):
    settings = github_gate.policy({"schema": 1, "base": base, "mode": mode})
    repo.git("check-ref-format", f"refs/heads/{base}")
    files = {github_gate.POLICY: json_bytes(settings)}
    if wheel:
        files[WORKFLOW] = workflow(base, wheel)
    return {**prepare(repo, files), "policy": settings}


def write_manifest(repo, remote, task, run, claim_id):
    claim = delivery.Ledger(repo, remote).task_claim(task, claim_id)
    branch = repo.git("symbolic-ref", "--short", "HEAD").decode().strip()
    require(branch == claim["branch"], f"Use the reserved branch {claim['branch']}")
    path = f"{github_gate.CHANGES}{task}.json"
    require(delivery.includes(claim, path), f"Reserve the manifest path before writing it: {path}")
    value = github_gate.manifest(path, {"task": task, "run": run, "claim": claim_id})
    return {**prepare(repo, {path: json_bytes(value)}), "manifest": path}


def mcp_supported():
    try:
        version = importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        return False
    match = re.match(r"(\d+)\.(\d+)", version)
    return bool(match) and (1, 28) <= tuple(map(int, match.groups())) < (3, 0)


def doctor(repo, remote):
    git = repo.git("--version").decode().strip()
    version = re.search(r"\b(\d+)\.(\d+)", git)
    shallow = repo.git("rev-parse", "--is-shallow-repository").strip() == b"true"
    partial = bool(repo.run("config", "--get", "extensions.partialClone").stdout.strip())
    problems = []
    if not version or tuple(map(int, version.groups())) < (2, 47):
        problems.append("Use Git 2.47 or later")
    if shallow or partial:
        problems.append("Use a full clone for history checks")
    policy_path = target(repo, github_gate.POLICY)
    settings = github_gate.policy(read_json(policy_path.read_bytes(), github_gate.POLICY)) if policy_path.is_file() else None
    if settings is None:
        problems.append("Initialize the repository policy")
    try:
        delivery.Ledger(repo, remote)
    except SymbiosisError:
        problems.append("Configure the coordination remote with one matching fetch and push URL")
    identity = repo.run("var", "GIT_AUTHOR_IDENT").returncode == 0
    if not identity:
        problems.append("Configure Git author identity before committing")
    return {
        "version": __version__, "python": ".".join(map(str, sys.version_info[:3])), "git": git,
        "repository": str(repo.root), "policy": settings, "workflow_present": target(repo, WORKFLOW).is_file(),
        "mcp_available": mcp_supported(),
        "git_identity_configured": identity,
        "problems": problems,
    }
