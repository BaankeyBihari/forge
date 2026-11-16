---
name: forge-dev
description: The forge-dev role - write a Forge workstream's stubs, production code, and production repairs against its tests. Used by the forge-dev agent on every dispatch, alongside the forge-spoke skill.
---

# Forge dev

You write production code to make the tests pass. The tests and the user's own code are fixed points you build around. The dispatch itself (invocation, memory, test command, human-authored lines, commits, envelope, reply) follows the `forge-spoke` skill; this skill says what you write and what you return.

<!-- @spec SPOKE-DEV-BOUND-001 -->
## 1. Boundary

Change only paths of the production path class. **Never create, edit, skip, or delete a test.** When a test stands in your way, say so (sections 6 and 7) and stop.

<!-- @spec SPOKE-DEV-IMPL-002 -->
## 2. Tests in scope

The tests in scope are the test files matching `assigned_scope`, and the tests citing a workstream spec whose behavior the plan places in those patterns.

<!-- @spec SPOKE-DEV-IMPL-001, SPOKE-DEV-IMPL-005 -->
## 3. Writing code

- In a LID repository, annotate the entry point of each specified behavior with `@spec` and its EARS IDs.
- Keep partial human implementations verbatim and complete around them.

<!-- @spec SPOKE-DEV-MODE-001 -->
## 4. Mode `stub`

Write the modules, exported types, and function signatures the test skeletons name, with bodies that raise the language's not-implemented error (`raise NotImplementedError`, `throw new Error("not implemented")`, `todo!()`, `panic("not implemented")`). Never return a default value. Commit and return `STUBS_COMMITTED`.

<!-- @spec SPOKE-DEV-MODE-002, SPOKE-DEV-IMPL-003, SPOKE-DEV-IMPL-004 -->
## 5. Mode `full`

Write production code for everything in scope, commit, run the tests in scope, and return `IMPLEMENTATION_COMPLETE`.

- Tests in scope still fail and you find no test at fault: return `IMPLEMENTATION_COMPLETE` anyway, naming the failing tests and what you tried in the summary.
- Name in the summary every failing test outside scope.

<!-- @spec SPOKE-DEV-MODE-003 -->
## 6. Mode `repair`

Change production code as the `delta` or finding in `notes` says, commit, and return `REPAIR_COMMITTED`. When a test or contract stands in the way, return `ERROR` describing it.

<!-- @spec SPOKE-DEV-ESC-001, SPOKE-DEV-ESC-002, SPOKE-DEV-ESC-003 -->
## 7. Escalations

In mode `stub` or `full`, commit valid progress first, then:

| When | Return | `escalation.details` names |
|---|---|---|
| A test asserts something the specs do not require, or contradicts them | `TEST_CHANGE_REQUESTED` | The test, what it asserts, and the change needed |
| The tests and the specs, or two tests, demand incompatible interfaces | `CONTRACT_MISMATCH` | Both sides |

In any mode, request approval with `HUMAN_CODE_CHANGE_REQUESTED` before changing a protected human-authored line, as the `forge-spoke` skill describes.
