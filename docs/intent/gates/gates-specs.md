# Gates — EARS Specs

Design: `gates-design.md`. Implementation: `reference/scripts/verify_gate.py`.

## Path classes

- [x] **GATE-PATH-001**: `verify_gate.py` shall classify a repository path as test when it matches any of the workstream's test patterns, else as plan when it is `PLAN.md` at the repository root or lies under `docs/`, else as production.
- [x] **GATE-PATH-002**: While the workstream's `config.test_patterns` is absent, `verify_gate.py` shall use the test patterns `tests/**`, `test/**`, `**/__tests__/**`, `**/testdata/**`, `**/fixtures/**`, `**/*_test.*`, `**/*.test.*`, `**/*.spec.*`, `**/test_*.py`, and `**/conftest.py`.
- [x] **GATE-PATH-003**: Where the workstream's `config.test_patterns` is present, `verify_gate.py` shall use it in place of the default test patterns.
- [x] **GATE-PATH-004**: `verify_gate.py` shall match patterns against `/`-separated repository-relative paths, with `**` matching zero or more whole directories and `*` matching any characters within one path segment.
- [x] **GATE-PATH-005**: `verify_gate.py` shall expose its path classification as an importable function, so the guard classifies paths identically.

## Commits examined

- [x] **GATE-COMMIT-001**: When running `check-commits` over `A..B`, `verify_gate.py` shall examine exactly the commits listed by `git rev-list --first-parent A..B`.
- [x] **GATE-COMMIT-003**: When `check-commits` is given a range that is a single revision `B`, `verify_gate.py` shall examine exactly the commits listed by `git rev-list --first-parent B`.
- [x] **GATE-COMMIT-002**: `verify_gate.py` shall treat a commit without a `Forge-Agent` trailer as a human commit and apply no `check-commits` rule to it.

## Trailers

- [x] **GATE-TRL-001**: If a Forge commit (one carrying a `Forge-Agent` trailer) lacks, or repeats, any of `Forge-Agent`, `Forge-Stage`, `Forge-Mode`, or `Forge-Input-SHA`, then `check-commits` shall report `TRAILER_MISSING` for that commit and trailer.
- [x] **GATE-TRL-002**: If a Forge commit's `Forge-Agent` is not `forge-planner`, `forge-qa`, or `forge-dev`, then `check-commits` shall report `TRAILER_INVALID`.
- [x] **GATE-TRL-003**: If a Forge commit's `Forge-Stage` is not one its agent may use — `LID-S1` to `LID-S4`, `NAIVE-PLAN`, or `REPAIR` for `forge-planner`; `COLLAB-SKEL`, `LID-S5`, `NAIVE-TESTS`, or `REPAIR` for `forge-qa`; `COLLAB-STUB`, `LID-S6`, `NAIVE-IMPL`, or `REPAIR` for `forge-dev` — then `check-commits` shall report `TRAILER_INVALID`.
- [x] **GATE-TRL-004**: If a Forge commit's `Forge-Mode` is not one of its agent's router modes — `strict`, `auto`, `naive`, or `repair` for `forge-planner`; `skeleton`, `full`, or `repair` for `forge-qa`; `stub`, `full`, or `repair` for `forge-dev` — then `check-commits` shall report `TRAILER_INVALID`.
- [x] **GATE-TRL-005**: If a Forge commit's `Forge-Input-SHA` does not resolve to a commit, or that commit is not an ancestor of the Forge commit, then `check-commits` shall report `INPUT_SHA_UNKNOWN`.
- [x] **GATE-TRL-006**: If a Forge commit has more than one parent, then `check-commits` shall report `FORGE_MERGE`.

## Boundaries

- [x] **GATE-BND-001**: For each Forge commit, `check-commits` shall take the changed paths from `git diff --no-renames --name-only <commit>^ <commit>`, so a rename contributes both its old and new path.
- [x] **GATE-BND-002**: If a `forge-planner` commit changes a path that is not a plan path, a `forge-qa` commit changes a path that is not a test path, or a `forge-dev` commit changes a path that is not a production path, then `check-commits` shall report `BOUNDARY_VIOLATION` for each such path.

## Human-authored lines

