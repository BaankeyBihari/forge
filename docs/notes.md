# Forge — Multi-Agent Engineering Orchestration System

> **Implementation Blueprint & Protocol Specification**
> **Target Harnesses:** Claude Code, Google Antigravity CLI, OpenAI Codex CLI, Pi (`pi-coding-agent`)
> **Skill Standard:** Agent Skills (`SKILL.md`) + `memoir` Episodic Persistence + Linked-Intent Development (LID)

---

## 1. System Vision & Architecture Overview

**Forge** is a deterministic, Git-anchored **Hub-and-Spoke multi-agent engineering system** augmented by an on-demand **Advisory Council** and partitioned **episodic workstream memory**.

### Core Architectural Principles

1. **Hub-and-Spoke Determinism:** The Main Conversation runs the **Commander (`/forge`)** hub. Spokes never message each other directly; all transitions route through the Hub using structured YAML/JSON envelopes and SHA-anchored state.
2. **Git as the Provenance Bus:** Every mutating spoke (`planner`, `qa`, `dev`) produces atomic commits signed with `Forge-*` Git trailers. Human partial commits carry no `Forge-Agent` trailer and are automatically treated as authoritative ground truth.
3. **Separation of Mutation vs. External I/O:** Local code/test authors (`planner`, `qa`, `dev`) have zero remote network/PR/push authority; read-only auditors (`verifier`, `reviewer`, `scout`, `council`) never mutate repo files; `publisher` and `reporter` execute external/release actions strictly behind dual SHA gates.
4. **Zero Cross-Workstream Pollution:** Runtime state and episodic loop memory live outside the repo under `~/.forge/state/<org>/<repo>/<workstream>/` and are disposed of on workstream completion. Repo-wide knowledge stays exclusively in your main-session native `memoir` store.

### System Topology

```text
                  [ External World: GitHub / Jira / CI ]
                     ▲              ▲               │
                     │ (comms/PR)   │ (push/merge)  │ (issues/PRs/CI)
              [ 8. Reporter ] [ 7. Publisher ] [ 6. Scout ]
                     ▲              ▲               │
                     └──────┬───────┘               ▼
                   [ Main Window: /forge Hub ] ◄────┘
                     │  (Router, HITL & memory/hub)
                     ├───(on blockers)───▶ [ Advisory Council ]
                     │                     (PM, Architect, Senior Dev,
                     │                      Debugger, SecOps, Critic)
       ┌─────────────┼──────────┬───────────┬──────────┐
       ▼             ▼          ▼           ▼          ▼
  [1.Planner]    [2. QA]    [3. Dev]  [4.Verifier] [5.Reviewer]
       └─────────────┴──────────┴───────────┴──────────┘
                      (Local Git Working Tree)

```

### Supported Execution Modes

| Axis | Options | Behavior |
| --- | --- | --- |
| **Repository Methodology** | `LID` | `NAIVE` | Auto-detected by `planner` (presence of `.lid/`, `docs/lid/`, `lid.config.*`). `LID` executes Stages 1–6; `NAIVE` executes `PLAN.md` → tests → implementation. |
| **Planning Cadence** | `strict` | `auto` | `strict` commits and returns to Hub after each LID stage (`S1`→`S2`→`S3`→`S4`). `auto` chains `S1`–`S4` continuously until Stage 4 finishes or an unresolved ambiguity/edge case triggers `CONCERNS`. |
| **Build Collaboration** | `collaborative` | `sequential` | `sequential` runs `QA (full)` → `Dev (full)`. `collaborative` runs `QA (skeletons)` → `Dev (stubs)` → `QA (full)` → `Dev (full)`, allowing human partial implementations after any commit. |

---

## 2. Directory & Repository Layout

### A. Portable Skill Repository (`forge-skills/`)

Follows the open `SKILL.md` standard so a single repository can be symlinked into Claude Code, Codex CLI, Antigravity CLI, and Pi simultaneously:

