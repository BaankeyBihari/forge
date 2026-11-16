---
parent: high-level-design
prefix: GATE
---

# Gates

## Context and Design Philosophy

`verify_gate.py` answers, from git alone, whether a range of commits obeys Forge's provenance rules and whether a workstream may publish. Its findings are facts computed from trailers, diffs, and blame, so the router can refuse to record a verifier pass and the publisher can refuse to push without either one trusting a model's account of what happened.

The gate inspects and reports; it never changes the repository or workstream state. It lives in `reference/scripts/`, uses only the Python standard library, and exposes the same checks both as a command and as importable functions (the router calls them directly).

## Path Classes

Every repository path belongs to exactly one class, decided in this order:

1. **Test** — matches any pattern in the workstream's test patterns.
2. **Plan** — `PLAN.md` at the repository root, or anything under `docs/`.
3. **Production** — everything else.

The default test patterns are:

```text
tests/**  test/**  **/__tests__/**  **/testdata/**  **/fixtures/**
**/*_test.*  **/*.test.*  **/*.spec.*  **/test_*.py  **/conftest.py
```

A workstream's `config.test_patterns`, when present, replaces the default list. Patterns use `/`-separated glob syntax where `**` matches any number of directories and `*` matches within one path segment.

The guard component classifies paths with the same function, so a write the guard allows at edit time is a write the gate accepts afterward.

### Write boundaries

| Agent | May change |
|---|---|
| `forge-planner` | Plan paths |
| `forge-qa` | Test paths |
| `forge-dev` | Production paths |

No other agent may author a commit. Changing a path includes adding, modifying, and deleting it; a rename counts as a deletion of the old path and an addition of the new one, so both must be allowed.

## Commit Checks

`check-commits` examines the commits on the range's first-parent chain (`git rev-list --first-parent A..B`; a range given as a single revision `B` means every first-parent commit reachable from `B`, for a workstream begun in a repository with no commits) and ignores commits without a `Forge-Agent` trailer — those are human commits and are subject to no rules here. For each Forge commit:

### Trailers

Trailers are read with git's trailer parser (`%(trailers:key=…,valueonly)`). A Forge commit must carry exactly one of each:

| Trailer | Valid value |
|---|---|
| `Forge-Agent` | `forge-planner`, `forge-qa`, or `forge-dev` |
| `Forge-Stage` | One of the router's stage values, and one that agent may use (`LID-S1`–`LID-S4`, `NAIVE-PLAN`, `REPAIR` for the planner; `COLLAB-SKEL`, `LID-S5`, `NAIVE-TESTS`, `REPAIR` for QA; `COLLAB-STUB`, `LID-S6`, `NAIVE-IMPL`, `REPAIR` for dev) |
| `Forge-Mode` | One of that agent's router modes |
| `Forge-Input-SHA` | A commit that exists and is an ancestor of this commit |

A Forge commit that is a merge commit is reported as `FORGE_MERGE`: no agent that commits is meant to merge.

### Boundaries

Each path the commit changes (`git diff --no-renames --name-only <commit>^ <commit>`) must be in the agent's write boundary. A violation is reported per path.

### Human-authored lines

A Forge commit may not modify or delete a protected human-authored line without an approval covering it. It may add lines anywhere; an insertion between two protected human-authored lines is reported as a warning so the user sees it at review.

Human-authored lines fall into two groups by their origin commit:

- **Baseline** — the origin commit is an ancestor of (or equal to) `anchors.workstream_base_sha`, the `HEAD` when the workstream was created. This is the code that existed before Forge started.
- **Workstream** — any other human origin commit: commits the user made, or merged in, after the workstream began.

Workstream lines are always protected. Baseline lines are protected except in a path that matches the planner's `scope_manifest.assigned` patterns, once the plan is approved (`anchors.plan_base_sha` is set): approving the plan approves changing the existing code it says to change.

A Forge commit that is `anchors.plan_base_sha` or one of its ancestors is a planning commit the user approved with the plan, and is exempt from the human-authored line checks. A `forge-planner` commit after `plan_base_sha` is exempt too: it can change only plan paths, and the user approved it at the pause that caused it (the findings sign-off for a planner repair). Trailers and boundaries of every Forge commit are still checked.

For each file the commit changes, the gate takes the commit's diff against its parent with no context (`-U0`) and collects the line ranges the commit removes or replaces on the parent side. It blames exactly those lines in the parent (`git blame --porcelain -L … <commit>^ -- <path>`). Each blamed line has an origin commit and an origin line number; the line is human-authored when its origin commit has no `Forge-Agent` trailer.

A binary file the commit modifies or deletes counts as one human-authored change when the commit that last changed it before this one has no `Forge-Agent` trailer.

An insertion is a hunk that removes nothing on the parent side. When the parent's lines immediately before and after the insertion point are both protected human-authored lines, the gate reports `HUMAN_CODE_INSERTION` as a warning.

A commit with no parent has no prior lines and is skipped.

### Approvals

The router records approvals in `state.json` as `{id, agent, paths, ranges, base_sha}`, where `ranges` are line ranges of each path as it stood at `base_sha`. The gate turns an approval into a set of line identities by blaming those ranges at `base_sha`: each identity is the pair (origin commit, origin line number). A human-authored line touched by a later commit is covered when its own identity is in the set of an approval for the same agent and path.

Identities, not line numbers, are compared because the line numbers of an approved region shift as the agent edits the file above it, while each line's blame origin does not change until the line itself is changed.

## Publish Check

`check-publish` passes only when all of these hold:

