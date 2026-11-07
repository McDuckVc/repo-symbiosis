# Workers, data, and cloud

Resolve available tools at runtime. Start with local Git and existing repository knowledge. Use live systems when they answer a named question about an authorized target. Prefer an existing scoped read connector, then an authenticated provider CLI. Record the evidence source, observation date, scope and result in project memory. A repository declaration and a live observation are separate facts.

| Need | Route | Record |
| --- | --- | --- |
| Parallel investigation | Codex agents or Antigravity read-only team | Revision, shard, evidence, coverage, open questions |
| Isolated implementation | Codex task worktree or Antigravity isolated edit | Claim ID, base, patch review, checks, PR |
| GitHub state | GitHub tools or GitHub CLI | Repo, base/head SHA, PR and check/deployment links |
| Data contracts | Migrations and ORM, then scoped data MCP or approved read-only client | Schema, producer/consumer, migration order, observed drift |
| Cloud topology | IaC and workflows, then scoped cloud MCP or provider CLI | Service type, environment, artifact/version, provider evidence |
| Existing Conductor project | Project Intelligence tools such as `project_overview`, `trace_feature`, and `get_runbook` | Verified source evidence relevant to the current repository |

## Worker routing

For two or more independent, approved external read-only investigations, use Antigravity `start_team` with absolute `cwd`, `objective`, bounded `agents`, and concrete acceptance criteria. Use `start_analysis` for a single worker. Poll `get_team` or `get_run` while doing coordinator work. Use native Codex workers when authorized and suitable. If a worker system is unavailable, cover the remaining shards locally and report the actual mode used.

Use an isolated, sanitized checkout or an approved source subset for external workers. A path assignment alone gives no access isolation. When a sanitized subset omits history, provide the pinned upstream revision in the task and independently check all evidence against it. Keep claims, credentials, raw data, and external mutations with the coordinator. Antigravity edits use `start_edit` on the reserved Git task checkout and require patch inspection before `apply_run`. Check the actual changed paths and rerun the relevant tests. Conductor's operational memory stays in the Conductor system. This repository's graph stays in Git.

## Live evidence examples

Substitute approved, explicit identifiers. Use the existing authenticated session with a narrowly scoped read role.

```text
aws cloudformation describe-stacks --stack-name STACK --region REGION --profile READ_PROFILE --query "Stacks[0].StackStatus" --output text
gcloud run services describe SERVICE --project PROJECT --region REGION --format="value(status.latestReadyRevisionName)"
az webapp show --subscription SUBSCRIPTION --resource-group GROUP --name APP --query state --output tsv
```

[AWS CLI](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/describe-stacks.html), [Google Cloud CLI](https://docs.cloud.google.com/sdk/gcloud/reference/run/services/describe), [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/webapp#az-webapp-show).

For data systems, request schema metadata only, preferably from a read replica or staging. For example, use an approved PostgreSQL connection under a restricted role and a read-only transaction. Filter `information_schema.columns` to the approved schema and tables and request only table name, column name, data type, and nullability. Redact sensitive identifiers where appropriate. Record a summarized observation with provenance and any permission gaps. [PostgreSQL columns](https://www.postgresql.org/docs/current/infoschema-columns.html), [read-only transactions](https://www.postgresql.org/docs/current/sql-set-transaction.html).
