# CI runs

`<main>` is the main worktree's root: `dirname "$(git rev-parse --path-format=absolute --git-common-dir)"`. `<worktree>` is `identity.worktree_path`. `<sha>` is `git rev-parse HEAD`. `<org>`/`<repo>` are `identity.org`/`identity.repo`.

- **List the runs for a commit**: `gh run list -R <org>/<repo> --commit <sha> --limit 100 --json databaseId,name,status,conclusion,attempt,url`.

- **Wait for runs to appear**: one shell command, every 30 seconds for up to 5 minutes, stopping at the first non-empty list. A `gh` failure ends it at once, non-zero.

  ```bash
  : forge-scout list
  for i in $(seq 11); do
    if [ -e "<main>/.STOP_FORGE" ] || [ -e "<worktree>/.STOP_FORGE" ]; then echo STOP_FORGE; exit 0; fi
    n=$(gh run list -R <org>/<repo> --commit <sha> --limit 100 --json databaseId --jq length) || exit 1
    [ "$n" != 0 ] && break
    [ "$i" -lt 11 ] && sleep 30
  done
  echo "runs: $n"
  ```

- **Wait for runs to finish, in rounds**: one shell command per round, run with a 600000 ms timeout, polling every 30 seconds for up to 9 minutes. It stops early when no run is unfinished or one is `waiting`. Number the rounds `1`, `2`, ... in the first line, so no two rounds are identical. A `gh` failure ends it at once, non-zero.

  ```bash
  : forge-scout round <n>
  for i in $(seq 18); do
    if [ -e "<main>/.STOP_FORGE" ] || [ -e "<worktree>/.STOP_FORGE" ]; then echo STOP_FORGE; exit 0; fi
    left=$(gh run list -R <org>/<repo> --commit <sha> --limit 100 --json status \
      --jq 'if any(.[]; .status == "waiting") then 0 else [.[] | select(.status != "completed")] | length end') || exit 1
    [ "$left" = 0 ] && break
    sleep 30
  done
  gh run list -R <org>/<repo> --commit <sha> --limit 100 --json databaseId,name,status,conclusion,attempt,url
  ```

- **A failed run's failures**: `gh run view <id> -R <org>/<repo> --json jobs`, keeping the failed jobs and their failed steps (`--jq '.jobs[] | select(.conclusion == "failure") | {name, steps: [.steps[] | select(.conclusion == "failure") | .name]}'`), and `gh run view <id> -R <org>/<repo> --log-failed | tail -n 300`.

- **Failed runs of a commit**: `gh run list -R <org>/<repo> --commit <sha> --limit 100 --json databaseId,conclusion --jq '.[] | select(.conclusion | IN("failure","timed_out","startup_failure","cancelled")) | .databaseId'`.

- **Re-run failed jobs**: `gh run rerun <id> -R <org>/<repo> --failed`. The only command in this file that changes anything on GitHub.

How long to keep issuing rounds, and what each conclusion counts as, is your role's to decide.
