#!/usr/bin/env python3
"""Forge router: the only place Forge decides what happens next.

Design: docs/intent/router/router-design.md. Specs: docs/intent/router/router-specs.md.

    next_step.py --holder-pid PID --holder-session ID [--workstream SLUG] [--event FILE|-] [--dry-run]

Each call: kill switch, wake, apply the event, handle drift, check for a dirty tree, dispatch.
Prints one JSON decision and exits 0; a non-zero exit is a usage error.
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lid_check  # noqa: E402
import resolve_state  # noqa: E402
import verify_gate  # noqa: E402

BUILDERS = ["forge-planner", "forge-qa", "forge-dev"]
MODES = resolve_state.MODES
STAGE_PAUSE_MODES = {"collab-plan", "collab"}  # plan approval after every stage; strict planner
TWO_PASS_MODES = {"collab-code", "collab"}  # skeleton/stub pass, with edit windows after both
POST_QA_WINDOW_MODES = {"collab-qa", "collab-code", "collab"}
DEV_FULL_WINDOW_MODES = {"collab-dev"}
NON_COMMITTING = {"forge-verifier", "forge-reviewer", "forge-scout", "forge-council", "forge-reporter",
                  "forge-publisher"}
VERIFIER_PHASES = {"POST_PLAN", "PRE_QA", "PRE_DEV", "POST_QA", "VERIFY_FINAL", "RECONCILE"}
DIRTY_OK = {"forge-scout", "forge-reporter"}
PLAN_STAGES = [f"PLAN_S{n}" for n in range(1, 5)]
MUTATING = set(PLAN_STAGES) | {"PLAN", "QA_SKEL", "DEV_STUB", "QA_FULL", "DEV_FULL", "REPAIR"}

ALLOWED = {
    "RECON": {"OK"},
    "PLAN": {"READY_FOR_QA", "CONCERNS"},
    "POST_PLAN": {"PASS", "AUDIT_FAILED"},
    "PRE_QA": {"PARTIAL_REMAINING", "NOTHING_REMAINING"},
    "PRE_DEV": {"PARTIAL_REMAINING", "NOTHING_REMAINING"},
    "QA_SKEL": {"SKELETON_COMMITTED", "CONTRACT_MISMATCH", "HUMAN_CODE_CHANGE_REQUESTED"},
    "DEV_STUB": {"STUBS_COMMITTED", "TEST_CHANGE_REQUESTED", "CONTRACT_MISMATCH", "HUMAN_CODE_CHANGE_REQUESTED"},
    "QA_FULL": {"TESTS_COMMITTED", "CONTRACT_MISMATCH", "HUMAN_CODE_CHANGE_REQUESTED"},
    "POST_QA": {"PASS", "AUDIT_FAILED"},
    "DEV_FULL": {"IMPLEMENTATION_COMPLETE", "TEST_CHANGE_REQUESTED", "CONTRACT_MISMATCH",
                 "HUMAN_CODE_CHANGE_REQUESTED"},
    "VERIFY_FINAL": {"PASS", "AUDIT_FAILED", "TESTS_FAILED"},
    "REVIEW_INTERNAL": {"REVIEW_APPROVED", "CHANGES_REQUESTED"},
    "PUBLISH": {"PUBLISHED", "MERGE_READY", "MERGE_BLOCKED", "PUBLISH_REFUSED"},
    "CI_WATCH": {"CI_GREEN", "CI_FAILED", "CI_NONE"},
    "REPORT": {"REPORT_DRAFTED", "REPORT_SENT"},
    "RECONCILE": {"PASS"},
    "COUNCIL": {"COUNCIL_RULING"},
    "REPAIR": {"REPAIR_COMMITTED", "CONTRACT_MISMATCH", "HUMAN_CODE_CHANGE_REQUESTED"},
    "PR_FETCH": {"OK"},
    "PR_REVIEW": {"PR_REVIEW_READY"},
}
for _stage in PLAN_STAGES:
    ALLOWED[_stage] = {"STAGE_COMPLETE", "READY_FOR_QA", "CONCERNS"}


# @spec ROUTE-ENV-012, ROUTE-ENV-014
def accepted_statuses(phase, agent, mode):
    """Statuses besides ERROR accepted from a dispatch of agent in mode; forge_spoke.py imports this."""
    excluded = set()
    if mode == "strict" and phase in PLAN_STAGES[:3]:
        excluded.add("READY_FOR_QA")
    if phase == "PLAN_S4" or mode == "auto":
        excluded.add("STAGE_COMPLETE")
    excluded |= {"draft": {"REPORT_SENT"}, "send": {"REPORT_DRAFTED"}}.get(mode, set())
    if agent == "forge-planner":
        excluded.add("HUMAN_CODE_CHANGE_REQUESTED")
    if phase == "REPAIR" and agent != "forge-qa":
        excluded.add("CONTRACT_MISMATCH")
    return ALLOWED.get(phase, set()) - excluded

PANELS = {
    "CONCERNS": ["pm", "architect", "critic"],
    "TEST_CHANGE_REQUESTED": ["senior-dev", "architect"],
    "CONTRACT_MISMATCH": ["senior-dev", "architect"],
    "AUDIT_FAILED": ["architect", "senior-dev"],
    "TESTS_FAILED": ["debugger", "senior-dev"],
    "CI_FAILED": ["debugger", "senior-dev"],
}
BLOCKER_KINDS = set(PANELS) | {"CHANGES_REQUESTED"}

PAUSES = {
    "plan_approval": (["approve", "revise"], "Approve the plan, or revise it with notes?"),
    "edit_window": (["continue"], "Make any edits you want, commit them, then continue."),
    "publish_approval": (["approve", "hold"], "Publish? Approve with option push or merge, or hold."),
    "manual_merge": (["done"], "Merge the branch into its base branch yourself, then answer done."),
    "report_approval": (["approve", "revise", "skip"], "Send this report, revise it, or skip it?"),
    "ci_none": (["approve", "wait"], "No CI runs found. Close the workstream, or wait and watch again?"),
    "council_signoff": (["approve", "reject"], "Approve one of the council's options, or reject with notes?"),
    "findings_signoff": (["approve"], "Review findings route to the planner. Approve the repairs?"),
    "human_code_approval": (["approve", "reject"], "An agent asks to change human-authored lines. Approve?"),
    "dirty_tree": (["checkpoint", "stash"], "The working tree has uncommitted changes. Checkpoint or stash them."),
    "attempt_limit": (["retry", "stop"], "This step has repeated three times. Retry or stop?"),
}


def stop(status, detail="", write=True):
    return {"kind": "stop", "status": status, "detail": detail, "write": write}


def goto(phase, **opts):
    return {"kind": "dispatch", "phase": phase, "opts": opts}


def pause(kind, phase, payload=None, question=None, context_refs=None):
    return {"kind": "pause", "awaiting": kind, "phase": phase, "payload": payload or {},
            "question": question, "context_refs": context_refs or []}


def close():
    return {"kind": "close"}


class Ctx:
    def __init__(self, cwd, report, state, dry_run):
        self.report = report
        self.state = state
        self.dry_run = dry_run
        self.sp = Path(report["state_path"])
        self.sd = self.sp.parent
        self.root = report["identity"]["worktree_path"]
        self.lc = state["lifecycle"]
        self.anchors = state["anchors"]
        self.prev_phase = self.lc.get("current_phase")
        self.accounted = set()
        self.extra_drift = []

    def git(self, *args, check=True):
        proc = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise resolve_state.GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
        return proc

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.strip()

    def is_ancestor(self, a, b):
        return self.git("merge-base", "--is-ancestor", a, b, check=False).returncode == 0

    def config(self, key, default=None):
        return (self.state.get("config") or {}).get(key, default)

    @property
    def lid(self):
        return self.config("repo_mode") == "LID"

    @property
    def mode(self):
        return self.config("mode", "collab")


# ------------------------------------------------------------------- dispatch


def first_planning_phase(ctx):
    return "PLAN_S1" if ctx.lid else "PLAN"


# @spec ROUTE-BUILD-003, ROUTE-MODE-008
def agent_and_mode(ctx, phase, opts):
    if phase in PLAN_STAGES:
        if opts.get("agent_mode"):
            return "forge-planner", opts["agent_mode"]
        return "forge-planner", "strict" if ctx.mode in STAGE_PAUSE_MODES else "auto"
    fixed = {
        "RECON": ("forge-scout", "PRE_PLAN"), "PLAN": ("forge-planner", "naive"),
        "QA_SKEL": ("forge-qa", "skeleton"), "DEV_STUB": ("forge-dev", "stub"),
        "QA_FULL": ("forge-qa", "full"), "DEV_FULL": ("forge-dev", "full"),
        "REVIEW_INTERNAL": ("forge-reviewer", "internal"), "PR_FETCH": ("forge-scout", "PR_FETCH"),
        "PR_REVIEW": ("forge-reviewer", "external_pr"),
    }
    if phase in fixed:
        return fixed[phase]
    if phase in VERIFIER_PHASES:
        return "forge-verifier", phase
    if phase == "PUBLISH":
        return "forge-publisher", opts.get("mode", "push")
    if phase == "CI_WATCH":
        return "forge-scout", opts.get("mode", "CI_WATCH")
    if phase == "REPORT":
        return "forge-reporter", opts.get("mode", "draft")
    if phase == "COUNCIL":
        return "forge-council", ctx.state["blockers"][-1]["kind"]
    if phase == "REPAIR":
        return ctx.lc["repair_queue"][0]["agent"], "repair"
    raise ValueError(f"no agent for phase {phase}")


# @spec ROUTE-DISP-003
def stage_for(ctx, phase):
    if phase in PLAN_STAGES:
        return f"LID-S{phase[-1]}"
    return {"PLAN": "NAIVE-PLAN", "QA_SKEL": "COLLAB-SKEL", "DEV_STUB": "COLLAB-STUB",
            "QA_FULL": "LID-S5" if ctx.lid else "NAIVE-TESTS", "DEV_FULL": "LID-S6" if ctx.lid else "NAIVE-IMPL",
            "REPAIR": "REPAIR"}.get(phase)


# @spec ROUTE-DISP-001, ROUTE-DISP-002, ROUTE-DISP-004, ROUTE-GUARD-001, ROUTE-GUARD-002, ROUTE-DRIFT-005
# @spec ROUTE-LIMIT-001, ROUTE-LIMIT-002, ROUTE-LIMIT-006, ROUTE-BLK-004, ROUTE-BLK-015
# @spec ROUTE-DISP-005, ROUTE-DISP-006, ROUTE-DISP-007, ROUTE-DRIFT-008, ROUTE-BUILD-033, ROUTE-BLK-021
# @spec ROUTE-DISP-009
def dispatch(ctx, phase, opts):
    head = ctx.head()
    if phase == "REVIEW_INTERNAL" and ctx.anchors.get("verifier_pass_sha") != head:
        phase, opts = "VERIFY_FINAL", {k: v for k, v in opts.items() if k == "user_caused"}
    if phase == "PUBLISH" and not (ctx.anchors.get("verifier_pass_sha") == ctx.anchors.get("reviewer_pass_sha")
                                   == head):
        phase, opts = "VERIFY_FINAL", {k: v for k, v in opts.items() if k == "user_caused"}
    agent, mode = agent_and_mode(ctx, phase, opts)
    pending = {"phase": phase, "opts": opts}
    if ctx.report.get("dirty") and agent not in DIRTY_OK:
        why = " They may be the dispatched agent's unfinished work, or your own edits." \
            if opts.get("redispatch") else ""
        return pause("dirty_tree", phase, {"pending": pending}, context_refs=[],
                     question=PAUSES["dirty_tree"][1] + why + " Paths: " + ", ".join(ctx.report["dirty"]))
    attempts = ctx.lc.setdefault("attempts", {})
    if opts.get("user_caused") or opts.get("new_repair_item") or phase != ctx.prev_phase:
        count = 1
    elif attempts.get(phase, 0) >= 3:
        return pause("attempt_limit", phase, {"pending": pending, "phase": phase})
    else:
        count = attempts.get(phase, 0) + 1
    attempts[phase] = count

    if opts.get("redispatch") and phase == ctx.prev_phase and ctx.lc.get("invoke"):
        # Same invocation as the outstanding dispatch, under a fresh ID and the current HEAD.
        invoke = {**ctx.lc["invoke"], "dispatch_id": uuid.uuid4().hex, "base_sha": head}
        ctx.lc["dispatched"] = {"dispatch_id": invoke["dispatch_id"], "agent": agent, "mode": mode, "base_sha": head}
        ctx.lc["invoke"] = invoke
        return {"kind": "dispatched", "phase": phase, "invoke": invoke}
    refs = list(opts.get("context_refs", []))
    notes = opts.get("notes")
    scope = []
    if phase in ("QA_SKEL", "DEV_STUB", "QA_FULL", "DEV_FULL"):
        scope = list(ctx.state["scope_manifest"].get("remaining", []))
    elif agent == "forge-verifier":
        scope = list(ctx.state["scope_manifest"].get("assigned", []))
    if phase == "REPAIR":
        item = ctx.lc["repair_queue"][0]
        item_refs = list(item.get("context_refs", []))
        notes, refs = item.get("notes"), item_refs + [x for x in refs if x not in item_refs]
    if agent == "forge-reporter":
        refs = [ctx.lc.get("report_input")]
    task = ctx.sd / "task.md"
    if (agent == "forge-planner" or phase == "REVIEW_INTERNAL") and task.is_file():
        refs = [str(task)] + [x for x in refs if x != str(task)]
    invoke = {"dispatch_id": uuid.uuid4().hex, "agent": agent, "mode": mode, "state_path": str(ctx.sp),
              "base_sha": head, "assigned_scope": scope, "context_refs": refs, "notes": notes}
    stage = stage_for(ctx, phase)
    if agent in BUILDERS:
        invoke["stage"] = stage
    if phase == "COUNCIL":
        blocker = ctx.state["blockers"][-1]
        invoke["panel"] = PANELS[blocker["kind"]]
        invoke["context_refs"] = [str(ctx.sd / "council" / f"{blocker['id']}.json")] \
            + blocker.get("context_refs", []) + refs
    ctx.lc["dispatched"] = {"dispatch_id": invoke["dispatch_id"], "agent": agent, "mode": mode, "base_sha": head}
    ctx.lc["invoke"] = invoke
    ctx.lc["current_phase"] = phase
    if not ctx.anchors.get("last_known_head_sha"):
        ctx.anchors["last_known_head_sha"] = head
    return {"kind": "dispatched", "phase": phase, "invoke": invoke}


# ------------------------------------------------------------------- blockers


# @spec ROUTE-BLK-001, ROUTE-BLK-002, ROUTE-BLK-003, ROUTE-BLK-013, ROUTE-BLK-014, ROUTE-LIMIT-003
# @spec ROUTE-LIMIT-004, ROUTE-BLK-025
def raise_blocker(ctx, kind, agent, phase, details, findings=None, force=False):
    counts = ctx.lc.setdefault("blocker_counts", {})
    if not force and counts.get(kind, 0) >= 3:
        return pause("attempt_limit", phase, {"blocker": {"kind": kind, "agent": agent, "phase": phase,
                                                          "details": details, "findings": findings}},
                     question=f"A fourth {kind} blocker was raised. Retry or stop?")
    counts[kind] = counts.get(kind, 0) + 1
    # context_refs: the raising dispatch's, for the council dispatch and the ruling's repairs.
    blocker = {"id": f"blk-{uuid.uuid4().hex[:8]}", "kind": kind, "agent": agent, "phase": phase,
               "details": details, "context_refs": list(ctx.lc["invoke"]["context_refs"])}
    ctx.state["blockers"].append(blocker)
    if phase != "REPAIR":
        ctx.lc["resume_phase"] = phase if kind in ("CONCERNS", "TEST_CHANGE_REQUESTED", "CONTRACT_MISMATCH",
                                                   "AUDIT_FAILED") else "VERIFY_FINAL"
    if kind == "CHANGES_REQUESTED":
        by_agent = {}
        for f in findings or []:
            by_agent.setdefault(f.get("route_to"), []).append(f)
        ctx.lc["repair_queue"] = [
            {"agent": a, "notes": "\n".join(str(f.get("detail", "")) for f in by_agent[a]),
             "context_refs": [], "findings": by_agent[a], "blocker_id": blocker["id"]}
            for a in BUILDERS if a in by_agent
        ]
        planner = by_agent.get("forge-planner")
        if planner:
            return pause("findings_signoff", "REPAIR", {},
                         question=PAUSES["findings_signoff"][1] + " Planner findings: "
                         + json.dumps([f.get("detail") for f in planner]))
        return goto("REPAIR", new_repair_item=True)
    if not ctx.dry_run:
        (ctx.sd / "council").mkdir(exist_ok=True)
        (ctx.sd / "council" / f"{blocker['id']}.json").write_text(json.dumps(blocker, indent=2))
    return goto("COUNCIL")


def remove_blocker(ctx, blocker_id):
    ctx.state["blockers"] = [b for b in ctx.state["blockers"] if b["id"] != blocker_id]


# @spec ROUTE-BLK-009, ROUTE-BLK-010, ROUTE-BLK-011, ROUTE-BLK-012, ROUTE-BLK-017, ROUTE-BLK-019, ROUTE-BLK-020
# @spec ROUTE-BLK-023, ROUTE-BLK-025, ROUTE-BLK-026
def apply_ruling(ctx, blocker, ruling, option, approved_by):
    """Apply option ({route_to, delta}, labelled option["label"]) of blocker's ruling."""
    ctx.state["council_decisions"].append({"id": f"cd-{uuid.uuid4().hex[:8]}", "blocker_id": blocker["id"],
                                           "panel": ruling.get("panel"), "option": option.get("label"),
                                           "approved_by": approved_by})
    user_caused = approved_by == "user"
    kind = blocker["kind"]
    matrix = [ruling["matrix_ref"]] if ruling.get("matrix_ref") else []
    blocked = blocker.get("context_refs", [])
    delta, route = option.get("delta"), option.get("route_to") or []
    if kind == "CONCERNS":
        remove_blocker(ctx, blocker["id"])
        return goto(blocker["phase"], context_refs=blocked + matrix, notes=delta, user_caused=user_caused)
    if not route:
        remove_blocker(ctx, blocker["id"])
        if kind == "CI_FAILED":
            return goto("CI_WATCH", mode="CI_RERUN", user_caused=user_caused)
        if kind in ("TEST_CHANGE_REQUESTED", "CONTRACT_MISMATCH"):
            if blocker["phase"] == "REPAIR":  # re-run the interrupted item; REPAIR reads the queue head's notes
                head = ctx.lc["repair_queue"][0]
                head["notes"] = "\n".join(n for n in (head.get("notes"), delta) if n)
                return goto("REPAIR", user_caused=user_caused)
            return goto(blocker["phase"], notes=delta, user_caused=user_caused)
        return goto(ctx.lc["resume_phase"], user_caused=user_caused)
    ctx.lc["repair_queue"] = [{"agent": a, "notes": delta, "context_refs": matrix + blocked,
                               "blocker_id": blocker["id"]} for a in route] + ctx.lc.get("repair_queue", [])
    return goto("REPAIR", new_repair_item=True, user_caused=user_caused)