```text
forge-skills/
├── forge/
│   └── SKILL.md                         # Commander skill (/forge) — Hub router, resume & synthesis
├── forge-protocol/
│   ├── references/
│   │   ├── PROTOCOL.md                  # Canonical schemas, Git trailers, state resolution & envelopes
│   │   └── MEMOIR_HOOKS.md              # Standardized cold-start rehydration & pre-exit crystallization prompts
│   └── scripts/
│       ├── resolve_state.py             # Deterministic <org>/<repo>/<workstream> resolver, lock & drift check
│       ├── verify_gate.py               # Git trailer validator, boundary auditor & dual-SHA gate check
│       └── guard_paths.py               # PreToolUse boundary & runaway loop guard
├── planner/
│   └── SKILL.md                         # LID Stages 1-4 / Naive planning + map-codebase
├── qa/
│   └── SKILL.md                         # Collaborative skeletons, Stage 5 tests & mid-Stage 6 fixes
├── dev/
│   └── SKILL.md                         # Collaborative stubs, Stage 6 impl, arrow-audit, never-touch-tests
├── verifier/
│   └── SKILL.md                         # Read-only mechanical gate, collaborative delta & arrow-audit
├── reviewer/
│   └── SKILL.md                         # Internal pre-push qualitative review & external PR inline review
├── scout/
│   └── SKILL.md                         # Read-only Jira/PR recon & post-push CI/CD log watcher
├── publisher/
│   └── SKILL.md                         # Dual-SHA gated git push / local main merge & workstream cleanup
├── reporter/
│   └── SKILL.md                         # Outbound GitHub PRs, inline review submission & Jira updates
└── council/
    └── SKILL.md                         # Stateless read-only persona panel (BMAD + Obra Superpowers)

```

### B. External Workstream State Layout (`~/.forge/`)

```text
~/.forge/
├── config.yaml                                  # Global defaults (plan_mode, collaborative, council_mode)
└── state/
    └── <org>/                                   # Git origin owner/org (fallback: "local")
        └── <repo>/                              # Git repository name (via --git-common-dir)
            └── <workstream>/                    # Sanitized branch/worktree slug (e.g., feat-jira-412)
                ├── state.yaml                   # Authoritative state machine, SHA anchors & scope manifest
                ├── lock.json                    # Active PID/CLI lock preventing concurrent collisions
                ├── scout/                       # Cached Jira/PR briefs & CI failure extracts
                ├── council/                     # Archived council deliberations for this workstream
                └── memory/                      # Ephemeral cold-start episodic memory (memoir format)
                    ├── hub/                     # Commander orchestration state, HITL questions & pause notes
                    ├── shared/                  # Workstream invariants & approved Council rulings
                    ├── planner/                 # Rejected design options, edge-case rationale
                    ├── qa/                      # Test runner flags, fixture quirks, flaky edge cases
                    ├── dev/                     # Module invariants, build flags, failed implementation paths
                    ├── verifier/                # Historical audit findings & arrow-audit hotspots
                    ├── reviewer/                # Qualitative review history & recurring style/edge notes
                    └── scout/                   # CI pipeline quirks & external ticket metadata

```

---

## 3. Episodic Memory Management (Workstream `memoir` Architecture)

To prevent cold-start amnesia when spokes or the Main Window resume across loop iterations—while preventing feature-branch noise from leaking into other sessions—Forge embeds a **partitioned episodic memory layer** using the `memoir` skill prompt structure (`[https://github.com/BaankeyBihari/memoir-skill](https://github.com/BaankeyBihari/memoir-skill)`).

### A. Hard Firewall: Workstream Memory vs. Repo-Wide `memoir`

* **Repo-Wide Knowledge:** Managed exclusively by you in the Main Conversation using your native `memoir` skill installation. **Never** create a `_repo-memory/` folder inside `~/.forge/` and **never** auto-promote workstream notes upward.
* **Workstream Memory (`~/.forge/state/<org>/<repo>/<workstream>/memory/`):** Strictly scoped to the active branch/worktree. Disposed of (or archived to `~/.forge/archive/`) when `publisher` closes out the workstream.

### B. Partition Access Control List (ACL)

| Partition | Read Access | Write Access | Purpose |
| --- | --- | --- | --- |
| `memory/hub/` | Commander (`/forge`) only | Commander (`/forge`) only | Captures user intent for this branch, pending HITL questions, why a Council option was chosen, and exact resume instructions when a session pauses. |
| `memory/shared/` | All Spokes + Commander | Commander (`/forge`) only | Stores workstream-wide architectural invariants and approved Council rulings after Hub synthesis. |
| `memory/<agent>/` | Owning Spoke only | Owning Spoke only | Stores agent-specific episodic notes (`decisions`, `dead_ends`, `tooling_quirks`) tagged with `sha_anchor`. |

### C. Cold-Start Rehydration & Pre-Exit Crystallization Hooks (`MEMOIR_HOOKS.md`)

Every spoke and the Commander execute two lightweight memory hooks on every turn:

