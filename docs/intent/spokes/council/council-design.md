---
parent: spokes
prefix: SPOKE-COUNCIL
---

# Council

## Context and Design Philosophy

The council answers one question: a spoke is blocked — what should happen next? `forge-council` is dispatched only for a blocker. It convenes two or three persona subagents in parallel, each judging the blocker from one angle, and synthesizes their views into a decision matrix with lettered options and a recommendation. The council decides nothing: the user approves an option (or the router auto-resolves when the user allowed it), and the router routes the work.

The personas' raw output stays inside the council's own subagent context; the Hub sees only the matrix file and the ruling. Every participant is read-only.

## Inputs

- `invoke.panel` — the personas to convene, chosen by the router per blocker kind.
- `context_refs[0]` — `council/<blocker id>.json`: the blocker's kind, raising agent, phase, and details (a string from a spoke; `{findings: [...]}` from the commit gate). Later entries are the `context_refs` of the dispatch that raised the blocker.
- `notes` — the user's reasons after rejecting an earlier ruling for the same blocker.

## Procedure

1. Read the blocker file and every file it or `context_refs` names; read the repository code the blocker concerns.
2. Frame the question in two or three sentences, with what each possible answer would change.
3. Start every panel persona at once with the Agent tool (`forge-council-<persona>`). Each prompt carries the persona's charter (from `personas/<persona>.md` in the `forge-council` skill), the framed question, and the paths to read, and asks for at most 300 words: assessment, preferred option, risks.
4. When the blocker concerns authentication, secrets, permissions, or untrusted input and the panel has fewer than three personas, add `sec-ops`.
5. Write the decision matrix to `council/<blocker id>-matrix-<YYYYMMDDTHHMMSSZ>.md`.
6. Return `COUNCIL_RULING` with `ruling`.

## Decision matrix

The matrix file holds the question; two to four options labelled `A`, `B`, … — each with what it changes, which agent does the work, and its consequences; where each persona stands and why; the dissenting risks; and the recommended option. It is written for the user to choose from without reading anything else.

## The ruling

| Field | Meaning |
|---|---|
| `matrix_ref` | Absolute path of the matrix file |
| `panel` | The personas actually convened, including `sec-ops` when added |
| `options` | `[{label, summary, route_to, delta}]`, one per option in the matrix |
| `recommended` | The recommended option's label |
| `route_to`, `delta` | Copies of the recommended option's `route_to` and `delta`, applied when the router auto-resolves |
| `requires_user` | `true` when any option changes specs or a public interface, when the personas disagree on the recommendation, or for `CONCERNS`; otherwise `false` |

`route_to` lists the agents whose work fixes the blocker (`forge-planner`, `forge-qa`, `forge-dev`), in arrow order; it is empty when nothing needs changing — a flaky CI failure, or a check that should simply run again. `delta` says, for those agents, exactly what to change.

When the user approves an option, the router applies that option's `route_to` and `delta`; for `CONCERNS` it carries the chosen option's `delta` into the re-dispatched planning phase's `notes` (router design). For `TEST_CHANGE_REQUESTED` and `CONTRACT_MISMATCH`, `route_to` names whichever side is wrong: `forge-qa` for the test, `forge-dev` for the code, `forge-planner` for the spec.

## Personas

Forge authors its six persona charters in `reference/skills/forge-council/personas/`:

| Persona | Judges |
|---|---|
| `pm` | Scope, acceptance criteria, what the user asked for versus what is being built |
| `architect` | Module boundaries, data flow, how the change fits the design tree |
| `sec-ops` | Authentication, secrets, failure modes, reliability |
| `debugger` | Root cause of a failure: reproduce, isolate, hypothesize, from logs and code |
| `senior-dev` | The simplest correct implementation, test and contract disputes |
| `critic` | The strongest case against the leading option and the edge cases it misses |

Each persona's agent profile (`claude/agents/forge-council-<persona>.md`) grants Read, Grep, and Glob only; the charter text arrives in the prompt, so the profiles stay generic.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Fan-out | The council subagent spawns personas in parallel | The Hub dispatches each persona | Persona output stays out of the main session (HLD). |
| Per-option routing | Every option carries its own `route_to` and `delta`; the router applies the chosen one | One `route_to`/`delta` for the recommended option only | A user who picks a different option would otherwise get the recommended option's repair. |
| `sec-ops` | Added for security-relevant blockers when the panel has room | Never convened; always convened | No router panel includes it, but security questions need it; a fixed third persona on every blocker adds cost for nothing. |
| Persona charters | Authored by Forge in `reference/` | Adapted at runtime from BMAD and Obra Superpowers | Neither is installed; owning the charters keeps the council self-contained and CLI-neutral. |
| Matrix storage | A file in `council/`, one per ruling | Inline in the envelope | The user reads it at sign-off, possibly in a later session; a rejected ruling's matrix stays for comparison. |

## References

- Spokes design (`docs/intent/spokes/`) and protocol design (`spokes/protocol/`).
- Router design (`docs/intent/router/`) — blocker kinds, panels, `council_signoff`, auto-resolve, `apply_ruling` routing.
- `docs/high-level-design.md` — council fan-out decision.
