---
parent: spokes
prefix: SPOKE-VER
---

# Verifier

## Context and Design Philosophy

`forge-verifier` is the mechanical gate between building steps. It changes nothing in the repository; it runs checks, runs the tests, and reports. What a script can decide, a script decides: `lid_check.py` for spec coverage and annotations, `verify_gate.py` for trailers and boundaries, `forge_spoke.py scope` for which commits touched which scope patterns. The verifier adds judgment only where no script can: whether the plan's specs say what its design says, whether a pattern's work is finished.

A verifier pass is a claim the router does not take on trust: at `VERIFY_FINAL` the router runs the commit gate itself before recording `verifier_pass_sha` (router design, *Commit gate*).

## `lid_check.py`

```text
lid_check.py --state PATH
```

In `reference/scripts/`, Python standard library only. Prints one JSON object and exits `0`; a usage error exits `2`. The same check is importable as `check(state)`, which the router calls before pausing `publish_approval` (router design, *Merged specs*). Runs in `identity.worktree_path`; returns `NOT_LID` when `config.repo_mode` is not `LID`, and `STATE_INVALID` for a missing or unparsable `state.json`.

**Specs.** A spec is a line of a `docs/intent/**/*-specs.md` file of the form `- [<marker>] **<ID>**: <text>`, where the marker is ` `, `x`, or `D`, and the ID is upper-case hyphen-separated segments ending in a three-digit number. Specs marked `D` (deferred) need no test and no code. A spec no automated test can verify — one checked only by a person or a manual scenario run — is marked `D`, with the reason for deferring stated in its spec file, so the check stays strict for every other spec (linked-intent development's deferral rule).

**Workstream specs.** A spec belongs to the workstream when its line was added or changed by a non-merge first-parent commit in `workstream_base_sha..HEAD` (every non-merge first-parent commit reachable from `HEAD` when `workstream_base_sha` is `null`). Coverage is checked only for them, so a repository's older gaps do not fail every workstream. Forge agents never make merge commits, so a merge on the first-parent chain is a human bringing in another branch, usually the base branch; the specs it brings belong to the work on that branch.

**Merged specs.** A spec that is not a workstream spec, whose line a merge on that chain added or changed, and that is not marked `D` and has no test citation is listed in `merged_specs`. It is not a finding: it does not fail the workstream, but it shows the user that the merge brought in unimplemented intent they may want to take up, here or in a workstream of its own.

**Annotations.** An `@spec` annotation is the text `@spec` followed by one or more comma-separated tokens on the same line, each matching the spec ID syntax; text after `@spec` that does not match it (prose such as `@spec <ID>`) is not an annotation. One in a file of the test path class is a test citation; one in a file of the production class is a code tag (gates design, *Path Classes*).

| Finding | When |
|---|---|
| `DUPLICATE_ID` | Another spec line anywhere in the repository defines a workstream spec's ID; a spec that only mentions an ID in its text does not count |
| `UNKNOWN_ID` | An annotation in a file changed in the workstream's range cites an ID no spec line has |
| `UNTESTED` | A workstream spec not marked `D` has no test citation |
| `UNTAGGED` | A workstream spec not marked `D` has no code tag |

Output: `{"status": "OK", "workstream_specs": [IDs], "merged_specs": [IDs], "findings": [{kind, id, path?, line?, detail}]}`. Each finding names one ID; every finding is reported, and the verifier's mode decides which kinds fail it.

## Modes

| Mode | Checks | Returns |
|---|---|---|
| `POST_PLAN` | LID: `lid_check.py` has no `DUPLICATE_ID`; every LLD behavior the plan adds has a spec and every new spec traces to the LLD (judgment). Naive: `PLAN.md` exists and states goal, approach, scope, and tests. Both: `scope_manifest.assigned` is empty only when the plan changes nothing outside plan paths (judgment) | `PASS` or `AUDIT_FAILED` |
| `PRE_QA` | For each assigned pattern, whether its tests are complete, from `forge_spoke.py scope` and the tests in scope (judgment) | `PARTIAL_REMAINING` or `NOTHING_REMAINING`, with `scope` |
| `PRE_DEV` | The same for production code | `PARTIAL_REMAINING` or `NOTHING_REMAINING`, with `scope` |
| `POST_QA` | LID: no `UNTESTED` or `UNKNOWN_ID`; the test suite loads without collection errors. Tests are expected to fail here | `PASS` or `AUDIT_FAILED` |
| `VERIFY_FINAL` | The test suite passes; LID: no `lid_check.py` finding of any kind, and the changed code does what its specs say (judgment). The router runs the commit gate itself after a `PASS` | `PASS`, `AUDIT_FAILED`, or `TESTS_FAILED` |
| `RECONCILE` | Recomputes completed and remaining scope after drift: by the `PRE_QA` criterion when `lifecycle.after_reconcile` is `PRE_QA`, `QA_SKEL`, `QA_FULL`, or `POST_QA`; by the `PRE_DEV` criterion for every later phase; and with both lists empty while `scope_manifest.assigned` is empty | `PASS` with `scope` |

**Tests in scope** for a pattern are the test files matching it and the tests citing a workstream spec whose behavior the plan places in it. A pattern is **complete** for `PRE_QA` when every behavior the plan places in it has an executable test; a skipped or to-do placeholder is not a test. It is complete for `PRE_DEV` when its tests run and pass; a skipped test is not passing, and code that raises not-implemented is not an implementation. **The test suite** is `config.test_command` or the discovered command (spokes design) run in full; failures that existed before the workstream still fail `VERIFY_FINAL`, and a user who wants them ignored narrows `config.test_command`. `completed` and `remaining` together equal `scope_manifest.assigned`. `NOTHING_REMAINING` is returned exactly when `remaining` is empty.

`AUDIT_FAILED` and `TESTS_FAILED` carry the failing findings or tests in `escalation.details`, specific enough for the council to route a repair. `TESTS_FAILED` is returned only in `VERIFY_FINAL`, when tests fail; `AUDIT_FAILED` when they pass but a check does not. When both apply, `TESTS_FAILED` is returned, with the check findings also listed. In earlier phases failing tests are expected and never a status of their own.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Structural LID checks | `lid_check.py`, read-only | The verifier follows arrow-maintenance's audit checklist by hand; the planner runs arrow-maintenance's audit-and-update | *Scripts over prompts*: counting citations is mechanical. arrow-maintenance's pass writes `docs/arrows/`, which a read-only role cannot do and a user's repository may not want. |
| Coverage scope | Specs added or changed in the workstream, for every finding kind | Every spec in the repository | A brownfield repository's existing gaps or duplicate IDs would fail every workstream on work it did not touch. |
| Pre-existing test failures | Fail `VERIFY_FINAL`; the user narrows `config.test_command` | Run the suite at `workstream_base_sha` and ignore failures already there | One run, no extra worktree; a published branch with a red suite needs the user's explicit choice. |
| Spec markers | Read only for `D`; Forge never flips `[ ]` to `[x]` | The planner flips markers after implementation | Flipping would need another planning dispatch after the code lands; tests and tags already show what is implemented. |
| Scope completeness | Judgment over script-provided facts | A pattern is complete once any human commit touches it; pure judgment | A partial human commit touches a pattern without finishing it; facts keep the judgment grounded in what git shows. |
| Commit gate | Run by the router only, at `VERIFY_FINAL` | The verifier also runs it at `POST_QA` and `VERIFY_FINAL` | A gate error names a commit already in the branch, which no repair can clear; `forge_spoke.py commit` already prevents trailer and boundary errors when committing. |
| Specs brought in by a merge | Not workstream specs; untested ones listed in `merged_specs`, never a finding | Count them as workstream specs; a pause asking the user whether each belongs to this workstream or another | Counting fails a workstream for intent it never took on. A pause would need a re-planning path for adopted specs; listing them, and naming them in the `publish_approval` question, lets the user decide without one. |
| Specs no test can verify | Marked `D` with the reason in the spec file; `lid_check.py` stays strict | Findings become warnings the verifier rules on; a config list of untested spec prefixes | Linked-intent development defers what is not verified yet and records why; a strict mechanical check needs no model judgment or extra configuration. |
| Tests and checks both failing | `TESTS_FAILED`, with check findings listed | `AUDIT_FAILED` first | The debugger panel handles failing tests; listing the findings keeps them in the same repair. |

## References

- Spokes design (`docs/intent/spokes/`) and protocol design (`spokes/protocol/`) — `forge_spoke.py scope`.
- Router design (`docs/intent/router/`) — verifier phases and their transitions, commit gate.
- Gates design (`docs/intent/gates/`) — path classes.
- `linked-intent-dev` skill — spec file layout, markers, `@spec` placement.