1. **Pre-Flight Rehydration (On Wake):**
* Read `state.yaml` (hard truth for files, SHAs, and remaining scope).
* Read `memory/shared/` and `memory/<self>/` (`memory/hub/` for Commander).
* Filter out or treat as historical any entries whose `sha_anchor` is marked `stale: true` in `state.yaml`.


2. **Pre-Exit Crystallization (Before Returning Envelope):**
* Persist a compact Markdown/YAML memoir entry (`<= 400 tokens`) into `memory/<self>/` using the `memoir` distillation format:
```yaml
timestamp: "2026-11-15T21:30:00+05:30"
sha_anchor: "9f8e7d6"
stage: "LID-S6-FULL"
stale: false
decisions:
  - "Used sliding-window counter in memory instead of token bucket to satisfy EARS-04 burst rule."
dead_ends:
  - "Async middleware wrapper broke Express error propagation in tests/auth.spec.ts:42."
tooling_quirks:
  - "Run targeted tests via `uv run pytest tests/test_rate_limit.py -q` (full suite takes 45s)."

```





### D. SHA-Anchored Invalidation & Disposal

* **On `git reset` / `git revert`:** When `resolve_state.py` detects that `HEAD` moved backward relative to `anchors.last_known_head_sha`, it automatically scans `memory/**/*.yaml` and flags entries anchored to orphaned commits with `stale: true` so agents do not act on rolled-back code assumptions.
* **On Workstream Closeout:** When `publisher` merges to `main` (or finalizes PR closure), it moves `~/.forge/state/<org>/<repo>/<workstream>/` to `~/.forge/archive/<org>/<repo>/<workstream>-<timestamp>/` (or deletes it per `config.yaml` retention setting).

---

## 4. Core Protocol & State Contracts (`PROTOCOL.md`)

### A. Deterministic Path & Workstream Resume Resolution

1. **`<org>` and `<repo>` Derivation:**
* Inspect `git remote get-url origin` (supports `git@host:org/repo.git` and `https://host/org/repo.git`):
* `<org>` = lowercase owner segment (fallback: `local`).
* `<repo>` = lowercase repo name without `.git` (fallback: basename of parent of `git rev-parse --git-common-dir`).




2. **Workstream Resume Priority Cascade (Used by `/forge`):**
* **Priority 1 — Explicit Commander Argument:** `/forge <workstream-slug>` (e.g., `/forge feat-jira-412` or `/forge list`).
* **Priority 2 — Environment Variable:** `$FORGE_WORKSTREAM` (for headless scripts, tmux layouts, or parallel sessions on the same branch).
* **Priority 3 — Active Git Branch / Worktree:** Sanitized `git rev-parse --abbrev-ref HEAD` (`/` → `-`, lowercase; if detached `HEAD`, `detached-<short-sha>`). If invoked on `main`/`master` with no active state, `/forge` lists all open workstreams under `~/.forge/state/<org>/<repo>/`.



### B. Canonical `state.yaml` Schema

```yaml
schema_version: 1
identity:
  org: "acme-corp"
  repo: "payments-service"
  workstream: "feat-jira-412"
  branch: "feat/JIRA-412"
  git_common_dir: "/home/dev/work/acme-corp/payments-service/.git"
  worktree_path: "/home/dev/work/acme-corp/payments-service-wt-412"
  session_id: "sess_20261115_2130"

config:
  repo_mode: LID              # LID | NAIVE
  plan_mode: strict           # strict | auto
  collaborative: true         # true | false
  council_auto_resolve: false # false = require user sign-off on spec/API changes

anchors:
  plan_base_sha: null         # Commit SHA where planner finished planning
  last_known_head_sha: null   # HEAD at end of last Forge turn
  verifier_pass_sha: null     # Must equal HEAD for reviewer & publisher
  reviewer_pass_sha: null     # Must equal HEAD for publisher

lifecycle:
  current_phase: INIT         # INIT | RECON | PLAN_S1 | PLAN_S2 | PLAN_S3 | PLAN_S4 | PRE_QA | QA_SKEL | DEV_STUB | QA_FULL | DEV_FULL | VERIFY_FINAL | REVIEW_INTERNAL | PUBLISH | CI_WATCH | REPORT | DONE
  last_agent: null
  last_status: null

scope_manifest:
  assigned: []                # Full target files/requirements from Stage 4 / PLAN.md
  completed: []               # [{ path, by: "user|qa|dev", commit }]
  remaining: []               # Computed by verifier in collaborative mode

blockers: []                  # Active CONCERNS, TEST_CHANGE_REQUESTED, AUDIT_FAILED, CI_FAILED
council_decisions: []         # [{ id, trigger, personas, resolution, approved_by }]

```