# ------------------------------------------------------------- result events


# @spec ROUTE-ENV-001, ROUTE-ENV-002, ROUTE-ENV-003, ROUTE-ENV-004, ROUTE-ENV-005, ROUTE-ENV-012
def validate_envelope(ctx, env):
    d = ctx.lc.get("dispatched")
    if not d or env.get("dispatch_id") != d["dispatch_id"] or env.get("agent") != d["agent"]:
        return "the envelope does not match the outstanding dispatch"
    phase = ctx.lc["current_phase"]
    allowed = accepted_statuses(phase, d["agent"], d.get("mode")) | {"ERROR"}
    if env.get("status") not in allowed:
        return f"status {env.get('status')} is not allowed for {d['agent']} in {phase}"
    head_sha = env.get("head_sha") or ""
    if not ctx.git("cat-file", "-e", f"{head_sha}^{{commit}}", check=False).returncode == 0 \
            or not ctx.is_ancestor(head_sha, ctx.head()):
        return f"head_sha {head_sha} is not HEAD or an ancestor of it"
    if d["agent"] in NON_COMMITTING and head_sha != d["base_sha"]:
        return f"{d['agent']} does not commit, but head_sha differs from the dispatch's base_sha"
    if d["agent"] == "forge-council" and env.get("status") == "COUNCIL_RULING":
        ruling = env.get("ruling") or {}
        routes = [ruling.get("route_to") or []] + [o.get("route_to") or [] for o in ruling.get("options") or []]
        if any(a not in BUILDERS for route in routes for a in route):
            return f"ruling routes to an agent outside {BUILDERS}"
    return None