- [x] **GATE-HUM-001**: For each file a non-root Forge commit changes, `check-commits` shall collect the parent-side line ranges the commit removes or replaces from `git diff -U0 <commit>^ <commit>` and blame exactly those lines with `git blame --porcelain -L` at `<commit>^`.
- [x] **GATE-HUM-002**: `verify_gate.py` shall treat a blamed line as human-authored when its origin commit has no `Forge-Agent` trailer.
- [x] **GATE-HUM-003**: `verify_gate.py` shall treat a human-authored line as baseline when its origin commit is an ancestor of, or equal to, `anchors.workstream_base_sha`, and as workstream otherwise.
- [x] **GATE-HUM-004**: `verify_gate.py` shall treat a baseline line as unprotected when `anchors.plan_base_sha` is set and the line's path matches any pattern in `scope_manifest.assigned`, and every other human-authored line as protected.
- [x] **GATE-HUM-005**: If a Forge commit modifies or deletes a protected human-authored line that no approval covers, then `check-commits` shall report `HUMAN_CODE_MODIFIED` with severity `error`, naming the commit, path, and lines.
- [x] **GATE-HUM-006**: When a Forge commit inserts lines without removing any parent-side line, and the parent lines immediately before and after the insertion point are both protected human-authored lines, `check-commits` shall report `HUMAN_CODE_INSERTION` with severity `warning`.
- [x] **GATE-HUM-007**: When a Forge commit modifies or deletes a binary file whose last change before that commit came from a commit without a `Forge-Agent` trailer, `check-commits` shall apply the protection rules to the file as a whole, as one human-authored change.
- [x] **GATE-HUM-008**: When a Forge commit has no parent, `check-commits` shall skip the human-authored line checks for it.
- [x] **GATE-HUM-009**: When `anchors.plan_base_sha` is set and a Forge commit is that commit or one of its ancestors, `check-commits` shall skip the human-authored line checks for it while still applying the trailer and boundary checks.
- [x] **GATE-HUM-010**: When a Forge commit's `Forge-Agent` is `forge-planner`, `check-commits` shall skip the human-authored line checks for it while still applying the trailer and boundary checks.

## Approvals

- [x] **GATE-APR-001**: `verify_gate.py` shall turn each approval in `state.json` into a set of line identities — (origin commit, origin line number) pairs — by blaming the approval's `ranges` of each of its `paths` at its `base_sha`.
- [x] **GATE-APR-002**: `verify_gate.py` shall treat a protected human-authored line as covered when its own (origin commit, origin line number) is in the identity set of an approval whose `agent` equals the Forge commit's `Forge-Agent` and whose `paths` include the line's path.

## Publish check

- [x] **GATE-PUB-001**: If `git status --porcelain` is not empty, then `check-publish` shall report `DIRTY_TREE`.
- [x] **GATE-PUB-002**: If `anchors.verifier_pass_sha` or `anchors.reviewer_pass_sha` is unset or differs from `HEAD`, then `check-publish` shall report `SHA_MISMATCH`.
- [x] **GATE-PUB-003**: If `check-publish --target push` finds that the current branch's remote-tracking branch exists and is not an ancestor of `HEAD`, then it shall report `NON_FAST_FORWARD`.
- [x] **GATE-PUB-004**: If `check-publish --target merge` finds no local branch named by `identity.base_branch`, then it shall report `NO_MERGE_TARGET`.
- [x] **GATE-PUB-005**: If `check-publish --target merge` finds that `git merge-tree --write-tree <base branch> HEAD` reports conflicts, then it shall report `MERGE_CONFLICT` listing the conflicted paths.
- [x] **GATE-PUB-006**: `check-publish` shall not modify any branch, the index, or the working tree.

## Human-line query

- [x] **GATE-QRY-001**: When `human-lines --path P --agent AGENT` runs, `verify_gate.py` shall return the line ranges of `P` at `HEAD` that are protected human-authored lines under GATE-HUM-002 through GATE-HUM-004 and not covered, per GATE-APR-002, by an approval whose `agent` is `AGENT`.
- [x] **GATE-QRY-002**: When `human-lines` runs with `--agent forge-planner`, `verify_gate.py` shall return no ranges.
- [x] **GATE-QRY-003**: If `human-lines` runs without `--agent`, then `verify_gate.py` shall exit `2` with a message on stderr.

## Command interface

- [x] **GATE-CLI-001**: `verify_gate.py` shall print one JSON object containing `ok` and `findings` for `check-commits` and `check-publish`, and `ranges` for `human-lines`.
- [x] **GATE-CLI-002**: `verify_gate.py` shall give every finding a `kind` and a `severity`, plus `commit`, `path`, `lines`, and `detail` where they apply, with severity `error` for every kind except `HUMAN_CODE_INSERTION`.
- [x] **GATE-CLI-003**: `verify_gate.py` shall set `ok` to `false` exactly when at least one finding has severity `error`.
- [x] **GATE-CLI-004**: `verify_gate.py` shall exit `0` when `ok` is `true`, `1` when `ok` is `false`, and `2` on a usage error or outside a git repository.
- [x] **GATE-CLI-005**: `verify_gate.py` shall expose `check-commits` and `check-publish` as importable functions returning the same result objects as the commands.
- [x] **GATE-CLI-006**: `verify_gate.py` shall never modify the repository or `state.json`.
- [x] **GATE-CLI-007**: `verify_gate.py` shall use only the Python standard library and external `git` commands.