### C. Git Commit Trailers & Human-Commit Reconciliation

Every commit created by `planner`, `qa`, or `dev` **must** end with structured Git trailers:

```text
feat(dev): implement token refresh rotation

Implements refresh rotation per LID Stage 4 EARS-03.

Forge-Agent: dev
Forge-Stage: LID-S6-FULL
Forge-Mode: collaborative
Forge-Input-SHA: e4f5g6h

```

**Automatic Drift Detection (`resolve_state.py`):**

* **`HEAD == last_known_head_sha`:** Normal transition; proceed to scheduled spoke.
* **`last_known_head_sha` is ancestor of `HEAD` (Human partial commit):** User committed manual code/tests in collaborative mode. Invalidate `verifier_pass_sha = null` and `reviewer_pass_sha = null`, and route to `verifier` (`PRE_QA` or `PRE_DEV`) to absorb user commits into `completed` and shrink `remaining`.
* **`HEAD` is ancestor of `last_known_head_sha` (`git reset`/`revert`):** Invalidate `verifier_pass_sha` and `reviewer_pass_sha`, mark orphaned memory entries `stale: true`, and route to `verifier` (`RECONCILE`).
* **Uncommitted changes (`git status --porcelain` non-empty):** Pause and ask user whether to checkpoint as `wip(user): partial implementation` or stash before invoking any mutating spoke.

### D. Hub ↔ Spoke Invocation & Return Envelopes

**Hub → Spoke Input (`invoke`):**

```yaml
invoke:
  agent: "dev"
  state_path: "~/.forge/state/acme-corp/payments-service/feat-jira-412/state.yaml"
  mode: "collaborative"       # strict | auto | collaborative | sequential | internal | external_pr | draft | send
  base_sha: "e4f5g6h"
  assigned_scope:
    - "src/auth/refresh.ts"
  context_refs:
    - ".lid/stage-4-ears.md"
  constraints:
    - "NEVER modify files matching tests/**, *_test.*, or *.spec.*"
    - "Run arrow-audit self-check before committing"

```

**Spoke → Hub Return (`result`):**

```yaml
result:
  agent: "dev"
  status: "IMPLEMENTATION_COMPLETE"
  head_sha: "9f8e7d6"
  files_touched:
    - "src/auth/refresh.ts"
  summary: "Implemented token refresh rotation; all 14 QA tests and arrow-audit pass."
  escalations: []
  next_suggested_agent: "verifier"

```

---

## 5. Commander (`/forge`) & Routing State Machine

### Cold-Start Wake & Resume Protocol

When `/forge` is invoked in a fresh Main Window:

1. Run `resolve_state.py` (`Arg > $FORGE_WORKSTREAM > Git Branch`) and check `lock.json` (clean up dead PID locks; warn if a live terminal holds the lock).
2. Reconcile `HEAD` against `anchors.last_known_head_sha` and check for dirty files.
3. Rehydrate `state.yaml`, `memory/hub/`, and `memory/shared/`.
4. Display a **5-Line Resume Brief** before dispatching:
* **Workstream:** `<org>/<repo>/<workstream>` (`LID` | `collaborative`)
* **Phase & Last Status:** `PRE_DEV` (`verifier` → `PARTIAL_REMAINING`)
* **Progress:** `1/3 files complete (middleware.ts by user); 2 remaining`
* **Hub Context:** *(summary from `memory/hub/`)*
* **Next Step:** *"Dispatch `dev` (`COLLAB-IMPL`) on the 2 remaining files?"*



### Complete Transition & Routing Table