def record_commits(ctx, env):
    """Mark the spoke's own commits as accounted for; anything else in or after its range is drift."""
    d = ctx.lc["dispatched"]
    head_sha, head = env["head_sha"], ctx.head()
    if head_sha != d["base_sha"]:
        for sha in ctx.git("rev-list", "--first-parent", f"{d['base_sha']}..{head_sha}").stdout.split():
            agent = ctx.git("show", "-s", "--format=%(trailers:key=Forge-Agent,valueonly)", sha).stdout.strip()
            (ctx.accounted.add if agent == d["agent"] else ctx.extra_drift.append)(sha)
    if head_sha != head:
        ctx.extra_drift += ctx.git("rev-list", f"{head_sha}..{head}").stdout.split()


def record_scope(ctx, env):
    scope = env.get("scope") or {}
    for key in ("completed", "remaining"):
        if key in scope:
            ctx.state["scope_manifest"][key] = scope[key]


# @spec ROUTE-ENV-006, ROUTE-ENV-007, ROUTE-ENV-008, ROUTE-ENV-009, ROUTE-ENV-010, ROUTE-BUILD-002
# @spec ROUTE-BUILD-004, ROUTE-BUILD-005, ROUTE-BUILD-007, ROUTE-BUILD-008, ROUTE-BUILD-009, ROUTE-BUILD-010
# @spec ROUTE-BUILD-011, ROUTE-BUILD-012, ROUTE-BUILD-013, ROUTE-BUILD-014, ROUTE-BUILD-015, ROUTE-BUILD-016
# @spec ROUTE-BUILD-017, ROUTE-BUILD-018, ROUTE-BUILD-019, ROUTE-BUILD-020, ROUTE-BUILD-021, ROUTE-BUILD-023
# @spec ROUTE-BUILD-024, ROUTE-BUILD-026, ROUTE-BUILD-027, ROUTE-BUILD-029, ROUTE-BUILD-030, ROUTE-BUILD-031
# @spec ROUTE-BUILD-032, ROUTE-PR-002, ROUTE-PR-003, ROUTE-BLK-005, ROUTE-BLK-006, ROUTE-BLK-016, ROUTE-HC-001
# @spec ROUTE-BLK-018, ROUTE-BLK-024, ROUTE-BUILD-034, ROUTE-BUILD-035, ROUTE-ENV-013
def apply_result(ctx, env):
    problem = validate_envelope(ctx, env)
    if problem:
        return stop("ENVELOPE_INVALID", problem, write=False)
    d = ctx.lc["dispatched"]
    phase, status, agent = ctx.lc["current_phase"], env["status"], d["agent"]
    ctx.lc["last_agent"], ctx.lc["last_status"] = agent, status
    escalation = env.get("escalation") or {}
    if status == "ERROR":
        ctx.lc["dispatched"] = None
        return stop("AGENT_ERROR", escalation.get("details", ""))
    record_commits(ctx, env)
    ctx.lc["dispatched"] = None
    if status in ("PUBLISHED", "PR_REVIEW_READY", "REPORT_DRAFTED"):
        ctx.lc["report_input"] = str(ctx.sd / "envelopes" / f"{d['dispatch_id']}.json")
    if agent in BUILDERS:
        ctx.anchors["last_known_head_sha"] = env["head_sha"]
    pass_no = ctx.lc.get("pass")

    if status in BLOCKER_KINDS:
        return raise_blocker(ctx, status, agent, phase, escalation.get("details"), escalation.get("findings"))
    if status == "HUMAN_CODE_CHANGE_REQUESTED":
        payload = {"phase": phase, "agent": agent, "paths": escalation.get("paths", []),
                   "ranges": escalation.get("ranges", {}), "base_sha": env["head_sha"],
                   "details": escalation.get("details")}
        return pause("human_code_approval", phase, payload,
                     question=f"{agent} asks to change human-authored lines: "
                     f"{json.dumps(payload['ranges'] or payload['paths'])}. {escalation.get('details', '')}")
    if phase == "RECON":
        return goto(first_planning_phase(ctx), context_refs=[str(ctx.sd / "scout")])
    if phase == "PR_FETCH":
        return goto("PR_REVIEW", context_refs=[str(ctx.sd / "scout")])
    if status == "STAGE_COMPLETE":
        nxt = f"PLAN_S{int(phase[-1]) + 1}"
        return pause("plan_approval", phase, {"next": nxt}) if ctx.mode in STAGE_PAUSE_MODES else goto(nxt)
    if status == "READY_FOR_QA":
        ctx.state["scope_manifest"]["assigned"] = (env.get("scope") or {}).get("assigned", [])
        if ctx.mode == "delivery":  # choosing delivery approved the plan in advance
            approve_plan(ctx)
            return goto("POST_PLAN")
        return pause("plan_approval", phase, {"next": "POST_PLAN", "set_plan_base": True})
    if phase == "POST_PLAN":
        if not ctx.state["scope_manifest"].get("assigned"):
            return goto("VERIFY_FINAL")
        if ctx.lc.get("two_pass") is None:
            ctx.lc["two_pass"] = ctx.mode in TWO_PASS_MODES
        if ctx.lc["two_pass"]:
            ctx.lc["pass"] = 1
            return goto("PRE_QA")
        return goto("QA_FULL")
    if phase == "PRE_QA":
        record_scope(ctx, env)
        if status == "PARTIAL_REMAINING":
            return goto("QA_SKEL" if pass_no == 1 else "QA_FULL")
        return goto("PRE_DEV")
    if phase == "PRE_DEV":
        record_scope(ctx, env)
        if status == "PARTIAL_REMAINING":
            return goto("DEV_STUB" if pass_no == 1 else "DEV_FULL")
        if pass_no == 1:
            ctx.lc["pass"] = 2
            return goto("PRE_QA")
        return goto("VERIFY_FINAL")
    if status == "SKELETON_COMMITTED":
        if ctx.mode in TWO_PASS_MODES:
            return pause("edit_window", phase, {"next": "PRE_DEV"})
        return goto("PRE_DEV")
    if status == "STUBS_COMMITTED":
        if ctx.mode in TWO_PASS_MODES:
            return pause("edit_window", phase, {"next": "PRE_QA", "set_pass": 2})
        ctx.lc["pass"] = 2
        return goto("PRE_QA")
    if status == "TESTS_COMMITTED":
        return goto("POST_QA")
    if phase == "POST_QA":
        nxt = "PRE_DEV" if ctx.lc.get("two_pass") else "DEV_FULL"
        return pause("edit_window", phase, {"next": nxt}) if ctx.mode in POST_QA_WINDOW_MODES else goto(nxt)
    if status == "IMPLEMENTATION_COMPLETE":
        if ctx.mode in DEV_FULL_WINDOW_MODES:
            return pause("edit_window", phase, {"next": "VERIFY_FINAL"})
        return goto("VERIFY_FINAL")
    if phase == "VERIFY_FINAL":
        base = ctx.anchors.get("workstream_base_sha")
        gate = verify_gate.check_commits(f"{base}..HEAD" if base else "HEAD", ctx.state, ctx.root)
        if not gate["ok"]:
            return gate_failed(ctx, phase, [f for f in gate["findings"] if f["severity"] == "error"])
        ctx.anchors["verifier_pass_sha"] = ctx.head()
        return goto("REVIEW_INTERNAL")
    if status == "REVIEW_APPROVED":
        ctx.anchors["reviewer_pass_sha"] = ctx.head()
        return publish_approval(ctx, phase)
    if status == "PUBLISHED":
        return goto("REPORT", mode="draft")
    if status == "MERGE_READY":
        return pause("manual_merge", phase)
    if status in ("MERGE_BLOCKED", "PUBLISH_REFUSED"):
        return stop(status, escalation.get("details") or env.get("summary", ""))
    if status == "CI_GREEN":
        return close()
    if status == "CI_NONE":
        return pause("ci_none", phase)
    if status == "REPORT_DRAFTED":
        return pause("report_approval", phase, context_refs=[ctx.lc["report_input"]])
    if status == "REPORT_SENT":
        return report_done(ctx)
    if phase == "RECONCILE":
        record_scope(ctx, env)
        target, ctx.lc["after_reconcile"] = ctx.lc.get("after_reconcile") or "VERIFY_FINAL", None
        return goto(target)
    if status == "COUNCIL_RULING":
        blocker = ctx.state["blockers"][-1]
        blocker["ruling"] = env.get("ruling") or {}
        ruling = blocker["ruling"]
        if not auto_rules(ctx, blocker):
            return pause("council_signoff", phase, {"blocker_id": blocker["id"]},
                         context_refs=[ruling["matrix_ref"]] if ruling.get("matrix_ref") else [])
        return auto_apply(ctx, blocker)
    if status == "REPAIR_COMMITTED":
        sm = ctx.state["scope_manifest"]
        if agent == "forge-planner" and "assigned" in (env.get("scope") or {}):
            sm["assigned"] = env["scope"]["assigned"]
            sm["completed"] = [p for p in sm["completed"] if p in sm["assigned"]]
            sm["remaining"] = [p for p in sm["assigned"] if p not in sm["completed"]]
            ctx.lc["resume_phase"] = "POST_PLAN"
        item = ctx.lc["repair_queue"].pop(0)
        if not any(i.get("blocker_id") == item.get("blocker_id") for i in ctx.lc["repair_queue"]):
            remove_blocker(ctx, item.get("blocker_id"))
        if ctx.lc["repair_queue"]:
            return goto("REPAIR", new_repair_item=True)
        return goto(ctx.lc.get("resume_phase") or "VERIFY_FINAL")
    if status == "PR_REVIEW_READY":
        return goto("REPORT", mode="draft")
    raise ValueError(f"no transition for {status} in {phase}")


