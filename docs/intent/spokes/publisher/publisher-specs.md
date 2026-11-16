# Publisher — EARS Specs

Design: `publisher-design.md`. Implementation: `reference/skills/forge-publisher/SKILL.md`.

Every spec here describes what the `forge-publisher` skill tells the publisher to do; they are verified by the sandbox scenario run, not by pytest.

## Push

- [ ] **SPOKE-PUB-PUSH-001**: When dispatched with mode `push` in a repository where `git remote get-url origin` fails, the `forge-publisher` skill shall tell the publisher to return `PUBLISH_REFUSED` saying the repository has no `origin`.
- [ ] **SPOKE-PUB-PUSH-002**: When dispatched with mode `push`, the `forge-publisher` skill shall tell the publisher to run `git fetch origin <identity.branch>`, ignoring a missing remote branch, before the publish gate.
- [ ] **SPOKE-PUB-PUSH-003**: The `forge-publisher` skill shall tell the publisher in mode `push` to run `verify_gate.py check-publish --target push` and, when it is not `ok`, return `PUBLISH_REFUSED` with its error findings in `escalation.details`.
- [ ] **SPOKE-PUB-PUSH-004**: The `forge-publisher` skill shall tell the publisher in mode `push`, when the gate is `ok`, to run `git push -u origin HEAD:refs/heads/<identity.branch>` and return `PUBLISHED`, or `PUBLISH_REFUSED` with git's message when the push fails.

## Merge

- [ ] **SPOKE-PUB-MERGE-001**: When dispatched with mode `merge`, the `forge-publisher` skill shall tell the publisher to run `verify_gate.py check-publish --target merge` and, when it is `ok`, return `MERGE_READY` with the summary naming the base branch the user merges into.
- [ ] **SPOKE-PUB-MERGE-002**: The `forge-publisher` skill shall tell the publisher in mode `merge` to return `MERGE_BLOCKED` with the findings when the gate reports `MERGE_CONFLICT` or `NO_MERGE_TARGET`, and `PUBLISH_REFUSED` with the findings for any other error finding.

## Every result

- [ ] **SPOKE-PUB-OUT-001**: The `forge-publisher` skill shall tell the publisher to set `publish_target` to its mode in every result.
- [ ] **SPOKE-PUB-BOUND-001**: The `forge-publisher` skill shall tell the publisher never to merge, never to force a push, and never to change the repository's files or branches other than by the push above.