| Current Phase / Trigger | Invoked Agent | Return Status | Next Phase / Hub Action |
| --- | --- | --- | --- |
| `INIT` *(optional issue URL)* | `scout` (`PRE_PLAN`) | `OK` | Save brief in `scout/`; advance to `PLAN_S1` |
| `PLAN_S1..S4` (`plan_mode=strict`) | `planner` (`strict`) | `STAGE_N_COMPLETE` | Pause for user approval/edits; advance to `PLAN_S(N+1)` |
| `PLAN_S1..S4` (`strict` or `auto`) | `planner` | `CONCERNS` | Dispatch `council` (`pm` + `architect`); present options to user; re-run `planner` |
| `PLAN_S4` (`strict` or `auto`) | `planner` | `READY_FOR_QA` | Set `plan_base_sha = HEAD`; advance to `POST_PLAN` verify |
| `POST_PLAN` | `verifier` (`POST_PLAN`) | `PASS` / `AUDIT_FAILED` | On `PASS`: route to `PRE_QA` (collab) or `QA_FULL` (seq). On `AUDIT_FAILED`: `council` → `planner` |
| `PRE_QA` *(collab)* | `verifier` (`PRE_QA`) | `PARTIAL_REMAINING` | Update `scope_manifest`; advance to `QA_SKEL` |
| `QA_SKEL` *(collab Pass 1)* | `qa` (`collaborative`) | `SKELETON_COMMITTED` | Optional user edit pause; advance to `PRE_DEV` |
| `PRE_DEV` *(collab)* | `verifier` (`PRE_DEV`) | `PARTIAL_REMAINING` | Update `scope_manifest`; advance to `DEV_STUB` (Pass 1) or `DEV_FULL` (Pass 2) |
| `DEV_STUB` *(collab Pass 1)* | `dev` (`collaborative`) | `STUBS_COMMITTED` | Optional user edit pause; advance to `PRE_QA` → `QA_FULL` |
| `QA_FULL` *(Stage 5 / Pass 2)* | `qa` | `TESTS_COMMITTED` | Verify via `verifier` (`POST_QA`); optional user edit pause; advance to `DEV_FULL` |
| `DEV_FULL` *(Stage 6 / Pass 2)* | `dev` | `IMPLEMENTATION_COMPLETE` | Advance to `VERIFY_FINAL` |
| `DEV_FULL` *(mid-impl dispute)* | `dev` | `TEST_CHANGE_REQUESTED` | Dispatch `council` (`senior-dev` + `architect`) → route approved delta to `qa` → resume `dev` |
| `VERIFY_FINAL` | `verifier` (`VERIFY_FINAL`) | `PASS` | Sets `verifier_pass_sha = HEAD`; advance to `REVIEW_INTERNAL` |
| `REVIEW_INTERNAL` | `reviewer` (`internal`) | `REVIEW_APPROVED` | Sets `reviewer_pass_sha = HEAD`; advance to `PUBLISH` |
| `REVIEW_INTERNAL` | `reviewer` (`internal`) | `CHANGES_REQUESTED` | Route findings by `route_to` tag (`planner` | `qa` | `dev`) |
| `PUBLISH` | `publisher` | `PUBLISHED` | Advance to `CI_WATCH` (if remote branch pushed) or `REPORT` (if local `main`) |
| `CI_WATCH` | `scout` (`CI_WATCH`) | `CI_GREEN` / `CI_FAILED` | On `CI_GREEN`: advance to `REPORT`. On `CI_FAILED`: `council` (`debugger`) → `dev`/`qa` |
| `REPORT` | `reporter` (`draft`/`send`) | `REPORT_SENT` | Mark `DONE`; archive workstream state |

---

## 6. Spoke Specifications (`SKILL.md` Blueprints)

### 1. `planner`

* **Assigned Skills:** `map-codebase`, LID Stages 1–4 (`HLD`, `LLD`, `Edge Audit`, `EARS`).
* **Write Scope:** `.lid/**`, `docs/**`, `PLAN.md`.
* **Rules:** Detects `LID` vs. `NAIVE`. Runs `map-codebase` in Stage 1. In `strict` mode, commits one stage (`Forge-Stage: LID-S1..S4`) and returns `STAGE_N_COMPLETE`. In `auto` mode, chains Stages 1–4 until complete (`READY_FOR_QA`) or blocked by unresolved ambiguities/edge cases (`CONCERNS` without committing partial stage output).

### 2. `qa`

* **Assigned Skills:** LID Stage 5.
* **Write Scope:** `tests/**`, `*_test.*`, `*.spec.*`, test fixtures/configs. (**Never** production code.)
* **Rules:**
* `collaborative` Pass 1: Scaffolds `describe`/`todo` skeletons (`Forge-Stage: COLLAB-SKEL`).
* `collaborative` Pass 2 / `sequential`: Writes full executable tests (`Forge-Stage: LID-S5`) scoped strictly to `verifier`'s `remaining` manifest while preserving human-written concrete tests verbatim.
* Mid-Stage 6 Repair: Updates test contracts when routed back by the Hub after a `TEST_CHANGE_REQUESTED` resolution.



### 3. `dev`

