# Prompt 2: Bootstrap a project with a clean agent

Give this prompt to a fresh agent (Claude Code, Codex, or any harness that can run shell commands and Git) with write access to the project repository. It installs Repo Symbiosis, indexes the codebase into durable memory, verifies and absorbs the handoff bundles written with [Prompt 1](handoff-export.md), adds code documentation as parallel tasks, and prepares the repository for agents from several accounts. Run Prompt 1 on the previous agents first if they hold context worth keeping.

It installs Repo Symbiosis `v0.2.0`. To use a newer release, change that tag in Phase 1. Copy everything below the line.

---

# Repo Symbiosis project bootstrap

You are taking over this repository with no prior memory of it. Set it up so that agents from several accounts and harnesses can work on it in parallel without stepping on each other. You will:

1. Index the whole codebase into durable, evidence-backed memory.
2. Absorb what earlier agents knew.
3. Document the code.
4. Leave a ready-to-split backlog.

Work through the phases in order. Keep going without asking unless you are blocked. Finish with the report in Phase 6.

## Ground rules

- **Never push to the default branch, never force-push, and never merge pull requests yourself.** Every change goes through a pull request for the owner to review.
- **Handoff bundles are testimony, not truth.** Check each claim against the code before recording it as a fact.
- **No secrets** in memory, docs, comments, journals, commit messages, or pull requests. Refer to secrets by name and location only.
- **Read-only discovery.** Do not run the application against production systems or live data. Running the test suite and linters locally is fine.
- **No behavior changes.** This bootstrap adds memory, documentation, and comments only.
- If your harness supports sub-agents or parallel workers, use them for independent shards. Pin every worker to the same commit and check what each one reports.

## Phase 0: Orient