def auto_rules(ctx, blocker):
    """Whether delivery applies this blocker's stored ruling without asking."""
    ruling = blocker.get("ruling") or {}
    return ctx.mode == "delivery" and blocker["kind"] != "CONCERNS" and ruling.get("requires_user") is False


def auto_apply(ctx, blocker):
    ruling = blocker["ruling"]
    option = {"label": ruling.get("recommended"), "route_to": ruling.get("route_to"), "delta": ruling.get("delta")}
    return apply_ruling(ctx, blocker, ruling, option, "auto")


# @spec ROUTE-BUILD-005, ROUTE-BUILD-041
def approve_plan(ctx):
    ctx.anchors["plan_base_sha"] = ctx.head()
    sm = ctx.state["scope_manifest"]
    sm["remaining"], sm["completed"] = list(sm["assigned"]), []


# @spec ROUTE-BUILD-020, ROUTE-BUILD-038
def gate_failed(ctx, phase, errors):
    if any(f["kind"] != "HUMAN_CODE_MODIFIED" for f in errors):
        return stop("GATE_UNREPAIRABLE", json.dumps(errors))
    shown = "; ".join(f"commit {f['commit']} {f['path']} lines {json.dumps(f.get('lines', []))}" for f in errors)
    return pause("human_code_approval", phase, {"gate_findings": errors},
                 question=f"Committed changes modify human-authored lines without approval: {shown}. "
                 "Approve them, or reject to stop?")