* **Assigned Skills:** LID Stage 6, `arrow-audit` (pre-commit self-check).
* **Write Scope:** Production source paths (`src/**`, etc.).
* **Hard Boundary:** **NEVER** create, edit, skip, or delete test files.
* **Rules:**
* `collaborative` Pass 1: Scaffolds module files, exported types/interfaces, and stubbed functions (`todo!()` / `NotImplementedError`) matching QA skeletons (`Forge-Stage: COLLAB-STUB`).
* `collaborative` Pass 2 / `sequential`: Implements full production logic (`Forge-Stage: LID-S6`) for `verifier`'s `remaining` manifest while preserving human partial code verbatim. Runs `arrow-audit` before committing.
* If a test blocks valid implementation due to an outdated/incorrect assertion, commits valid progress (if clean) and exits immediately with `TEST_CHANGE_REQUESTED`.



### 4. `verifier`

* **Assigned Skills:** `arrow-audit`, `map-codebase` (read-only).
* **Write Scope:** None in repo (updates `state.yaml` `scope_manifest` and `anchors.verifier_pass_sha` only).
* **Rules:** Audits commit trailers, enforces path boundaries (confirming zero test edits by `dev`), calculates `completed` vs. `remaining` deltas in `PRE_QA`/`PRE_DEV`, runs test suite + `arrow-audit`, and records `verifier_pass_sha = HEAD` on `PASS`.

### 5. `reviewer`

* **Assigned Skills:** Configurable review skill mix (two-stage code review, security/edge review, idiom checks).
* **Write Scope:** None in repo (updates `anchors.reviewer_pass_sha` or writes PR review payload to workstream state).
* **Rules:**
* `mode=internal`: Requires `HEAD == verifier_pass_sha`. Reviews `plan_base_sha..HEAD` against specs. Returns `REVIEW_APPROVED` (setting `reviewer_pass_sha = HEAD`) or `CHANGES_REQUESTED` with each finding tagged `route_to: planner | qa | dev`.
* `mode=external_pr`: Reviews an external PR diff gathered by `scout`, generates line-anchored `inline_comments[]` (`path`, `line`, `severity`, `body`, `suggestion`) + overall verdict, and returns `PR_REVIEW_READY` (`next_suggested_agent: reporter`).



### 6. `scout`

* **Write Scope:** `~/.forge/state/<org>/<repo>/<workstream>/scout/` only.
* **Rules:** Read-only reconnaissance (`PRE_PLAN` Jira/GitHub context, `PR_FETCH` external PR diffs/threads) and post-push CI/CD monitoring (`CI_WATCH` via `gh run watch`), returning structured summaries or parsed failure logs (`CI_GREEN` / `CI_FAILED`).

### 7. `publisher`

* **Write Scope:** Git branches/tags (`git push`, local `git merge`).
* **Hard Gate:** Aborts immediately unless `HEAD == anchors.verifier_pass_sha` **AND** `HEAD == anchors.reviewer_pass_sha` and `git status --porcelain` is empty. Never force-pushes protected branches. Archives workstream state on closeout.

### 8. `reporter`

* **Write Scope:** External APIs only (`gh pr create/edit`, `gh api` / `gh pr review` for inline comments, Jira transitions/comments).
* **Rules:** Supports `mode=draft` (returns formatted PR/Jira/review payload to Hub for approval) and `mode=send` (executes external calls). Grounded strictly in committed specs and `verifier`/`reviewer` receipts.

---

## 7. Advisory Council (`council`) Specification

The **Council** is a stateless, read-only panel invoked by the Hub only when a spoke escalates a blocker.

### Persona Roster (BMAD + Obra Superpowers)

* **`pm`** *(BMAD)*: Scope boundaries, MVP trade-offs, acceptance criteria.
* **`architect`** *(BMAD)*: Module boundaries, data flow, `arrow-audit` dependency resolution.
* **`sec-ops`** *(BMAD)*: Auth, secrets, failure modes, reliability.
* **`debugger`** *(Obra Superpowers)*: Systematic root-cause isolation (`reproduce → isolate → hypothesize`) for CI/test failures.
* **`senior-dev`** *(Obra Superpowers)*: YAGNI minimalism, idiomatic implementation, contract dispute resolution.
* **`critic`** *(Obra Superpowers)*: Devil's advocate stress-testing proposed fixes against edge cases.

### Dynamic Routing & Execution Protocol

