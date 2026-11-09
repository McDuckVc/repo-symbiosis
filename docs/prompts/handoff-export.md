# Prompt 1: Export a handoff from an experienced agent

Give this prompt to an agent that has already been working on the project (Claude Code, Codex, Cursor, or any other harness) and holds context that is not in the code. It writes everything it knows into the repository in a standard format and changes no application code. Run it once per agent or account that has useful context; each run produces its own bundle.

Copy everything below the line.

---

# Repo Symbiosis handoff export

You have been working on this project and hold knowledge that is not in the code: decisions and their reasons, failed attempts, plans, environment details, conventions, and unfinished work. A new agent with no memory of any of this will take over. Your job now is to write all of it into the repository in the standard format below, publish it on its own branch, and stop.

## Rules

1. **Do not change application code.** Write only the bundle described below and, if needed, the WIP branch in Step 2.
2. **No secrets.** Never write tokens, API keys, passwords, private keys, credentials inside URLs, session cookies, customer data, or personal data. Name a secret and where it lives instead, for example "`STRIPE_SECRET_KEY`, set in the production environment of the hosting provider". If you are unsure whether something is sensitive, leave it out and list it under `omitted` in the manifest.
3. **Separate what you checked from what you remember.** Before writing any fact about the code, open the file and confirm it at the commit you record. Label every fact:
   - `verified`: you checked it in the code during this export.
   - `recalled`: it comes from the conversation or your memory and you did not re-check it.
   - `inferred`: it is your own reasoning or guess.
4. **Give evidence.** For code facts, give repository-relative paths and inclusive line ranges at the recorded commit.
5. **Completeness beats polish.** Use short bullet points. Include what went wrong, and say so when you are unsure.
6. **Read all your sources first.** These include this conversation and any earlier sessions you can access, your memory or notes, `CLAUDE.md`, `AGENTS.md`, `.cursor/rules`, `.github/copilot-instructions.md`, TODO or notes files, open branches and pull requests, and recent `git log`. Carry over everything that still applies.

Choose a source ID: `<harness>-<account>-<yyyymmdd>`, using only lowercase letters, digits, and hyphens, for example `claude-code-alice-20261109`. In the rest of this prompt, `<source-id>` means that value.

## Step 1: Record the repository state

Run and note:

```text
git remote -v                      (remove any credentials from URLs before recording them)
git symbolic-ref refs/remotes/origin/HEAD
git rev-parse HEAD
git status --short
git stash list
git branch -a
git log --oneline -30
git log --oneline @{upstream}..HEAD   (unpushed commits, if the branch has an upstream)
```

Also list open pull requests if you can see them.

## Step 2: Protect unfinished work

If there are uncommitted changes, stashes, or unpushed commits, do not discard them:

1. Create the branch `handoff-wip/<source-id>` from your current state. Never use the default branch.
2. Commit the unfinished work there, with a message that says what state it is in and whether it builds.
3. Push the branch.

If you cannot push, say so in your final report. If there is no unfinished work, write "none" in `05-work-state.md`.

## Step 3: Write the bundle

Fetch, then create the branch `handoff/<source-id>` from the tip of the default branch. Record that tip's full SHA as the bundle commit. Check every code fact in `facts.json` against that commit. Describe work that exists only on other branches in `05-work-state.md`, not in `facts.json`.

Create `docs/symbiosis/handoff/<source-id>/` containing:

### `manifest.json`

```json
{
  "format": "repo-symbiosis-handoff/1",
  "source_id": "claude-code-alice-20261109",
  "harness": "claude-code",
  "model": "the model you are, if known",
  "account": "GitHub login or account label of whoever ran you (no email addresses)",
  "written_at": "2026-11-09T12:00:00Z",
  "repository": "owner/name",
  "default_branch": "main",
  "commit": "full SHA of the default branch tip the facts were checked against",
  "wip_branch": "handoff-wip/claude-code-alice-20261109 or null",
  "period": {"from": "approximate start of your work", "to": "approximate end"},
  "files": ["README.md", "01-overview.md", "02-architecture.md", "03-decisions.md", "04-runbook.md",
            "05-work-state.md", "06-history.md", "07-open-questions.md", "facts.json", "backlog.json"],
  "omitted": ["what you deliberately left out, and why"]
}
```

### Markdown files