- `git status --porcelain` is empty.
- `anchors.verifier_pass_sha` and `anchors.reviewer_pass_sha` are both set and both equal `HEAD`.
- For `--target push`: the remote-tracking branch for the current branch either does not exist or is an ancestor of `HEAD`, so the push is a fast-forward.
- For `--target merge`: the local branch named by `identity.base_branch` exists, and merging `HEAD` into it would succeed without conflicts, checked with `git merge-tree --write-tree <base branch> HEAD` (git 2.38 or later), which computes the merge without touching any branch or the working tree.

## Human-Line Query

`human-lines --path P --agent AGENT --state STATE_JSON` returns the line ranges of `P` at `HEAD` that are protected human-authored lines, applying the baseline rule above and leaving out every line covered by an approval for `AGENT` and `P` (*Approvals*). A spoke uses it before editing a file to know which lines it would need approval for, rather than discovering a violation after it commits; after the user approves a change, the approved lines no longer appear. For `forge-planner` it returns no ranges, because planner commits are exempt.

## Command Interface

```text
verify_gate.py check-commits --range A..B --state STATE_JSON
verify_gate.py check-publish --target push|merge --state STATE_JSON
verify_gate.py human-lines --path PATH --agent AGENT --state STATE_JSON
```

Each command prints one JSON object: `{"ok": true|false, "findings": [...]}` (or `{"ranges": [...]}` for `human-lines`). `ok` is `false` when any finding has severity `error`; warnings alone leave it `true`. Exit `0` means `ok`, `1` means not `ok`, `2` means a usage error or not a git repository.

Each finding carries `kind` and `severity` (`error` unless the table says otherwise), and as applicable `commit`, `path`, `lines`, and `detail`:

| Kind | Meaning |
|---|---|
| `TRAILER_MISSING` | A required Forge trailer is absent or repeated |
| `TRAILER_INVALID` | A trailer value is not allowed for that agent |
| `INPUT_SHA_UNKNOWN` | `Forge-Input-SHA` does not resolve or is not an ancestor |
| `FORGE_MERGE` | A Forge-trailered merge commit |
| `BOUNDARY_VIOLATION` | A path outside the agent's write boundary |
| `HUMAN_CODE_MODIFIED` | A protected human-authored line modified or deleted without a covering approval |
| `HUMAN_CODE_INSERTION` | Warning: lines inserted between two protected human-authored lines |
| `DIRTY_TREE` | Uncommitted changes at publish |
| `SHA_MISMATCH` | A pass SHA is missing or differs from `HEAD` |
| `NON_FAST_FORWARD` | The push would rewrite the remote branch |
| `NO_MERGE_TARGET` | The base branch does not exist locally |
| `MERGE_CONFLICT` | Merging `HEAD` into the base branch would conflict; `detail` lists the conflicted paths |

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Human-line rule | Modifying or deleting a protected human line needs approval; insertions between protected lines are warnings | Any change to a file with human lines needs approval; insertions between human lines need approval | Agents must be able to add code beside human code; the rule protects what the human wrote, and the warning keeps insertions into human code visible at review without blocking. Detecting "inside a human function" from neighbouring lines is too approximate to block on. |
| Pre-existing code | Covered by plan approval within the plan's assigned paths | Protected everywhere; never protected | Protecting all of a brownfield codebase would put an approval on nearly every dev step; never protecting it would let agents rewrite code the plan did not mention. |
| Planning commits | Every `forge-planner` commit is exempt from the human-line checks; trailers and boundaries still checked | Human-line checks apply to planner commits; exempt only up to `plan_base_sha` | The planner revises existing design docs, which are human-authored; the user reviews those edits at plan approval, and planner repairs only follow a findings sign-off, so an approval per line would repeat that review. Trailers and boundaries cost nothing to check and catch a planner writing outside `docs/`. |
| Approved lines in the human-line query | Left out for the approved agent | Reported; the spoke reconciles approvals itself | A spoke dispatched again after an approval would otherwise see the same lines protected and ask again. Matching identities is mechanical. |
| Commits examined | First-parent chain of the range | Every commit in the range | Merging another branch in brings other workstreams' Forge commits, whose approvals live in other workstreams' state. |
| Approval matching | Blame line identities (origin commit, origin line) | Line numbers at approval time | Identities survive edits elsewhere in the file; line numbers would silently cover the wrong lines after the first insertion above them. |
| Plan paths | `docs/**` and `PLAN.md` | Also `.lid/**` | Current LID keeps its design tree under `docs/`; a `.lid/` directory is not part of it. |
| Test patterns | Default list, replaceable per workstream | Fixed list; per-repository config file | Test layouts vary by language; overriding per workstream needs no file in the repository. |
| Renames | Treated as delete plus add | Follow renames | Moving a test file is still a change to a test path; following renames would let a rename carry a file across boundaries. |
| Exit codes | `0` pass, `1` findings | Always `0` with JSON | A gate should be usable as a shell condition (`verify_gate.py check-publish … || abort`). |
| Fast-forward check at publish | Part of the publish gate | Left to the publisher's prompt | Rewriting a remote branch is destructive; a script can check it. |

## Open Questions & Future Decisions

### Deferred
1. Formatter runs: a code formatter that rewraps human-authored lines produces `HUMAN_CODE_MODIFIED` findings, so it needs an approval like any other change to those lines.

## References

- `docs/high-level-design.md` — human-authored code, the publish gate, tenet *Human code is ground truth*.
- Router design (`docs/intent/router/`) — stage and mode values, approval records, the commit gate at `VERIFY_FINAL`.
- Guard design (`docs/intent/guard/`) — reuses the path classes.