| Blocker Status | Selected Panel (Max 3) | Resolution Policy |
| --- | --- | --- |
| `CONCERNS` (Plan / Edge Audit) | `pm`, `architect`, `critic` | Present Decision Matrix to user for sign-off before re-invoking `planner`. |
| `TEST_CHANGE_REQUESTED` / `CONTRACT_MISMATCH` | `senior-dev`, `architect` | Auto-resolve if internal signature detail; escalate to user if Stage 4 EARS / public API changes. |
| `AUDIT_FAILED` (`arrow-audit`) | `architect`, `senior-dev` | Synthesize minimal refactoring path and route to `dev` or `planner`. |
| `CHANGES_REQUESTED` (`reviewer`) | `senior-dev`, `critic` | Validate severity and route actionable patch list to `planner` / `qa` / `dev`. |
| `CI_FAILED` (`scout`) | `debugger`, `senior-dev` | Isolate root cause from CI logs and route fix to `dev` or `qa`. |

Each persona runs in parallel with a **`< 300`-word output cap**. The Hub synthesizes their outputs into a single **Decision Matrix** (Consensus, Dissenting Risks, Concrete Spec/Code Delta) and logs it to `state.yaml` and `memory/shared/`.

---

## 8. Deterministic Helper Scripts & Guardrail Hooks

### A. `forge-protocol/scripts/resolve_state.py`

* Computes `<org>/<repo>/<workstream>` from `git remote get-url origin` and `git rev-parse --git-common-dir` / `--abbrev-ref HEAD`.
* Initializes `~/.forge/state/<org>/<repo>/<workstream>/` (`state.yaml`, `memory/{hub,shared,planner,qa,dev,verifier,reviewer,scout}/`).
* Compares `HEAD` to `last_known_head_sha`: detects human partial commits (`PRE_QA`/`PRE_DEV` needed) or `git reset`/`revert` (invalidates `verifier_pass_sha` & `reviewer_pass_sha` and flags orphaned `memory/**/*.yaml` entries with `stale: true`).

### B. `forge-protocol/scripts/verify_gate.py`

* **`--check-trailers <base_sha>..HEAD`:** Verifies every automated commit carries valid `Forge-Agent`, `Forge-Stage`, and `Forge-Input-SHA` trailers and touched only allowed file paths for that agent.
* **`--check-publish-gate`:** Exits `0` **only** if `git status --porcelain` is empty and `HEAD == verifier_pass_sha == reviewer_pass_sha`; otherwise exits `1` with exact failure reason.

### C. Pre-Tool Guardrail Hook (`guard_paths.py` / `forge-guard.ts`)

Runs before every tool call across harnesses:

1. **Kill-Switch:** Blocks immediately if `.STOP_FORGE` exists in repo root.
2. **Duplicate-Call Circuit Breaker:** Tracks `(agent, tool_name, args_hash)`; aborts if identical calls repeat `> 3` times.
3. **Read-Only Enforcement:** Blocks file-write/edit tools when `SWARM_ACTIVE_AGENT` / `FORGE_ACTIVE_AGENT` is in `[verifier, reviewer, scout, council]`.
4. **Path Boundary Enforcement:**
* `dev` writing `tests/**`, `*_test.*`, `*.spec.*` → **BLOCK** (`BOUNDARY_VIOLATION: exit with TEST_CHANGE_REQUESTED`).
* `qa` writing non-test source paths → **BLOCK**.
* `planner` writing outside `.lid/**`, `docs/**`, `PLAN.md` → **BLOCK**.



---

## 9. Cross-CLI Adapter Configurations & Model Routing

### A. Recommended Model Tiering Matrix

| Role | Tier | Anthropic (Claude Code / Pi) | OpenAI (Codex CLI / Pi) | Google (Antigravity / Pi) |
| --- | --- | --- | --- | --- |
| **Commander (`/forge` Hub)** | **Tier 1** | `claude-opus-4-6` | `gpt-5.4` | `gemini-3.1-pro` |
| **`planner`** | **Tier 1** | `claude-opus-4-6` | `gpt-5.4` *(high reasoning)* | `gemini-3.1-pro` |
| **`dev` (Stage 6 Full)** | **Tier 1** | `claude-sonnet-4-6` *(or `opus-4-6`)* | `gpt-5.3-codex` | `gemini-3.1-pro` |
| **`dev` (Collab Pass 1 Stubs)** | **Tier 2** | `claude-sonnet-4-6` | `gpt-5.4-mini` | `gemini-3-flash` |
| **`qa`** | **Tier 2** | `claude-sonnet-4-6` | `gpt-5.3-codex` | `gemini-3-flash` |
| **`verifier`** | **Tier 2** | `claude-sonnet-4-6` | `gpt-5.4-mini` | `gemini-3-flash` |
| **`reviewer`** *(Cross-vendor preferred)* | **Tier 1** | `claude-opus-4-6` *(if `dev`=GPT/Gemini)* | `gpt-5.4` *(if `dev`=Claude)* | `gemini-3.1-pro` *(if `dev`=Claude/GPT)* |
| **`scout` / `publisher` / `reporter**` | **Tier 3** | `claude-haiku-4-5` | `gpt-5.4-mini` | `gemini-3-flash` |
| **`council` Personas** | **Tier 1** | `opus-4-6` + `sonnet-4-6` | `gpt-5.4` + `gpt-5.3-codex` | `gemini-3.1-pro` |

