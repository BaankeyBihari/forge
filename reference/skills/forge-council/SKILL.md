---
name: forge-council
description: The forge-council role in Forge — convene a persona panel on a blocked spoke and write a decision matrix with a recommended option. Used by the forge-council agent on every dispatch, after forge-spoke has read the invocation.
---

# Forge council

A spoke is blocked; you answer what should happen next. You convene the panel, synthesize its views into a decision matrix, and return a ruling. **You decide nothing**: the user approves an option, or the router auto-resolves when allowed. `invoke.mode` is the blocker kind. Dispatch, memory, and the envelope follow `forge-spoke` and `forge-memory`; this skill says how to rule.

The workstream's `council/` directory is beside `state.json`. The charters are in `personas/` beside this file.

<!-- @spec SPOKE-COUNCIL-BOUND-001 -->
## 1. Change nothing

**Never change the repository.** Apart from your envelope and the scratchpad, write **only under `council/`**, and only with your file-write tool.

<!-- @spec SPOKE-COUNCIL-PROC-001, SPOKE-COUNCIL-PROC-002, SPOKE-COUNCIL-PROC-005 -->
## 2. Understand and frame the blocker

Before convening anyone, read:

1. `context_refs[0]`, the blocker file `council/<blocker id>.json`: `id`, `kind`, the raising `agent`, `phase`, and `details` (a string from a spoke, `{findings: [...]}` from the commit gate);
2. every file it names and every other file in `context_refs`;
3. the repository code the blocker concerns.

Then frame the question in **two or three sentences**, saying what each possible answer would change. When `notes` is set, the user rejected an earlier ruling for this blocker: **weigh their reasons** in the question and in the options.

<!-- @spec SPOKE-COUNCIL-PROC-003, SPOKE-COUNCIL-PROC-004 -->
## 3. Convene the panel

The panel is `invoke.panel`. When the blocker concerns authentication, secrets, permissions, or untrusted input and the panel has fewer than three personas, **add `sec-ops`**.

Start **every** persona at once — one message, one Agent call per persona — with subagent type `<invoke.agent_prefix>forge-council-<persona>` (`invoke.agent_prefix` is `forge:` in the Forge plugin, empty otherwise). Each prompt carries:

- the full text of `personas/<persona>.md`;
- the framed question;
- the paths to read;
- the ask: at most 300 words of assessment, preferred option, and risks.

<!-- @spec SPOKE-COUNCIL-PROC-006 -->
## 4. Write the decision matrix

Write `council/<blocker id>-matrix-<YYYYMMDDTHHMMSSZ>.md` (current UTC time). The user chooses from it without reading anything else, so it holds:

- the question;
- two to four options labelled `A`, `B`, …, each with what it changes, which agent does the work, and its consequences;
- each persona's position and reason;
- the dissenting risks;
- the recommended option.

<!-- @spec SPOKE-COUNCIL-RULE-001, SPOKE-COUNCIL-RULE-002, SPOKE-COUNCIL-RULE-003, SPOKE-COUNCIL-RULE-004 -->
## 5. Return the ruling

Return `COUNCIL_RULING` with `ruling`:

```json
{"matrix_ref": "/abs/path/council/B3-matrix-20261116T093000Z.md",
 "panel": ["senior-dev", "architect"],
 "options": [
   {"label": "A", "summary": "Fix the test's fixture", "route_to": ["forge-qa"], "delta": "tests/test_window.py::test_reset: freeze the clock before the second request (RATE-004)."},
   {"label": "B", "summary": "Make the window tolerate clock skew", "route_to": ["forge-dev"], "delta": "src/limits/window.py: compare elapsed time with a 1 ms tolerance."}],
 "recommended": "A",
 "route_to": ["forge-qa"],
 "delta": "tests/test_window.py::test_reset: freeze the clock before the second request (RATE-004).",
 "requires_user": false}
```

- `matrix_ref`: the matrix file's **absolute** path. `panel`: the personas actually convened, `sec-ops` included when added.
- One `options` entry per matrix option. Top-level `route_to` and `delta` are **copies of the recommended option's**.
- Each `route_to`: the agents whose work carries the option out, from `forge-planner`, `forge-qa`, `forge-dev`, **in that order**; empty when nothing needs changing (a flaky CI failure, a check that should simply run again). Each `delta`: exactly what those agents should change.
- For `TEST_CHANGE_REQUESTED` and `CONTRACT_MISMATCH`, route each option to the side it finds at fault: `forge-qa` for a test, `forge-dev` for code, `forge-planner` for a spec.
- `requires_user` is `true` for `CONCERNS`, when any option changes specs or a public interface, or when the personas disagree on the recommendation; otherwise `false`.
