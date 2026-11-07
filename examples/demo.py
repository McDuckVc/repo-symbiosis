"""Build an inspectable local demo of two concurrent agent PRs and a fresh-clone handoff."""

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path


def command(args, payload=None, failure=None):
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True)
    if failure:
        if result.returncode != 1 or failure not in result.stderr:
            raise RuntimeError(result.stderr or "Expected the gate to reject this candidate")
    else:
        result.check_returncode()
    return result.stdout.strip()


def git(repo, *args):
    return command(["git", "-C", str(repo), *args])


def symbiosis(repo, *args, payload=None, failure=None):
    result = command([sys.executable, "-B", "-m", "repo_symbiosis.symbiosis", "--repo", str(repo), *args],
                     payload, failure)
    return json.loads(result) if result else None


def author(repo):
    git(repo, "config", "user.name", "Symbiosis Demo")
    git(repo, "config", "user.email", "demo@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")


def run_agent(root, bare, base, plan, task, provider):
    clone = root / f"clone-{task}"
    git(root, "clone", "--template=", bare.as_uri(), str(clone))
    author(clone)
    branch, run = f"symbiosis/{task}", f"{task}-agent"
    reserved = symbiosis(clone, "claim", task, "--remote", "origin", "--owner", task,
                         "--base", base, "--branch", branch, "--plan", str(plan))
    lease = ("--remote", "origin", "--claim-id", reserved["claim"])
    work = root / f"work-{task}"
    symbiosis(clone, "worktree", task, *lease, "--path", str(work))
    initial = {"id": f"agent-{run}-0001", "kind": "agent", "summary": f"Started {task}",
               "task": task, "actor": f"demo/{task}",
               "agent": {"run": run, "provider": provider, "phase": "started", "rationale": "Use the reserved scope."}}
    symbiosis(work, "agent-commit", "-", *lease, payload=initial)
    manifest = symbiosis(work, "manifest", task, "--run", run, *lease)["manifest"]
    source = f"{task}.py"
    (work / source).write_text(f"ENABLED = True  # {task}\n", encoding="utf-8")
    git(work, "add", "--", source, manifest)
    git(work, "commit", "-m", f"Enable {task}")
    subprocess.run([sys.executable, "-B", "-c",
                    f"import runpy; assert runpy.run_path('{source}')['ENABLED'] is True"], cwd=work, check=True)
    final = {**initial, "id": f"agent-{run}-0002", "summary": f"Verified {task}", "evidence": [{"path": source}],
             "agent": {**initial["agent"], "phase": "completed", "previous": initial["id"], "checks": ["Smoke test passed."]}}
    head = symbiosis(work, "agent-commit", "-", *lease, payload=final)["commit"]
    git(work, "push", "origin", f"HEAD:refs/heads/{branch}")
    return task, run, branch, head, work


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, help="New destination for local demo repositories")
    args = parser.parse_args()
    root = Path(args.directory).resolve()
    root.mkdir(parents=True, exist_ok=False)
    project, bare = root / "project", root / "team.git"
    git(root, "init", "--template=", "-b", "main", str(project))
    git(root, "init", "--template=", "--bare", "-b", "main", str(bare))
    author(project)
    symbiosis(project, "init", "--base", "main")
    git(project, "add", ".")
    git(project, "commit", "-m", "Initialize the demo policy")
    git(project, "remote", "add", "origin", bare.as_uri())
    git(project, "push", "origin", "main")
    base = git(project, "rev-parse", "HEAD")
    plan = root / "backlog.json"
    tasks = [("checkout", "codex"), ("reporting", "antigravity")]
    plan.write_text(json.dumps({"tasks": [
        {"id": task, "title": f"Enable {task}", "paths": [
            f"{task}.py", f".symbiosis/events/agent-runs/{task}-agent/", f".symbiosis/changes/{task}.json",
        ]} for task, _ in tasks
    ]}), encoding="utf-8")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_agent, root, bare, base, plan, task, provider) for task, provider in tasks]
        ready = [future.result() for future in futures]
    git(project, "fetch", "origin")
    _, _, branch, head, work = ready[0]
    (work / "outside.txt").write_text("Outside the reservation\n", encoding="utf-8")
    git(work, "add", "outside.txt")
    git(work, "commit", "-m", "Simulate an out-of-scope PR edit")
    rogue = git(work, "rev-parse", "HEAD")
    event = {"pull_request": {"base": {"sha": base, "ref": "main", "repo": {"id": 1}},
                              "head": {"sha": rogue, "ref": branch, "repo": {"id": 1}}, "user": {"id": 2}}}
    symbiosis(work, "gate", "--event-name", "pull_request", "--event", "-", "--candidate", rogue,
              payload=event, failure="Paths outside the reservation")
    for _, _, _, commit, _ in ready:
        git(project, "merge", "--no-ff", "--no-edit", commit)
    candidate = git(project, "rev-parse", "HEAD")
    group = {"merge_group": {"base_ref": "refs/heads/main", "base_sha": base, "head_sha": candidate}}
    report = symbiosis(project, "gate", "--event-name", "merge_group", "--event", "-", payload=group)
    git(project, "push", "origin", "main")
    reviewer = root / "fresh-reviewer"
    git(root, "clone", "--template=", bare.as_uri(), str(reviewer))
    journals = [symbiosis(reviewer, "memory", "--ref", "origin/main", "run", run) for _, run, _, _, _ in ready]
    print(json.dumps({"repositories": str(root), "out_of_scope_rejected": True, "merge_gate": report,
                      "fresh_clone_journals": journals}, indent=2))


if __name__ == "__main__":
    main()
