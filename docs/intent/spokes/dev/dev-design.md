---
parent: spokes
prefix: SPOKE-DEV
---

# Dev

## Context and Design Philosophy

`forge-dev` writes production code to make the tests pass. It never creates, edits, skips, or deletes a test; when a test stands in the way, it says so and stops. The tests and the user's own code are fixed points it builds around.

## Modes

| Mode | Phase | Does | Returns |
|---|---|---|---|
| `stub` | `DEV_STUB` (collaborative pass 1) | Modules, exported types, and function signatures the test skeletons name, with bodies that raise the language's not-implemented error | `STUBS_COMMITTED` |
| `full` | `DEV_FULL` | Production code for everything in scope, run against the tests in scope | `IMPLEMENTATION_COMPLETE` |
| `repair` | `REPAIR` | Changes production code as the delta or finding in `notes` says | `REPAIR_COMMITTED` |

`stub` and `full` may also return `TEST_CHANGE_REQUESTED` or `CONTRACT_MISMATCH`; every mode may return `HUMAN_CODE_CHANGE_REQUESTED`. A `repair` that finds a test or contract in its way returns `ERROR` describing it.

**Tests in scope** are the test files matching `assigned_scope`.

### Implementation

- In a LID repository, code carries `@spec` annotations at the entry point of each specified behavior.
- `full` runs the tests in scope before returning `IMPLEMENTATION_COMPLETE`. When some still fail and dev finds no test at fault, it returns `IMPLEMENTATION_COMPLETE` anyway with the failing tests and what it tried in the summary; the verifier then raises `TESTS_FAILED` and the council's debugger panel takes it. Failures outside scope are named in the summary too.
- Partial human implementations are kept verbatim; dev completes around them.

### Escalations

| Status | When | Before returning |
|---|---|---|
| `TEST_CHANGE_REQUESTED` | A test asserts something the specs do not require, or contradicts them | Commit valid progress; `escalation.details` names the test, what it asserts, and the change needed |
| `CONTRACT_MISMATCH` | The tests and the specs, or two tests, demand incompatible interfaces | Commit valid progress; `details` names both sides |
| `HUMAN_CODE_CHANGE_REQUESTED` | The change needs protected human-authored lines | Commit valid progress; list paths and ranges |

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Wrong tests | Stop with `TEST_CHANGE_REQUESTED`; council and QA decide | Dev edits the test; dev implements around it | A builder that edits the tests it must pass removes the only independent check (HLD *Problem*). |
| Stub bodies | Raise not-implemented | Return defaults | A default return can make a wrong test pass; a raise keeps every unimplemented test red. |
| Done condition | Tests in scope run on dev's own run; persistent failures with no test at fault are handed on, named | Commit and let the verifier find out; stop with `ERROR` | Most failures are fixed in the same dispatch; one dev cannot fix after trying goes to the debugger panel through `TESTS_FAILED` instead of stopping the workstream. |

## References

- Spokes design (`docs/intent/spokes/`) and protocol design (`spokes/protocol/`).
- Router design (`docs/intent/router/`) — `DEV_STUB`, `DEV_FULL`, dev blockers and their council panel.
- Guard design (`docs/intent/guard/`) — dev writes to test paths are blocked with the `TEST_CHANGE_REQUESTED` reason.