# @spec ROUTE-BUILD-039, ROUTE-BUILD-040, ROUTE-BUILD-042
def publish_approval(ctx, phase):
    result = lid_check.check(ctx.state) if ctx.lid else {}
    payload, question = {}, PAUSES["publish_approval"][1]
    for key, line in (("merged_specs", "Merged specs nobody in this workstream implements: "),
                      ("unmarked_specs", "Specs tested and tagged but still marked [ ]: ")):
        if result.get(key):
            payload[key] = result[key]
            question += "\n" + line + ", ".join(result[key])
    if not payload:
        return pause("publish_approval", phase)
    return pause("publish_approval", phase, payload, question=question)


# @spec ROUTE-BUILD-028, ROUTE-PR-004
def report_done(ctx):
    if ctx.state["identity"].get("flow") == "pr_review":
        return close()
    return goto("CI_WATCH", mode="CI_WATCH")


# --------------------------------------------------------------- user events


# @spec ROUTE-PAUSE-002, ROUTE-BUILD-006, ROUTE-BUILD-022, ROUTE-BUILD-025, ROUTE-BLK-007, ROUTE-BLK-008
# @spec ROUTE-HC-002, ROUTE-HC-003, ROUTE-DRIFT-006, ROUTE-LIMIT-005, ROUTE-DISP-008, ROUTE-BUILD-005
# @spec ROUTE-BUILD-036, ROUTE-BUILD-037, ROUTE-BLK-022
def apply_user(ctx, event):
    awaiting = ctx.lc.get("awaiting")
    if not awaiting or event.get("awaiting") != awaiting["kind"]:
        return stop("EVENT_INVALID", "the event does not answer the pending pause", write=False)
    kind, payload = awaiting["kind"], awaiting.get("payload") or {}
    decision, notes, option = event.get("decision"), event.get("notes"), event.get("option")
    if decision not in PAUSES[kind][0]:
        return stop("EVENT_INVALID", f"{decision} is not an option of {kind}", write=False)
    if kind == "publish_approval" and decision == "approve" and option not in ("push", "merge"):
        return stop("EVENT_INVALID", "approve needs option push or merge", write=False)
    phase = ctx.lc["current_phase"]
    if kind == "council_signoff" and decision == "approve":
        blocker = next(b for b in ctx.state["blockers"] if b["id"] == payload["blocker_id"])
        ruling = blocker.get("ruling") or {}
        chosen = next((o for o in ruling.get("options") or [] if o.get("label") == option), None)
        if chosen is None:
            return stop("EVENT_INVALID", f"{option} is not an option of the ruling", write=False)
        ctx.lc["awaiting"] = None
        return apply_ruling(ctx, blocker, ruling, chosen, "user")
    ctx.lc["awaiting"] = None
    user = {"user_caused": True}
    prior_refs = list((ctx.lc.get("invoke") or {}).get("context_refs", []))

    if kind == "plan_approval":
        if decision == "revise":
            replaced = (ctx.lc.get("invoke") or {}).get("mode")
            return goto(phase, notes=notes, context_refs=prior_refs, agent_mode=replaced, **user)
        if payload.get("set_plan_base"):
            approve_plan(ctx)
        return goto(payload["next"], **user)
    if kind == "edit_window":
        if payload.get("set_pass"):
            ctx.lc["pass"] = payload["set_pass"]
        return goto(payload["next"], **user)
    if kind == "publish_approval":
        if decision == "hold":
            return pause("publish_approval", phase, {k: v for k, v in payload.items() if not k.startswith("_")},
                         question=payload.get("_question"))
        return goto("PUBLISH", mode=option, **user)
    if kind == "manual_merge":
        base = ctx.state["identity"].get("base_branch")
        merged = ctx.git("rev-parse", "--verify", "--quiet", f"refs/heads/{base}", check=False).returncode == 0 \
            and ctx.is_ancestor(ctx.head(), f"refs/heads/{base}")
        if merged:
            return close()
        return pause("manual_merge", phase, question=f"HEAD is not yet merged into {base}. "
                     + PAUSES["manual_merge"][1])
    if kind == "report_approval":
        if decision == "skip":
            return report_done(ctx)
        return goto("REPORT", mode="send" if decision == "approve" else "draft", notes=notes, **user)
    if kind == "ci_none":
        return close() if decision == "approve" else goto("CI_WATCH", mode="CI_WATCH", **user)
    if kind == "council_signoff":  # reject; approve is applied above
        return goto("COUNCIL", notes=notes, **user)
    if kind == "findings_signoff":
        return goto("REPAIR", new_repair_item=True, **user)
    if kind == "human_code_approval" and "gate_findings" in payload:
        if decision == "reject":
            return stop("GATE_REJECTED", notes or "the user rejected the committed human-code changes")
        for f in payload["gate_findings"]:
            agent = ctx.git("show", "-s", "--format=%(trailers:key=Forge-Agent,valueonly)", f["commit"]).stdout
            ctx.state["approvals"].append({
                "id": f"apr-{uuid.uuid4().hex[:8]}", "agent": agent.strip(), "paths": [f["path"]],
                "ranges": {f["path"]: f.get("lines", [])},
                "base_sha": ctx.git("rev-parse", f"{f['commit']}^").stdout.strip(), "approved_by": "user"})
        return goto("VERIFY_FINAL", **user)
    if kind == "human_code_approval":
        if decision == "reject":
            added = notes
        else:
            approval = {"id": f"apr-{uuid.uuid4().hex[:8]}", "agent": payload["agent"], "paths": payload["paths"],
                        "ranges": payload["ranges"], "base_sha": payload["base_sha"], "approved_by": "user"}
            ctx.state["approvals"].append(approval)
            added = f"Approved human-code change {approval['id']}"
        new_notes = "\n".join(n for n in ((ctx.lc.get("invoke") or {}).get("notes"), added) if n)
        if payload["phase"] == "REPAIR":
            ctx.lc["repair_queue"][0]["notes"] = new_notes  # the REPAIR dispatch reads the queue head's notes
        return goto(payload["phase"], notes=new_notes, context_refs=prior_refs, **user)
    if kind == "dirty_tree":
        pending = payload["pending"]
        return goto(pending["phase"], **{**pending["opts"], **user})
    if kind == "attempt_limit":
        if decision == "stop":
            return stop("ATTEMPT_LIMIT", f"stopped at {phase}")
        if "blocker" in payload:
            b = payload["blocker"]
            ctx.lc["blocker_counts"][b["kind"]] = 0
            return raise_blocker(ctx, b["kind"], b["agent"], b["phase"], b["details"], b.get("findings"))
        pending = payload["pending"]
        ctx.lc["attempts"][pending["phase"]] = 0
        return goto(pending["phase"], **{**pending["opts"], **user})
    raise ValueError(f"unhandled pause {kind}")


