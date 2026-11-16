---
parent: spokes
prefix: SPOKE-PUB
---

# Publisher

## Context and Design Philosophy

`forge-publisher` is the only Forge agent that pushes, and it pushes only what the publish gate allows: a clean tree whose `HEAD` both the verifier and the reviewer passed. It never merges; for a merge target it proves the merge would succeed and hands it to the user. Its whole judgment is reading the gate's findings back to the user in plain words.

## Modes

Both modes start by running `verify_gate.py check-publish --target <mode> --state <state_path>`. In `push` mode two steps come first: when `git remote get-url origin` fails, the publisher returns `PUBLISH_REFUSED` saying the repository has no `origin`; otherwise it runs `git fetch origin <identity.branch>` (ignoring a missing remote branch) so the gate's fast-forward check sees the remote as it is now.

### `push`

| Gate result | Then | Returns |
|---|---|---|
| `ok` | `git push -u origin HEAD:refs/heads/<identity.branch>` | `PUBLISHED`; when the push fails, `PUBLISH_REFUSED` with git's message |
| Not `ok` | Nothing | `PUBLISH_REFUSED` with the error findings in `escalation.details` |

### `merge`

| Gate result | Returns |
|---|---|
| `ok` | `MERGE_READY`, with the summary naming the base branch the user merges into |
| `MERGE_CONFLICT` or `NO_MERGE_TARGET` findings | `MERGE_BLOCKED` with those findings |
| Any other error finding | `PUBLISH_REFUSED` with the findings |

Every result carries `publish_target` equal to the mode.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Push form | Explicit refspec to `identity.branch`, setting upstream | Plain `git push` | Works whatever the worktree's upstream configuration; force and `+` refspecs are blocked by the guard anyway. |
| Gate run by the publisher | Yes, before every action | Trust the router's entry guard | The entry guard checks SHAs; the gate also checks the tree and fast-forwardness at the moment of pushing. |
| Gate failure statuses | Merge conflicts and a missing base are `MERGE_BLOCKED`; everything else `PUBLISH_REFUSED` | One status for all | `MERGE_BLOCKED` tells the user to merge the base in; `PUBLISH_REFUSED` means Forge's own checks failed. |

## References

- Spokes design (`docs/intent/spokes/`) and protocol design (`spokes/protocol/`).
- Gates design (`docs/intent/gates/`) — publish check and its findings.
- Router design (`docs/intent/router/`) — `PUBLISH` transitions, `manual_merge`.
