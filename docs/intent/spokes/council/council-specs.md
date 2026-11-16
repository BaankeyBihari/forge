# Council — EARS Specs

Design: `council-design.md`. Implementation: `reference/skills/forge-council/SKILL.md` and `reference/skills/forge-council/personas/<persona>.md`.

Every spec here describes what the `forge-council` skill and its persona charters say; they are verified by the sandbox scenario run, not by pytest.

## Procedure

- [ ] **SPOKE-COUNCIL-PROC-001**: The `forge-council` skill shall tell the council to read the blocker file first in `context_refs`, every file it or the rest of `context_refs` names, and the repository code the blocker concerns, before convening anyone.
- [ ] **SPOKE-COUNCIL-PROC-002**: The `forge-council` skill shall tell the council to frame the blocker as a question of two or three sentences, with what each possible answer would change.
- [ ] **SPOKE-COUNCIL-PROC-003**: The `forge-council` skill shall tell the council to start every persona in `invoke.panel` at once as `forge-council-<persona>` subagents, each prompt carrying that persona's charter, the framed question, and the paths to read, and asking for at most 300 words of assessment, preferred option, and risks.
- [ ] **SPOKE-COUNCIL-PROC-004**: The `forge-council` skill shall tell the council to add `sec-ops` when the blocker concerns authentication, secrets, permissions, or untrusted input and `invoke.panel` has fewer than three personas.
- [ ] **SPOKE-COUNCIL-PROC-005**: The `forge-council` skill shall tell the council, after a rejected ruling, to weigh the user's `notes` when framing the question and building options.
- [ ] **SPOKE-COUNCIL-PROC-006**: The `forge-council` skill shall tell the council to write the decision matrix to `council/<blocker id>-matrix-<YYYYMMDDTHHMMSSZ>.md` with the question; two to four options labelled `A`, `B`, and so on, each with what it changes, which agent does the work, and its consequences; each persona's position and reason; the dissenting risks; and the recommended option.

## Ruling

- [ ] **SPOKE-COUNCIL-RULE-001**: The `forge-council` skill shall tell the council to return `COUNCIL_RULING` with `ruling` holding the matrix file's absolute path in `matrix_ref`, the personas convened in `panel`, one `{label, summary, route_to, delta}` per matrix option in `options`, the recommended option's label in `recommended`, and that option's `route_to` and `delta` at the top level.
- [ ] **SPOKE-COUNCIL-RULE-002**: The `forge-council` skill shall tell the council to set each option's `route_to` to the agents whose work carries it out among `forge-planner`, `forge-qa`, and `forge-dev`, in that order, or empty when nothing needs changing, and its `delta` to exactly what those agents should change.
- [ ] **SPOKE-COUNCIL-RULE-003**: The `forge-council` skill shall tell the council, for `TEST_CHANGE_REQUESTED` and `CONTRACT_MISMATCH`, to route each option to the side it finds at fault: `forge-qa` for a test, `forge-dev` for code, `forge-planner` for a spec.
- [ ] **SPOKE-COUNCIL-RULE-004**: The `forge-council` skill shall tell the council to set `requires_user` to `true` for `CONCERNS`, when any option changes specs or a public interface, or when the personas disagree on the recommendation, and to `false` otherwise.

## Personas

- [ ] **SPOKE-COUNCIL-PERS-001**: The `forge-council` skill shall hold a charter for each of `pm` (scope, acceptance criteria, what was asked versus built), `architect` (module boundaries, data flow, fit with the design tree), `sec-ops` (authentication, secrets, failure modes, reliability), `debugger` (root cause from logs and code: reproduce, isolate, hypothesize), `senior-dev` (simplest correct implementation, test and contract disputes), and `critic` (the strongest case against the leading option and the edge cases it misses).
- [ ] **SPOKE-COUNCIL-PERS-002**: Each persona charter shall tell the persona to read only, answer the framed question from its own angle in at most 300 words, and name its preferred option and risks.

## Boundaries

- [ ] **SPOKE-COUNCIL-BOUND-001**: The `forge-council` skill shall tell the council never to change the repository and to write only under the workstream's `council/` directory apart from its envelope and the scratchpad.