# ------------------------------------------------------------------- routing


def unaccounted_drift(ctx):
    drift = ctx.report.get("drift") or {}
    new = [c["sha"] for c in drift.get("new_commits", []) if c["sha"] not in ctx.accounted]
    return new + list(drift.get("orphaned_commits", [])) + ctx.extra_drift


# @spec ROUTE-REDO-001, ROUTE-REDO-002, ROUTE-REDO-003, ROUTE-REDO-004, ROUTE-DRIFT-003, ROUTE-BUILD-001
# @spec ROUTE-BUILD-003, ROUTE-PR-001, ROUTE-DRIFT-008
def apply_event(ctx, event):
    if event is None:
        if ctx.lc.get("awaiting"):
            aw = ctx.lc["awaiting"]
            return {"kind": "repause", **pause_from_awaiting(ctx, aw)}
        if ctx.lc.get("dispatched"):
            if not unaccounted_drift(ctx):
                if ctx.report.get("dirty") and ctx.lc["dispatched"]["agent"] not in DIRTY_OK:
                    return goto(ctx.lc["current_phase"], redispatch=True)
                return {"kind": "dispatched", "phase": ctx.lc["current_phase"], "invoke": ctx.lc["invoke"],
                        "rederived": True}
            ctx.lc["dispatched"] = None  # drift while a spoke was out: account for it first
        phase = ctx.lc["current_phase"]
        if phase == "INIT":
            return stop("NOT_STARTED", "send a start event first", write=False)
        if phase == "DONE":
            return {"kind": "done"}
        queued = ctx.lc.pop("next_dispatch", None)
        if queued:  # what a pause answered by a mode switch leads to
            return goto(queued["phase"], **queued["opts"])
        return goto(phase)
    etype = event.get("type")
    if etype == "start":
        if ctx.lc["current_phase"] != "INIT":
            return stop("EVENT_INVALID", "the workstream has already started", write=False)
        if ctx.state["identity"].get("flow") == "pr_review":
            return goto("PR_FETCH")
        return goto("RECON" if ctx.state["identity"].get("issue") else first_planning_phase(ctx))
    if etype == "result":
        return apply_result(ctx, event.get("envelope") or {})
    if etype == "user":
        return apply_user(ctx, event)
    if etype == "mode":
        return apply_mode(ctx, event.get("mode"))
    return stop("EVENT_INVALID", f"unknown event type {etype}", write=False)