### B. Harness Setup Templates

1. **Claude Code:**
* Symlink skills: `ln -s ~/path/to/forge-skills/* ~/.claude/skills/`
* Define subagent profiles in `~/.claude/agents/<role>.md` with `model:` frontmatter and configure `PreToolUse` in `~/.claude/settings.json` to invoke `guard_paths.py`.


2. **Google Antigravity CLI:**
* Symlink skills: `ln -s ~/path/to/forge-skills/* ~/.gemini/antigravity/skills/`
* Surface `strict` plan checkpoints and HITL pauses into **Agent Manager Inbox**; enforce read-only tool permissions on `verifier`, `reviewer`, `scout`, and `council`.


3. **OpenAI Codex CLI:**
* Symlink skills: `ln -s ~/path/to/forge-skills/* ~/.codex/skills/`
* Enable `multi_agent = true`, `max_threads = 4`, `max_depth = 1`, and add `~/.forge` to `writable_roots` in `~/.codex/config.toml`.
* Configure `~/.codex/agents/<role>.toml` with `sandbox_mode = "read-only"` for gatekeepers/council and `sandbox_mode = "workspace-write"` + `max_turns = 40` for mutating spokes.


4. **Pi (`pi-coding-agent`):**
* Symlink skills: `ln -s ~/path/to/forge-skills/* ~/.pi/agent/skills/` and install `pi install npm:pi-subagents`.
* Register `~/.pi/agent/extensions/forge-guard.ts` hooking `pi.on("tool_call", ...)` to enforce `FORGE_ACTIVE_AGENT` path rules, `.STOP_FORGE`, and `>3` duplicate-call circuit breaking.



---

## 10. Testing, Evaluation & Bootstrap Execution Plan

### A. 3-Layer Verification Suite

1. **Layer 1 — Deterministic Unit & Hook Tests (`pytest` / `vitest`, 0 tokens):**
* Test `resolve_state.py` on SSH/HTTPS remotes, linked `git worktree` paths, human partial commits, and `git reset` memory invalidation.
* Test `verify_gate.py` and `guard_paths.py` / `forge-guard.ts` across all role/path combinations and dual-SHA `publisher` gates.


2. **Layer 2 — Single-Spoke Behavioral Evals (`promptfoo`):**
* Verify `planner` (`strict` stops after Stage 1; `auto` triggers `CONCERNS` on ambiguous specs).
* Verify `dev` exits with `TEST_CHANGE_REQUESTED` without editing a deliberately broken test file.
* Verify `reviewer` (`external_pr`) outputs valid line-anchored `inline_comments[]`.


3. **Layer 3 — End-to-End Golden Scenarios (`Inspect AI` or `promptfoo`):**
* Run 3 canonical multi-file tasks across Claude Code, Codex CLI, Antigravity CLI, and Pi: *(1) Clean LID `auto` feature*, *(2) Collaborative mode with simulated human partial commit + cold-start resume*, and *(3) CI failure → Council → recovery → dual-SHA publish*.



### B. Bootstrap Sequence for Claude Code

Pass this document to Claude Code with the following execution order:

1. **Scaffold Directory Tree:** Create `forge-skills/` with all 11 skill folders and script directories.
2. **Write Shared Contracts:** Generate `forge-protocol/references/PROTOCOL.md` and `MEMOIR_HOOKS.md`.
3. **Implement Deterministic Scripts:** Write `resolve_state.py`, `verify_gate.py`, and `guard_paths.py` (plus `forge-guard.ts` for Pi) and verify with `pytest`.
4. **Author `SKILL.md` Files:** Generate `forge/SKILL.md` (Commander) and the 9 spoke/council `SKILL.md` files referencing `PROTOCOL.md` and `MEMOIR_HOOKS.md`.
5. **Install & Wire Harnesses:** Symlink skills into `~/.claude/skills/`, `~/.codex/skills/`, `~/.gemini/antigravity/skills/`, and `~/.pi/agent/skills/`, and generate starter config templates for each CLI.