| File | Content |
| --- | --- |
| `README.md` | One page: what the project is, who uses it, whether and where it is live, what you were doing last, the three most important things the next agent must know, and an index of the other files. |
| `01-overview.md` | Purpose, users, and features, with the state of each feature (working, partial, broken). Environments (local, staging, production) and how to reach them, by name only. External services and what they are used for. |
| `02-architecture.md` | Components and their responsibilities with paths, entry points, request and data flow, data stores and schemas, integrations, and a Mermaid diagram if it helps. |
| `03-decisions.md` | One entry per decision: approximate date, the decision, why it was made, alternatives rejected and why, status (active, reversed, superseded), evidence paths or links, and its label (verified, recalled, inferred). |
| `04-runbook.md` | Exact commands to install, build, test, lint, run locally, migrate, deploy, and roll back. Required environment variable **names**. Known gotchas and flaky tests. Conventions for style, naming, branches, commits, and reviews. Standing instructions from your memory or instruction files that still apply. |
| `05-work-state.md` | Work in progress: what it is, where it lives (branch and commit), how far it got, and the next step. Where the WIP from Step 2 was saved. Known bugs: symptom, suspected cause, location. Technical debt. The next steps you would take, in priority order. |
| `06-history.md` | A chronological summary of the work: what was attempted, what worked, and what failed and why. Say clearly what **not** to try again. |
| `07-open-questions.md` | Unknowns, unverified assumptions, decisions the owner still has to make, and contradictions you noticed between docs, code, and memory. |

### `facts.json`

Machine-readable facts for the next agent to verify and load into project memory:

```json
{
  "format": "repo-symbiosis-handoff-facts/1",
  "commit": "same SHA as manifest.json",
  "facts": [
    {
      "id": "decision-auth-uses-jwt",
      "kind": "decision",
      "summary": "Sessions use short-lived JWTs refreshed by /auth/refresh because the API runs on several stateless instances.",
      "confidence": "verified",
      "evidence": [{"path": "src/auth/session.ts", "start": 12, "end": 58}],
      "links": [],
      "components": ["component:auth"]
    }
  ]
}
```

- One fact per entry. Each `kind` is one of `analysis` (how the code works), `decision`, or `failure` (something that broke or an approach that failed). Put remaining work in `backlog.json`, not here.
- IDs are unique, up to 80 characters, and use lowercase letters, digits, and hyphens.
- Evidence paths must exist at the bundle commit. Line ranges are inclusive and must fall inside the file. Leave out `start` and `end` for whole-file evidence.
- `links` must be `https://` URLs with no credentials and no query string.
- `components` uses prefixed IDs such as `component:auth`, `service:api`, or `store:postgres`.

### `backlog.json`

Remaining work, in the Repo Symbiosis backlog format:

```json
{
  "completed": [],
  "tasks": [
    {
      "id": "fix-refresh-race",
      "title": "Stop parallel refresh calls from invalidating each other",
      "paths": ["src/auth/session.ts", "src/auth/refresh/"],
      "resources": ["auth-api"],
      "depends_on": [],
      "acceptance": ["Two concurrent refreshes both succeed in the integration test"],
      "priority": 2
    }
  ]
}
```

- Use the same ID format as facts. Include only work that is not done.
- `paths` are the literal files, or directories ending in `/`, that the task will write. No wildcards.
- `resources` name shared things two tasks must not change at once, such as a database schema or an API contract.
- Give every task at least one observable acceptance check. A higher `priority` goes first.

## Step 4: Check and publish

1. Confirm every JSON file parses, for example with `python -m json.tool <file>`.
2. Confirm every evidence path exists at the bundle commit: `git cat-file -e <commit>:<path>`.
3. Search the bundle for anything that looks like a credential: long random strings, `key`, `token`, `secret`, `password`, and URLs containing `@`. If Repo Symbiosis is installed, stage the bundle and run `symbiosis scan-memory`.
4. Commit only the bundle on `handoff/<source-id>`, with the message `Handoff export from <source-id>`, and push the branch.
5. Do not open a pull request into the default branch, and do not merge anything.

## Step 5: Report

Finish with:

- the handoff branch and commit, and the WIP branch (or "none")
- the files written, with the number of facts (by confidence) and backlog tasks
- what you could not verify, and what you omitted
- anything urgent the next agent or the owner must know right away, such as broken production or a deadline