def lacks_pause(ctx, mode):
    """The answer to the pending pacing pause that mode would not have raised, or None."""
    aw = ctx.lc.get("awaiting") or {}
    kind, payload, phase = aw.get("kind"), aw.get("payload") or {}, ctx.lc.get("current_phase")
    if kind == "plan_approval":
        stage = str(payload.get("next", "")).startswith("PLAN_S")
        if (stage and mode not in STAGE_PAUSE_MODES) or (payload.get("set_plan_base") and mode == "delivery"):
            return "approve"
    if kind == "edit_window":
        keeps = {"QA_SKEL": TWO_PASS_MODES, "DEV_STUB": TWO_PASS_MODES, "POST_QA": POST_QA_WINDOW_MODES,
                 "DEV_FULL": DEV_FULL_WINDOW_MODES}.get(phase)
        if keeps is not None and mode not in keeps:
            return "continue"
    return None


# @spec ROUTE-MODE-001, ROUTE-MODE-002, ROUTE-MODE-003, ROUTE-MODE-004, ROUTE-MODE-005, ROUTE-MODE-006
# @spec ROUTE-MODE-009
def apply_mode(ctx, mode):
    if mode not in MODES or ctx.lc.get("current_phase") == "DONE":
        return stop("EVENT_INVALID", f"mode {mode!r} is not one of {', '.join(MODES)}"
                    if mode not in MODES else "the workstream is done", write=False)
    ctx.state["config"]["mode"] = mode
    aw = ctx.lc.get("awaiting") or {}
    answer = lacks_pause(ctx, mode)
    outcome = None
    if answer:
        outcome = apply_user(ctx, {"type": "user", "awaiting": aw["kind"], "decision": answer})
    elif aw.get("kind") == "council_signoff":
        blocker = next((b for b in ctx.state["blockers"] if b["id"] == (aw.get("payload") or {}).get("blocker_id")),
                       None)
        if blocker and auto_rules(ctx, blocker):
            ctx.lc["awaiting"] = None
            outcome = auto_apply(ctx, blocker)
    if outcome and outcome["kind"] == "dispatch":  # dispatched on the next call, which carries no event
        ctx.lc["next_dispatch"] = {"phase": outcome["phase"], "opts": outcome["opts"]}
    return {"kind": "mode_set", "mode": mode}


def pause_from_awaiting(ctx, aw):
    payload = aw.get("payload") or {}
    return {"awaiting": aw["kind"], "phase": ctx.lc["current_phase"], "payload": payload,
            "question": payload.get("_question"), "context_refs": payload.get("_context_refs", [])}


