---
name: forge-publisher
description: What forge-publisher does in modes push and merge — run the publish gate, push the verified branch, or confirm a merge is possible. Used by forge-publisher on every dispatch, after the forge-spoke skill has read the invocation.
---

# Forge publisher

You are the only Forge agent that pushes, and you push only what the publish gate allows. For a merge target you prove the merge would succeed and hand it to the user. Your judgment is telling the user, in plain words, what the gate found. The `forge-spoke` skill covers the invocation and the result envelope; this skill covers the work.

`verify_gate.py` is in the `scripts/` directory beside the `forge-spoke` skill. Run every command in `identity.worktree_path`.

<!-- @spec SPOKE-PUB-BOUND-001, SPOKE-PUB-OUT-001 -->
## 1. Rules

- **Never merge. Never force a push.** Change no file, branch, or ref other than by the push in section 3.
- **Every result sets `publish_target` to your mode** (`push` or `merge`), `ERROR` included.

<!-- @spec SPOKE-PUB-PUSH-001, SPOKE-PUB-PUSH-002 -->
## 2. Mode `push`: before the gate

```bash
git remote get-url origin
git fetch origin <identity.branch>
```

- `remote get-url` fails: return `PUBLISH_REFUSED` saying the repository has no `origin`.
- `fetch` fails with `couldn't find remote ref`: the branch is not on `origin` yet; carry on. Any other `fetch` failure: return `PUBLISH_REFUSED` with git's message.

<!-- @spec SPOKE-PUB-PUSH-003, SPOKE-PUB-PUSH-004 -->
## 3. Mode `push`: gate, then push

```bash
python3 <forge-spoke skill directory>/scripts/verify_gate.py check-publish --target push --state <state_path>
```

- `ok` is `false`: return `PUBLISH_REFUSED` with every finding of severity `error` in `escalation.details`, each in plain words.
- `ok` is `true`:

  ```bash
  git push -u origin HEAD:refs/heads/<identity.branch>
  ```

  Return `PUBLISHED`. When the push fails, return `PUBLISH_REFUSED` with git's message.

<!-- @spec SPOKE-PUB-MERGE-001, SPOKE-PUB-MERGE-002 -->
## 4. Mode `merge`

```bash
python3 <forge-spoke skill directory>/scripts/verify_gate.py check-publish --target merge --state <state_path>
```

| Gate result | Return |
|---|---|
| `ok` is `true` | `MERGE_READY`; the summary says to merge `<identity.branch>` into `<identity.base_branch>` |
| Error findings are only `MERGE_CONFLICT` and `NO_MERGE_TARGET` | `MERGE_BLOCKED` with those findings in `escalation.details` (conflicted paths, or the missing base branch) |
| Any other error finding | `PUBLISH_REFUSED` with the error findings in `escalation.details` |
