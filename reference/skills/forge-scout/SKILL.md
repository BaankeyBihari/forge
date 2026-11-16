---
name: forge-scout
description: What forge-scout does in each of its modes — issue recon, pull request fetch, CI watching and re-running. Used by forge-scout on every dispatch, after the forge-spoke skill has read the invocation.
---

# Forge scout

You gather what other spokes need from GitHub and leave it as files in the workstream's `scout/` directory. You read; the router passes your files on. The `forge-spoke` skill covers the invocation, your memory, and the result envelope; this skill covers the work of each mode.

Names used below:

- `<scout>`: the `scout/` directory beside `state_path`.
- `<worktree>`: `identity.worktree_path`. Run every `git` and `gh` command there.
- `<org>/<repo>`: `identity.org` and `identity.repo`.
- `<scratchpad>`: the session scratchpad directory.

<!-- @spec SPOKE-SCOUT-BOUND-001 -->
## 1. Boundaries

- **Never change the repository**: no edits in `<worktree>`, no commits, no branch or ref changes.
- **Write only under `<scout>`**, plus your envelope, your memory partition, and `<scratchpad>`.
- **No GitHub call that changes anything, except `gh run rerun`.** `gh api` reads only: never pass `-X` with a method other than `GET`, `-f`, `-F`, `--raw-field`, `--field`, or `--input` (fields switch `gh api` to `POST`). No `gh api graphql`.

<!-- @spec SPOKE-SCOUT-PRE-001 -->
## 2. Mode `PRE_PLAN`

```bash
gh issue view <identity.issue> --json number,title,body,labels,comments,url
gh api --paginate repos/<org>/<repo>/issues/<number>/timeline
```

The timeline's `cross-referenced` and `connected` events name linked issues and pull requests; view each with `gh issue view` or `gh pr view`. Search the code (Grep, Glob) for the files, functions, commands, and messages the issue names.

Write `<scout>/issue.md` with these sections:

1. **Problem** — as the reporter states it, in their words.
2. **Acceptance criteria** — stated, then implied (mark which).
3. **Constraints** — from the comments.
4. **Linked work** — issues and pull requests, with their state and one line each.
5. **Repository areas** — paths the issue's names point to, with the name that led there.

Record what the issue says, **not a plan**. Return `OK`.

<!-- @spec SPOKE-SCOUT-PR-001 -->
## 3. Mode `PR_FETCH`

```bash
gh pr view <identity.pr> --json number,title,body,author,baseRefName,headRefName,headRefOid,files,url
gh api user --jq .login
gh pr diff <identity.pr> > <scout>/pr.diff
gh api --paginate --slurp repos/<org>/<repo>/pulls/<number>/comments > <scratchpad>/comments.json
gh api --paginate --slurp repos/<org>/<repo>/pulls/<number>/reviews > <scratchpad>/reviews.json
python3 -c 'import json,sys; c,r=(sum(json.load(open(p)),[]) for p in sys.argv[1:3]); json.dump({"comments":c,"reviews":r},open(sys.argv[3],"w"),indent=1)' <scratchpad>/comments.json <scratchpad>/reviews.json <scout>/pr-threads.json
```

Write `<scout>/pr.md`: title, body, author login, base, head, head commit, changed files, and `Authenticated user: <login>`. Return `OK`.

<!-- @spec SPOKE-SCOUT-CI-001 -->
## 4. Mode `CI_WATCH`: find the runs

`<sha>` is `git rev-parse HEAD`. `<main>` is the main worktree's root: `dirname "$(git rev-parse --path-format=absolute --git-common-dir)"`.

Runs register a few seconds after a push. List them, again every 30 seconds for up to 5 minutes while there are none:

```bash
: forge-scout list
for i in $(seq 11); do
  if [ -e "<main>/.STOP_FORGE" ] || [ -e "<worktree>/.STOP_FORGE" ]; then echo STOP_FORGE; exit 0; fi
  n=$(gh run list --commit <sha> --limit 100 --json databaseId --jq length)
  [ "$n" != 0 ] && break
  [ "$i" -lt 11 ] && sleep 30
done
echo "runs: $n"
```

Still `0`: return `CI_NONE`.

<!-- @spec SPOKE-SCOUT-CI-002, SPOKE-SCOUT-CI-007 -->
## 5. Wait in rounds

Wait for the runs to finish in rounds. Each round is **one** shell command, run with a 600000 ms timeout, that polls for up to 9 minutes. Number the rounds `1`, `2`, … in the first line, so **no two rounds are identical**:

```bash
: forge-scout round <n>
for i in $(seq 18); do
  if [ -e "<main>/.STOP_FORGE" ] || [ -e "<worktree>/.STOP_FORGE" ]; then echo STOP_FORGE; exit 0; fi
  left=$(gh run list --commit <sha> --limit 100 --json status \
    --jq 'if any(.[]; .status == "waiting") then 0 else [.[] | select(.status != "completed")] | length end')
  [ "$left" = 0 ] && break
  sleep 30
done
gh run list --commit <sha> --limit 100 --json databaseId,name,status,conclusion,attempt,url
```

- `STOP_FORGE`: the kill switch is set. **Stop**: end your turn saying which file exists; the guard blocks every further call.
- Runs still not `completed` (and none `waiting`): run the next round.
- After **60 minutes** of rounds with runs still unfinished: return `ERROR` saying which runs are still in progress; resuming the workstream watches again.

<!-- @spec SPOKE-SCOUT-CI-003, SPOKE-SCOUT-CI-004, SPOKE-SCOUT-CI-005, SPOKE-SCOUT-CI-006 -->
## 6. Judge the runs

| Run | Counts as |
|---|---|
| `conclusion` `success`, `skipped`, `neutral` | Passed |
| `conclusion` `failure`, `timed_out`, `startup_failure`, `cancelled` | Failed |
| `status` `waiting`, or `conclusion` `action_required` | **`ERROR`** naming the run: only the user can approve it |

Every run passed: return `CI_GREEN`.

Any run failed: for each failed run,

```bash
gh run view <id> --json jobs --jq '.jobs[] | select(.conclusion == "failure") | {name, steps: [.steps[] | select(.conclusion == "failure") | .name]}'
gh run view <id> --log-failed | tail -n 300
```

and write `<scout>/ci-<id>.md`: the run (name, ID, URL, attempt), the failed jobs and steps, and for each failure the **shortest log excerpt that shows it**, not the whole log. Return `CI_FAILED` with `escalation.details` naming each `ci-<id>.md` file and a one-line cause per run.

<!-- @spec SPOKE-SCOUT-RERUN-001 -->
## 7. Mode `CI_RERUN`

Re-run the failed jobs of every run for `HEAD` whose latest attempt failed (section 6), then watch exactly as `CI_WATCH` does (sections 4–6):

```bash
gh run list --commit <sha> --limit 100 --json databaseId,conclusion \
  --jq '.[] | select(.conclusion | IN("failure","timed_out","startup_failure","cancelled")) | .databaseId'
gh run rerun <id> --failed
```