# @spec ROUTE-DRIFT-001, ROUTE-DRIFT-002, ROUTE-DRIFT-004, ROUTE-DRIFT-007, ROUTE-DRIFT-009, ROUTE-DRIFT-010
def handle_drift(ctx, outcome):
    if outcome["kind"] not in ("dispatch", "close"):
        return outcome
    if not unaccounted_drift(ctx):
        return outcome
    if ctx.state["identity"].get("flow") == "pr_review":
        return stop("REVIEW_WORKTREE_MOVED", "commits appeared in the pull request review worktree", write=False)
    ctx.anchors["verifier_pass_sha"] = None
    ctx.anchors["reviewer_pass_sha"] = None
    ctx.anchors["last_known_head_sha"] = ctx.head()
    target = outcome.get("phase", "VERIFY_FINAL")
    if outcome["kind"] == "dispatch" and target in VERIFIER_PHASES:
        return outcome
    ctx.lc["after_reconcile"] = "VERIFY_FINAL" if target in ("REPORT", "CI_WATCH") else target
    return goto("RECONCILE")


# @spec ROUTE-KILL-001
def kill_switch(cwd):
    """The KILL_SWITCH stop when .STOP_FORGE sits at the main worktree's root or this worktree's, else None."""
    def out(*args):
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    roots = [out("worktree", "list", "--porcelain").split("\n", 1)[0].removeprefix("worktree "),
             out("rev-parse", "--show-toplevel")]
    found = []
    for root in roots:
        path = str(Path(root) / ".STOP_FORGE") if root else ""
        if path and path not in found and Path(path).is_file():
            found.append(path)
    if not found:
        return None
    return {"action": "stop", "phase": None, "status": "KILL_SWITCH", "detail": ", ".join(found),
            "summary": "Stopped: the kill switch is on"}


# @spec ROUTE-CLI-001, ROUTE-CLI-002, ROUTE-CLI-004, ROUTE-CLI-006, ROUTE-CLI-007, ROUTE-CLI-008, ROUTE-PAUSE-001
# @spec ROUTE-ENV-011, ROUTE-MODE-007, ROUTE-MODE-010
def route(cwd, workstream, pid, session, event, dry_run=False):
    killed = kill_switch(cwd)
    if killed:
        return killed
    report = resolve_state.wake(cwd, workstream, pid, session)
    if report["status"] != "OK":
        return {"action": "stop", "phase": None, "status": report["status"],
                "detail": report.get("detail", ""), "summary": f"wake returned {report['status']}"}
    state, _ = resolve_state.load_state(report["state_path"])
    config = state.get("config") or {}
    if "mode" in config and config["mode"] not in MODES:
        return {"action": "stop", "phase": state["lifecycle"].get("current_phase"), "status": "STATE_INVALID",
                "detail": f"config.mode {config['mode']!r} is not one of {', '.join(MODES)}",
                "summary": "Stopped: STATE_INVALID"}
    ctx = Ctx(cwd, report, state, dry_run)
    before = json.dumps(state, sort_keys=True)

    outcome = apply_event(ctx, event)
    if outcome["kind"] == "pause" and ctx.extra_drift:
        # Foreign commits inside the envelope's range: keep the anchor so the next call sees them.
        ctx.anchors["last_known_head_sha"] = json.loads(before)["anchors"].get("last_known_head_sha")
    outcome = handle_drift(ctx, outcome)
    if outcome["kind"] == "dispatch":
        outcome = dispatch(ctx, outcome["phase"], outcome["opts"])

    kind = outcome["kind"]
    if kind == "stop":
        decision = {"action": "stop", "phase": ctx.lc.get("current_phase"), "status": outcome["status"],
                    "detail": outcome["detail"], "summary": f"Stopped: {outcome['status']}"}
        if not outcome["write"]:
            return decision
    elif kind in ("pause", "repause"):
        options, default_q = PAUSES[outcome["awaiting"]]
        question = outcome.get("question") or default_q
        if kind == "pause":
            payload = {**outcome["payload"], "_question": question, "_context_refs": outcome["context_refs"]}
            ctx.lc["awaiting"] = {"kind": outcome["awaiting"], "payload": payload}
            ctx.lc["current_phase"] = outcome["phase"]
        decision = {"action": "pause", "phase": ctx.lc["current_phase"], "awaiting": outcome["awaiting"],
                    "question": question, "options": options, "context_refs": outcome["context_refs"],
                    "summary": f"Waiting for the user: {outcome['awaiting']}"}
    elif kind == "dispatched":
        decision = {"action": "dispatch", "phase": outcome["phase"], "invoke": outcome["invoke"],
                    "summary": f"Dispatch {outcome['invoke']['agent']} ({outcome['invoke']['mode']}) "
                               f"for {outcome['phase']}"}
    elif kind == "mode_set":
        decision = {"action": "mode_set", "phase": ctx.lc["current_phase"], "mode": outcome["mode"],
                    "summary": f"Mode set to {outcome['mode']}"}
    elif kind == "close":
        ctx.lc["current_phase"] = "DONE"
        decision = {"action": "close", "phase": "DONE", "summary": "Workstream finished; close it"}
    else:
        decision = {"action": "done", "phase": "DONE", "summary": "Workstream already closed"}
    if not dry_run and json.dumps(state, sort_keys=True) != before:
        resolve_state.write_state(report["state_path"], state)
    return decision


def read_event(arg):
    if arg is None:
        return None
    text = sys.stdin.read() if arg == "-" else Path(arg).read_text()
    event = json.loads(text)
    if not isinstance(event, dict):
        raise ValueError("the event must be a JSON object")
    return event


# @spec ROUTE-CLI-003, ROUTE-CLI-005
def main(argv=None):
    parser = argparse.ArgumentParser(prog="next_step.py")
    parser.add_argument("--holder-pid", required=True, type=int)
    parser.add_argument("--holder-session", required=True)
    parser.add_argument("--workstream")
    parser.add_argument("--event")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    killed = kill_switch(os.getcwd())
    if killed:  # before the wake and before reading the event
        print(json.dumps(killed))
        return 0
    try:
        event = read_event(args.event)
        decision = route(os.getcwd(), args.workstream, args.holder_pid, args.holder_session, event, args.dry_run)
    except (ValueError, OSError) as e:
        print(f"next_step.py: {e}", file=sys.stderr)
        return 2
    except (resolve_state.UsageError, resolve_state.GitError, verify_gate.GitError) as e:
        print(f"next_step.py: {e}", file=sys.stderr)
        return 2
    print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
