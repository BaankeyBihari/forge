---
parent: spokes
prefix: SPOKE-QA
---

# QA

## Context and Design Philosophy

`forge-qa` writes tests and nothing else. It works from the plan — the EARS specs in a LID repository, the test section of `PLAN.md` otherwise — over the invocation's `assigned_scope`, which the verifier has narrowed to what is still missing. **In scope** are the workstream specs (added or changed in the workstream, not marked `D`) whose behavior the plan places in those patterns. Tests written by the user are ground truth: the agent adds around them and never rewrites them without approval.

## Modes

| Mode | Phase | Does | Returns |
|---|---|---|---|
| `skeleton` | `QA_SKEL` (two-pass build, pass 1) | Test files with one placeholder per behavior to test — named, annotated with its `@spec` ID in a LID repository or naming its `PLAN.md` test item otherwise, marked skipped or to-do in the framework's own way, no assertions | `SKELETON_COMMITTED` |
| `full` | `QA_FULL` | Executable tests for every behavior in scope; fills placeholders in place | `TESTS_COMMITTED` |
| `repair` | `REPAIR` | Changes tests as the council ruling's delta or the review finding in `notes` says | `REPAIR_COMMITTED` |

Any mode may return `HUMAN_CODE_CHANGE_REQUESTED`, or `CONTRACT_MISMATCH` when the specs contradict each other or a spec cannot be tested as written; `escalation.details` names the specs and why. `CONTRACT_MISMATCH` goes to the council.

When existing tests already cover everything in scope, QA returns its mode's status (`SKELETON_COMMITTED` in `skeleton`, `TESTS_COMMITTED` in `full`) with no commit, and the summary says so.

### Full tests

- Every EARS spec in scope gets at least one test, annotated `@spec <ID>` on the test that exercises it (LID repositories); in other repositories, every test item of `PLAN.md` in scope gets one. A spec that cannot be tested as written is a `CONTRACT_MISMATCH`, not a silent skip.
- After committing, QA runs the tests it wrote. They are expected to fail for behavior not yet implemented; they must not fail to load. A test that cannot be collected or crashes the runner is fixed before returning.
- Existing tests that already cover a behavior in scope are kept and counted; QA adds only what is missing.

### Repairs

A repair changes exactly what its delta names. When the delta would change a human-authored test, QA asks for approval first (`HUMAN_CODE_CHANGE_REQUESTED`), as for any human-authored line.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Skeleton shape | Named, annotated, skipped placeholders with no assertions | Full tests in pass 1; empty files | The user sees the test plan as code and can fill any of it in by hand before QA does; skipped tests keep the suite green meanwhile. |
| Failing tests at commit | Expected for unimplemented behavior; collection errors are not | Require a red run; skip running | Tests-first means red is correct; a test that cannot load proves nothing about the code. |
| Existing tests | Kept and counted toward coverage | Rewritten to the agent's style | *Human code is ground truth*; rewriting also needs approval for every human line. |

## References

- Spokes design (`docs/intent/spokes/`) and protocol design (`spokes/protocol/`).
- Router design (`docs/intent/router/`) — `QA_SKEL`, `QA_FULL`, `REPAIR`.
- Gates design (`docs/intent/gates/`) — test path class.