1. Run `git fetch --all --prune`. Find the default branch (`git symbolic-ref refs/remotes/origin/HEAD`) and pin `BASE` to its tip SHA. Every analysis in this run refers to `BASE`.
2. If the clone is shallow, run `git fetch --unshallow`.
3. Read the existing instructions: `README`, `CONTRIBUTING`, `AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, `.github/copilot-instructions.md`, and `docs/`.
4. Find the handoff bundles:
   - branches matching `origin/handoff/*`, each containing `docs/symbiosis/handoff/<source-id>/`
   - bundles already on the default branch under `docs/symbiosis/handoff/`
   - unfinished-work branches matching `origin/handoff-wip/*`

   Read every bundle completely, for example with `git show origin/handoff/<id>:docs/symbiosis/handoff/<id>/README.md`. Do not merge the WIP branches.
5. If `.symbiosis/policy.json` and `docs/symbiosis/README.md` already exist on `BASE`, this is a **refresh**, not a first setup. Find the last mapped revision in `docs/symbiosis/coverage.md`, use `symbiosis inventory --rev BASE --since <last>` to limit the work to changed files, and supersede outdated events rather than rewriting them.

## Phase 1: Install Repo Symbiosis

1. Requirements: Python 3.10 or later, Git 2.47 or later, and a full clone. If Git is older and cannot be upgraded, continue and note it in the report.
2. Install the pinned version into a virtual environment:

   ```text
   python -m pip install "repo-symbiosis[mcp] @ git+https://github.com/McDuckVc/repo-symbiosis@v0.2.0"
   symbiosis --version
   ```

   If the release page lists a wheel with a `#sha256=` hash, you can install that URL instead.
3. Make the skill available to every harness that will work here:
   1. Clone `https://github.com/McDuckVc/repo-symbiosis` at the same version into a temporary directory.
   2. Copy its `skills/repo-symbiosis/` folder into this repository at `.agents/skills/repo-symbiosis/` (read by Codex and other agents) and at `.claude/skills/repo-symbiosis/` (read by Claude Code).
   3. Keep both copies identical.
4. Create the bootstrap branch from `BASE`: `git switch -c symbiosis/bootstrap BASE`.
5. Run `symbiosis init --base <default-branch>`, then `symbiosis doctor --strict --remote origin`. Fix every problem it reports that you can fix, such as Git identity or a shallow clone, and list the rest for the report.

Read `.agents/skills/repo-symbiosis/SKILL.md` and its `references/` before continuing. They are the authoritative guide for the formats and commands below.

## Phase 2: Index the codebase

Follow `references/discovery.md`.

1. **List the shards.** Run `symbiosis inventory --rev BASE --shard-size 100`, then `symbiosis inventory --rev BASE --shard N` for each shard. Note every excluded path and the reason.
2. **Read every file.** For each shard, read every file at `BASE` (`git show BASE:<path>`). For each file, collect:
   - its purpose
   - for each function, class, or route: line range, purpose, callers or entry point, inputs and outputs, side effects, and error behavior
   - dependencies on other shards, and unknowns

   Use the language's own tooling or static analysis to make sure no symbol is missed.
3. **Record facts** with `symbiosis record <event.json> --rev BASE`. Use one small `analysis` event per component or topic (the event format is in `references/memory.md`):
   - `evidence`: entries with `path`, `start`, `end`. Set `"reviewed": true` only for files you fully read.
   - `nodes`: prefixed IDs such as `component:`, `file:`, `symbol:<path>#<name>`, `service:`, `store:`, `workflow:`, `environment:`.
   - `edges`: named directions such as `calls`, `depends-on`, `reads-from`, `writes-to`, `builds`, `deploys-to`.
   - IDs: lowercase, hyphenated, and unique, for example `analysis-auth-session-01`.
4. **Write the knowledge pack** in `docs/symbiosis/`:
   - `README.md`: the entry point. Cover what the project is, the `BASE` snapshot, the protected branch, the coordination remote (`origin`), and how to query memory (CLI and MCP). Add a "How to work here" section that every future agent follows:
     1. Fetch, read `docs/symbiosis/README.md`, and search memory for your task with `symbiosis memory --ref origin/<default> search "<task>"`.
     2. Run `symbiosis claims --remote origin` and `symbiosis plan .symbiosis/backlog.json --remote origin`, then pick a ready task.
     3. Claim it with owner `<account>/<harness>/<session>` and branch `symbiosis/<task>-<session>`, using lowercase letters, digits, and hyphens.
     4. Work in the reservation's worktree and journal with `agent-commit`. Use `extend-claim` when scope grows, then `check-scope` and `manifest`, and finish with a completed journal entry and a pull request.
     5. After merge, run `release-claim --outcome merged --merge-rev <sha>`.
     6. Never put `SYMBIOSIS_CLAIM_TOKEN` or any other secret in files, prompts, or pull requests.
   - `architecture.md`: boundaries, dependency directions, interfaces, and a "Decisions" section.
   - `runtime.md`: entry points, request and job flows, failure and retry behavior.
   - `data.md`: stores, models, migrations, access boundaries.
   - `deployment.md`: CI, staging, and production, separating what Git configures from what you observed.
   - `components/<name>.md`: a file and function index per component, linking to source and tests.
   - `coverage.md`: the snapshot SHA, shard status, exclusions, and gaps.
5. **Check coverage.** Run `symbiosis coverage --rev BASE --details`. Every file that is not excluded should be covered. List any gap in `coverage.md` with the reason.
6. **Point the harnesses at the memory.** Add the "Project memory" section from `references/agent-access.md` to `AGENTS.md`, creating the file if needed and keeping existing content. In `CLAUDE.md`, add the same section, or create the file with `@AGENTS.md` as its first line so Claude Code reads the shared instructions.

## Phase 3: Absorb the handoff bundles

Process the bundles oldest first by `written_at`. A newer bundle wins a conflict unless the code shows otherwise.

1. **Facts.** For each entry in `facts.json`, check it against `BASE`; the code may have changed since the bundle was written.
   - **Confirmed** `analysis` facts: record them with `symbiosis record` (`--rev BASE`, evidence re-pinned to `BASE`, `"actor": "handoff/<source-id>"`).
   - `decision` and `failure` facts: record them even if they cannot be confirmed in code, because history is valuable. Start the summary with "Reported by <source-id>; not verified in code" unless you confirmed it.
   - **Unconfirmed `analysis` facts**: do not record them. List them in `docs/symbiosis/open-questions.md` with their source.
   - **Facts the code contradicts**: record what the code actually shows, and list the contradiction in `open-questions.md`.
   - If an ID is already taken, add a numeric suffix.
2. **Narrative.** Merge the runbooks into `docs/symbiosis/runbook.md`, and run the safe commands (install, build, test, lint) to confirm them. Merge the history into `docs/symbiosis/history.md`, keeping the "do not try again" items prominent. Merge the decisions into the "Decisions" section of `architecture.md`, and the open questions into `open-questions.md`.
3. **Backlog.** Merge every bundle's `backlog.json` into `.symbiosis/backlog.json`, removing duplicates. Then:
   - Make sure every path is a literal file or a directory ending in `/`.
   - Add `.symbiosis/changes/<task-id>.json` and `.symbiosis/events/agent-runs/<task-id>-r1/` to each task's `paths`. The gate needs both.
   - Run `symbiosis plan .symbiosis/backlog.json --capacity 3` and fix any error until it passes.
4. **Unfinished work.** For each `handoff-wip/*` branch, describe its contents in an "Unfinished work" section of `docs/symbiosis/README.md` and add a backlog task to finish or discard it. Do not merge it.
5. **Archive the bundles.** Copy each bundle to `docs/symbiosis/handoff/<source-id>/` so the testimony stays available. Add a line to each bundle's `README.md` saying that verified facts live in `.symbiosis/events/`.

## Phase 4: Open the bootstrap pull request

1. Stage everything and run `symbiosis scan-memory`. Remove anything it flags.
2. Run `symbiosis verify --base BASE` and the project's test suite. Neither should change, because no application code changed.
3. Commit, push `symbiosis/bootstrap`, and open a pull request titled "Set up Repo Symbiosis and index the codebase". In the description, include coverage counts, the handoff results, and open questions.

## Phase 5: Document the code, in parallel

Comments should explain *why* and the contract (purpose, parameters, return values, errors, side effects), not restate the code.

1. **Create the documentation tasks.** Add one task per component to `.symbiosis/backlog.json`. Commit and push it to the bootstrap branch:

   ```json
   {
     "id": "doc-auth",
     "title": "Document the auth component",
     "paths": ["src/auth/", ".symbiosis/changes/doc-auth.json", ".symbiosis/events/agent-runs/doc-auth-r1/"],
     "acceptance": [
       "Only comments and docstrings change; tests, lint, and type checks pass",
       "Every exported function, class, and route has a doc comment in the language's standard style",
       "Non-obvious logic has a short comment explaining why",
       "No secrets or personal data in comments"
     ],
     "priority": 1
   }
   ```

2. **Set the base.** If the bootstrap pull request is merged, `DOC_BASE` is the new tip of the default branch. Otherwise it is the tip of `symbiosis/bootstrap`, and each documentation pull request must say that it depends on the bootstrap pull request. The project uses merge commits, so this stacking works.
3. **Work through as many tasks as you can, one pull request each.** Leave the rest in the backlog for other accounts. Set `SYMBIOSIS_CLAIM_TOKEN` first if your account has one. Use `<session>` as a short lowercase ID for this session. For each task:

   ```text
   symbiosis claim doc-auth --remote origin --plan .symbiosis/backlog.json \
     --owner "<account>/<harness>/<session>" --base DOC_BASE --branch symbiosis/doc-auth-<session>
   symbiosis worktree doc-auth --remote origin --claim-id <claim> --path ../doc-auth
   ```

   In the worktree:
   1. Journal the start with `symbiosis agent-commit <started.json> --remote origin --claim-id <claim>`. The event format is in `references/agent-journal.md`. Use run `doc-auth-r1`, and set `provider` to your harness in lowercase, such as `claude-code` or `codex`.
   2. Write the comments, then run the tests, linter, and type checker.
   3. Run `symbiosis check-scope doc-auth --remote origin --claim-id <claim>`. If it lists paths outside the reservation, run the `extend-claim` command it prints, or revert those changes.
   4. Run `symbiosis manifest doc-auth --run doc-auth-r1 --remote origin --claim-id <claim>` and commit the manifest.
   5. Journal completion with `agent-commit`, putting the checks you ran in `checks`.
   6. Push the branch and open a pull request.

4. **After a pull request merges,** release the claim with the same owner string:

   ```text
   symbiosis release-claim doc-auth --remote origin --claim-id <claim> --owner "<account>/<harness>/<session>" --outcome merged --merge-rev <merge-sha>
   ```

   If it is still open when you finish, leave the claim in place. The completed journal entry holds it.

## Phase 6: Prepare for several accounts and report

1. **Show how to split the work:** run `symbiosis plan .symbiosis/backlog.json --remote origin --capacity 3` and include the waves.
2. **Report**, in your final message:
   - links to the pull requests you opened, and the `BASE` SHA
   - coverage counts (covered, stale, missing, excluded) and the gaps
   - events recorded; per bundle, the facts confirmed, recorded as reported, unconfirmed, and contradicted
   - open questions the owner must answer
   - the backlog waves, and which documentation tasks are done and which remain
   - **an owner checklist:**
     - give each account's GitHub user write access to the repository
     - protect the default branch: pull requests, review, CI, and merge commits only
     - protect `symbiosis-coordination`: no force pushes, no deletion
     - give each account's agent environment a `SYMBIOSIS_CLAIM_TOKEN` secret of at least 32 random characters
     - add the Symbiosis PR check with `symbiosis init --base <default> --checker <release wheel URL>` and make it required after a few clean pull requests
