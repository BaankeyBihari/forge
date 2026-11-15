"""Tests for next_step.py (docs/intent/router/router-specs.md).

Each test starts from a workstream created by resolve_state.py `new` (naive repo,
config defaults: plan_mode strict, collaborative true) and drives the router through
its CLI, editing state.json directly to place the workstream in a phase.

ASSUMPTIONS about shapes the specs leave open:
- events: {"type": "start"}, {"type": "result", "envelope": {...}},
  {"type": "user", "awaiting", "decision", "option"?, "notes"?}
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (
    commit,
    forge_trailers,
    git,
    head,
    holder_args,
    read_state,
    run_script,
    write_state,
)

PHASE_AGENT = {
    "RECON": ("forge-scout", "PRE_PLAN"),
    "POST_PLAN": ("forge-verifier", "POST_PLAN"),
    "PRE_QA": ("forge-verifier", "PRE_QA"),
    "QA_SKEL": ("forge-qa", "skeleton"),
    "PRE_DEV": ("forge-verifier", "PRE_DEV"),
    "DEV_STUB": ("forge-dev", "stub"),
    "QA_FULL": ("forge-qa", "full"),
    "POST_QA": ("forge-verifier", "POST_QA"),
    "DEV_FULL": ("forge-dev", "full"),
    "VERIFY_FINAL": ("forge-verifier", "VERIFY_FINAL"),
    "RECONCILE": ("forge-verifier", "RECONCILE"),
}


class Router:
    def __init__(self, ws):
        self.ws = ws
        self.wt = ws["worktree"]
        self.sp = ws["state_path"]
        self.sd = ws["state_path"].parent
        self.last = None

    # -- invocation
    def run(self, event=None, *extra, raw=False):
        args = ["--workstream", self.ws["slug"], *holder_args(), *extra]
        stdin = None
        if event is not None:
            args += ["--event", "-"]
            stdin = json.dumps(event)
        res = run_script("next_step.py", *args, cwd=self.wt, stdin=stdin)
        if raw:
            return res
        assert res.returncode == 0, res.stderr
        self.last = res.json
        return self.last

    def start(self):
        return self.run({"type": "start"})

    def result(self, status, head_sha=None, **fields):
        d = self.state()["lifecycle"]["dispatched"]
        envelope = {"dispatch_id": d["dispatch_id"], "agent": d["agent"], "status": status,
                    "head_sha": head_sha or head(self.wt), "files_touched": [], "summary": "done", **fields}
        return self.run({"type": "result", "envelope": envelope})

    def user(self, decision, option=None, notes=None, awaiting=None):
        event = {"type": "user", "awaiting": awaiting or self.state()["lifecycle"]["awaiting"]["kind"],
                 "decision": decision}
        if option is not None:
            event["option"] = option
        if notes is not None:
            event["notes"] = notes
        return self.run(event)

    # -- state
    def state(self):
        return read_state(self.sp)

    def patch(self, **sections):
        state = self.state()
        for key, value in sections.items():
            if isinstance(value, dict) and isinstance(state.get(key), dict):
                state[key].update(value)
            else:
                state[key] = value
        write_state(self.sp, state)

    def goto(self, phase, **lifecycle):
        """Place the workstream in phase with nothing outstanding, then let the router dispatch."""
        self.patch(lifecycle={"current_phase": phase, "dispatched": None, "awaiting": None, **lifecycle})
        return self.run()

    # -- spoke work
    def spoke_commit(self, files, message="spoke work"):
        inv = self.last["invoke"]
        return commit(self.wt, files, message,
                      forge_trailers(inv["agent"], inv["stage"], inv["mode"], inv["base_sha"]))

    def human_commit(self, files, message="human work"):
        return commit(self.wt, files, message)


@pytest.fixture
def r(workstream):
    return Router(workstream)


def dispatches(decision, agent, mode=None):
    assert decision["action"] == "dispatch", decision
    assert decision["invoke"]["agent"] == agent, decision
    if mode is not None:
        assert decision["invoke"]["mode"] == mode, decision
    return True


def pauses(decision, kind):
    assert decision["action"] == "pause", decision
    assert decision["awaiting"] == kind, decision
    return True


def stops(decision, status):
    assert decision["action"] == "stop", decision
    assert decision["status"] == status, decision
    return True


# -------------------------------------------------------------------- invocation


# @spec ROUTE-CLI-001, ROUTE-CLI-002
def test_wake_failure_stops_without_changes(r):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        lock = r.sd / "lock.json"
        data = json.loads(lock.read_text())
        data.update(holder_pid=proc.pid, holder_session="other")
        lock.write_text(json.dumps(data))
        before = r.sp.read_bytes()
        assert stops(r.start(), "LOCKED")
        assert r.sp.read_bytes() == before
    finally:
        proc.kill()
        proc.wait()


# @spec ROUTE-CLI-001
def test_wake_runs_before_event(r):
    r.sp.write_text("{broken")
    assert stops(r.start(), "STATE_INVALID")


# @spec ROUTE-CLI-003
def test_event_from_file(r, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"type": "start"}))
    res = run_script("next_step.py", "--workstream", "feat-demo", *holder_args(), "--event", event, cwd=r.wt)
    assert dispatches(res.json, "forge-planner", "naive")


# @spec ROUTE-CLI-003
def test_event_from_stdin(r):
    assert dispatches(r.start(), "forge-planner", "naive")


# @spec ROUTE-CLI-004
def test_dry_run_writes_nothing(r):
    before = r.sp.read_bytes()
    res = r.run({"type": "start"}, "--dry-run")
    assert dispatches(res, "forge-planner")
    assert r.sp.read_bytes() == before


# @spec ROUTE-CLI-005
def test_usage_error_exits_nonzero(r):
    assert run_script("next_step.py", "--bogus", cwd=r.wt).returncode != 0
    assert run_script("next_step.py", "--workstream", "feat-demo", cwd=r.wt).returncode != 0  # no holder
    ok = r.run({"type": "start"}, raw=True)
    assert ok.returncode == 0
    json.loads(ok.stdout)  # exactly one JSON document: json.loads rejects trailing data


# @spec ROUTE-CLI-006
def test_every_decision_has_action_phase_summary(r):
    for decision in [r.start(), r.result("READY_FOR_QA", scope={"assigned": ["src/**"]}), r.user("approve")]:
        assert {"action", "phase", "summary"} <= set(decision)
        assert isinstance(decision["summary"], str) and "\n" not in decision["summary"]


# @spec ROUTE-CLI-007
def test_router_leaves_resolve_state_fields_alone(r):
    before = r.state()
    r.start()
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    r.user("approve")
    after = r.state()
    for key in ["identity", "config"]:
        assert after[key] == before[key]
    for anchor in ["workstream_base_sha", "stale_shas"]:
        assert after["anchors"][anchor] == before["anchors"][anchor]


# @spec ROUTE-CLI-008
def test_event_then_drift_then_dirty(r):
    r.goto("DEV_FULL")
    dev = r.spoke_commit({"src/a.py": "a\n"})
    r.human_commit({"src/h.py": "h\n"})
    Path(r.wt, "scratch.txt").write_text("dirty\n")
    decision = r.result("IMPLEMENTATION_COMPLETE", head_sha=dev)
    state = r.state()
    assert state["lifecycle"]["last_status"] == "IMPLEMENTATION_COMPLETE"  # event applied
    assert state["anchors"]["last_known_head_sha"] == head(r.wt)  # drift handled
    assert pauses(decision, "dirty_tree")  # dirty check before dispatching VERIFY_FINAL
    assert decision["phase"] == "VERIFY_FINAL"


# ------------------------------------------------------------------ re-derivation


# @spec ROUTE-REDO-001
def test_pending_pause_is_returned_again(r):
    r.start()
    first = r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    again = r.run()
    assert pauses(again, "plan_approval") and again["question"] == first["question"]


# @spec ROUTE-REDO-002, ROUTE-DISP-005
def test_outstanding_dispatch_is_returned_again(r):
    r.start()
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    first = r.user("revise", notes="split the parser change")
    assert r.state()["lifecycle"]["invoke"] == first["invoke"]
    again = r.run()
    assert again["invoke"] == first["invoke"]


# @spec ROUTE-REDO-003
def test_no_event_at_init_is_not_started(r):
    before = r.sp.read_bytes()
    assert stops(r.run(), "NOT_STARTED")
    assert r.sp.read_bytes() == before


# @spec ROUTE-REDO-004
def test_no_event_at_done_is_done(r):
    r.patch(lifecycle={"current_phase": "DONE"})
    assert r.run()["action"] == "done"


# ----------------------------------------------------------------------- dispatch


# @spec ROUTE-DISP-001
@pytest.mark.parametrize("phase", sorted(PHASE_AGENT))
def test_phase_dispatch(r, phase):
    decision = r.goto(phase, **({"pass": 1} if phase in {"PRE_QA", "QA_SKEL", "PRE_DEV", "DEV_STUB"} else {}))
    agent, mode = PHASE_AGENT[phase]
    assert dispatches(decision, agent, mode)
    d = r.state()["lifecycle"]["dispatched"]
    assert d == {"dispatch_id": decision["invoke"]["dispatch_id"], "agent": agent, "mode": mode,
                 "base_sha": head(r.wt)}


# @spec ROUTE-DISP-001
def test_dispatch_ids_are_unique(r):
    first = r.start()["invoke"]["dispatch_id"]
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    second = r.user("approve")["invoke"]["dispatch_id"]
    assert first != second


# @spec ROUTE-DISP-002
def test_invocation_envelope_fields(r):
    inv = r.start()["invoke"]
    assert {"dispatch_id", "agent", "mode", "stage", "state_path", "base_sha", "assigned_scope",
            "context_refs", "notes"} <= set(inv)
    assert inv["state_path"] == str(r.sp)
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    verifier = r.user("approve")["invoke"]
    assert "stage" not in verifier


# @spec ROUTE-DISP-003
@pytest.mark.parametrize(
    "phase, repo_mode, stage",
    [("PLAN_S1", "LID", "LID-S1"), ("PLAN_S3", "LID", "LID-S3"), ("PLAN", "NAIVE", "NAIVE-PLAN"),
     ("QA_SKEL", "NAIVE", "COLLAB-SKEL"), ("DEV_STUB", "NAIVE", "COLLAB-STUB"),
     ("QA_FULL", "LID", "LID-S5"), ("QA_FULL", "NAIVE", "NAIVE-TESTS"),
     ("DEV_FULL", "LID", "LID-S6"), ("DEV_FULL", "NAIVE", "NAIVE-IMPL")],
)
def test_stage_values(r, phase, repo_mode, stage):
    state = r.state()
    state["config"]["repo_mode"] = repo_mode
    write_state(r.sp, state)
    assert r.goto(phase, **{"pass": 1})["invoke"]["stage"] == stage


# @spec ROUTE-DISP-003, ROUTE-BLK-015
def test_repair_stage(r):
    decision = r.goto("REPAIR", repair_queue=[{"agent": "forge-qa", "notes": "fix", "context_refs": ["x.md"]}],
                      resume_phase="VERIFY_FINAL")
    assert dispatches(decision, "forge-qa", "repair")
    assert decision["invoke"]["stage"] == "REPAIR"
    assert decision["invoke"]["notes"] == "fix" and "x.md" in decision["invoke"]["context_refs"]


# @spec ROUTE-DISP-004
@pytest.mark.parametrize("phase", ["QA_SKEL", "DEV_STUB", "QA_FULL", "DEV_FULL"])
def test_build_dispatch_scope_is_remaining(r, phase):
    r.patch(scope_manifest={"assigned": ["src/**", "lib/**"], "completed": ["lib/**"], "remaining": ["src/**"]})
    assert r.goto(phase, **{"pass": 1})["invoke"]["assigned_scope"] == ["src/**"]


# @spec ROUTE-DISP-006
def test_first_dispatch_sets_last_known_head(r):
    assert r.state()["anchors"]["last_known_head_sha"] is None
    r.start()
    assert r.state()["anchors"]["last_known_head_sha"] == head(r.wt)


# @spec ROUTE-DISP-007
@pytest.mark.parametrize("phase", ["POST_PLAN", "PRE_QA", "PRE_DEV", "POST_QA", "VERIFY_FINAL", "RECONCILE"])
def test_verifier_scope_is_assigned(r, phase):
    r.patch(scope_manifest={"assigned": ["src/**", "lib/**"], "completed": ["lib/**"], "remaining": ["src/**"]})
    assert r.goto(phase, **{"pass": 1})["invoke"]["assigned_scope"] == ["src/**", "lib/**"]


# ------------------------------------------------------------- result envelopes


def unchanged_stop(r, event_fn, status="ENVELOPE_INVALID"):
    before = r.sp.read_bytes()
    assert stops(event_fn(), status)
    assert r.sp.read_bytes() == before


# @spec ROUTE-ENV-001
def test_wrong_dispatch_id_or_agent(r):
    r.start()
    d = r.state()["lifecycle"]["dispatched"]
    base = {"dispatch_id": d["dispatch_id"], "agent": d["agent"], "status": "READY_FOR_QA",
            "head_sha": head(r.wt), "files_touched": [], "summary": "s"}
    unchanged_stop(r, lambda: r.run({"type": "result", "envelope": {**base, "dispatch_id": "nope"}}))
    unchanged_stop(r, lambda: r.run({"type": "result", "envelope": {**base, "agent": "forge-dev"}}))


# @spec ROUTE-ENV-002
def test_status_not_allowed_in_phase(r):
    r.start()
    unchanged_stop(r, lambda: r.result("TESTS_COMMITTED"))


# @spec ROUTE-ENV-003
def test_head_not_ancestor(r):
    r.goto("DEV_FULL")
    git(r.wt, "checkout", "-q", "-b", "elsewhere")
    other = commit(r.wt, {"x.txt": "x\n"}, "elsewhere")
    git(r.wt, "checkout", "-q", "feat/demo")
    unchanged_stop(r, lambda: r.result("IMPLEMENTATION_COMPLETE", head_sha=other))
    unchanged_stop(r, lambda: r.result("IMPLEMENTATION_COMPLETE", head_sha="0" * 40))


# @spec ROUTE-ENV-004
def test_non_committing_agent_must_not_move_head(r):
    r.goto("VERIFY_FINAL")
    r.human_commit({"x.txt": "x\n"})
    unchanged_stop(r, lambda: r.result("PASS"))


# @spec ROUTE-ENV-005
def test_council_route_outside_builders(r):
    r.goto("VERIFY_FINAL")
    r.result("TESTS_FAILED", escalation={"details": "red"})
    ruling = {"matrix_ref": "m.md", "requires_user": False, "route_to": ["forge-reviewer"], "delta": "d"}
    unchanged_stop(r, lambda: r.result("COUNCIL_RULING", ruling=ruling))


# @spec ROUTE-ENV-006
def test_accepted_envelope_bookkeeping(r):
    r.start()
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    lc = r.state()["lifecycle"]
    assert lc["dispatched"] is None
    assert (lc["last_agent"], lc["last_status"]) == ("forge-planner", "READY_FOR_QA")


# @spec ROUTE-ENV-007
def test_committing_agent_advances_last_known_head(r):
    r.goto("DEV_FULL")
    dev = r.spoke_commit({"src/a.py": "a\n"})
    r.result("IMPLEMENTATION_COMPLETE")
    assert r.state()["anchors"]["last_known_head_sha"] == dev


# @spec ROUTE-ENV-008
def test_foreign_commit_inside_envelope_range_is_drift(r):
    r.goto("DEV_FULL")
    r.patch(anchors={"verifier_pass_sha": head(r.wt)})
    r.spoke_commit({"src/a.py": "a\n"})
    r.human_commit({"src/h.py": "h\n"})
    dev = r.spoke_commit({"src/b.py": "b\n"})
    decision = r.result("IMPLEMENTATION_COMPLETE", head_sha=dev)
    assert dispatches(decision, "forge-verifier", "VERIFY_FINAL")
    assert r.state()["anchors"]["verifier_pass_sha"] is None


# @spec ROUTE-ENV-011
def test_foreign_commit_in_paused_envelope_keeps_anchor(r):
    r.goto("QA_SKEL", **{"pass": 1})
    anchor = r.state()["anchors"]["last_known_head_sha"]
    r.spoke_commit({"tests/test_a.py": "a\n"})
    r.human_commit({"tests/test_h.py": "h\n"})
    qa = r.spoke_commit({"tests/test_b.py": "b\n"})
    assert pauses(r.result("SKELETON_COMMITTED", head_sha=qa), "edit_window")
    assert r.state()["anchors"]["last_known_head_sha"] == anchor
    r.patch(anchors={"verifier_pass_sha": head(r.wt)})
    assert dispatches(r.user("continue"), "forge-verifier", "PRE_DEV")
    assert r.state()["anchors"]["verifier_pass_sha"] is None  # the human commit was handled as drift


# @spec ROUTE-ENV-009
def test_agent_error_stops(r):
    r.start()
    before = r.state()
    decision = r.result("ERROR", escalation={"details": "pytest not installed"})
    assert stops(decision, "AGENT_ERROR") and "pytest not installed" in decision["detail"]
    after = r.state()
    assert after["lifecycle"]["dispatched"] is None
    assert (after["lifecycle"]["last_agent"], after["lifecycle"]["last_status"]) == ("forge-planner", "ERROR")
    for key in ["anchors", "scope_manifest", "blockers", "approvals", "council_decisions"]:
        assert after[key] == before[key]
    assert after["lifecycle"]["current_phase"] == before["lifecycle"]["current_phase"]


# @spec ROUTE-ENV-010
def test_head_sha_ancestor_of_head_is_accepted(r):
    r.goto("DEV_FULL")
    dev = r.spoke_commit({"src/a.py": "a\n"})
    r.human_commit({"src/h.py": "user committed while dev ran\n"})
    decision = r.result("IMPLEMENTATION_COMPLETE", head_sha=dev)
    assert decision["action"] != "stop"
    state = r.state()
    assert state["lifecycle"]["last_status"] == "IMPLEMENTATION_COMPLETE"
    assert state["anchors"]["last_known_head_sha"] == head(r.wt)


# --------------------------------------------------------------------- build flow


def set_mode(r, **config):
    state = r.state()
    state["config"].update(config)
    write_state(r.sp, state)


# @spec ROUTE-BUILD-001
def test_start_with_issue_goes_to_recon(r):
    r.patch(identity={"issue": "https://github.com/acme/widgets/issues/1"})
    assert dispatches(r.start(), "forge-scout", "PRE_PLAN")
    assert r.state()["lifecycle"]["current_phase"] == "RECON"


# @spec ROUTE-BUILD-001, ROUTE-BUILD-003
@pytest.mark.parametrize("repo_mode, plan_mode, phase, mode",
                         [("LID", "strict", "PLAN_S1", "strict"), ("LID", "auto", "PLAN_S1", "auto"),
                          ("NAIVE", "strict", "PLAN", "naive"), ("NAIVE", "auto", "PLAN", "naive")])
def test_start_without_issue(r, repo_mode, plan_mode, phase, mode):
    set_mode(r, repo_mode=repo_mode, plan_mode=plan_mode)
    decision = r.start()
    assert dispatches(decision, "forge-planner", mode) and decision["phase"] == phase


# @spec ROUTE-BUILD-002
def test_recon_ok_goes_to_planning_with_brief(r):
    r.patch(identity={"issue": "https://github.com/acme/widgets/issues/1"})
    r.start()
    decision = r.result("OK")
    assert dispatches(decision, "forge-planner", "naive")
    assert str(r.sd / "scout") in decision["invoke"]["context_refs"]


# @spec ROUTE-BUILD-004
def test_strict_stage_complete(r):
    set_mode(r, repo_mode="LID")
    r.start()
    assert pauses(r.result("STAGE_COMPLETE"), "plan_approval")
    decision = r.user("approve")
    assert dispatches(decision, "forge-planner", "strict") and decision["phase"] == "PLAN_S2"
    assert decision["invoke"]["stage"] == "LID-S2"


# @spec ROUTE-BUILD-005
def test_ready_for_qa(r):
    r.start()
    assert pauses(r.result("READY_FOR_QA", scope={"assigned": ["src/**"]}), "plan_approval")
    assert r.state()["scope_manifest"]["assigned"] == ["src/**"]
    decision = r.user("approve")
    assert dispatches(decision, "forge-verifier", "POST_PLAN")
    assert r.state()["anchors"]["plan_base_sha"] == head(r.wt)


# @spec ROUTE-BUILD-006
def test_plan_revise(r):
    r.start()
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    decision = r.user("revise", notes="split the parser")
    assert dispatches(decision, "forge-planner", "naive") and decision["phase"] == "PLAN"
    assert "split the parser" in json.dumps(decision["invoke"]["notes"])


# @spec ROUTE-BUILD-007
@pytest.mark.parametrize("collaborative, phase, agent", [(True, "PRE_QA", "forge-verifier"),
                                                         (False, "QA_FULL", "forge-qa")])
def test_post_plan_pass(r, collaborative, phase, agent):
    set_mode(r, collaborative=collaborative)
    r.goto("POST_PLAN")
    decision = r.result("PASS")
    assert dispatches(decision, agent) and decision["phase"] == phase
    if collaborative:
        assert r.state()["lifecycle"]["pass"] == 1


# @spec ROUTE-BUILD-008
@pytest.mark.parametrize("phase", ["PRE_QA", "PRE_DEV"])
def test_verifier_scope_replaces_manifest(r, phase):
    r.patch(scope_manifest={"assigned": ["a/**", "b/**"], "completed": [], "remaining": ["a/**", "b/**"]})
    r.goto(phase, **{"pass": 1})
    r.result("PARTIAL_REMAINING", scope={"completed": ["a/**"], "remaining": ["b/**"]})
    sm = r.state()["scope_manifest"]
    assert (sm["completed"], sm["remaining"]) == (["a/**"], ["b/**"])


# @spec ROUTE-BUILD-009, ROUTE-BUILD-010
@pytest.mark.parametrize("pass_no, status, phase", [(1, "PARTIAL_REMAINING", "QA_SKEL"),
                                                    (2, "PARTIAL_REMAINING", "QA_FULL"),
                                                    (1, "NOTHING_REMAINING", "PRE_DEV"),
                                                    (2, "NOTHING_REMAINING", "PRE_DEV")])
def test_pre_qa(r, pass_no, status, phase):
    r.goto("PRE_QA", **{"pass": pass_no})
    assert r.result(status, scope={"completed": [], "remaining": ["src/**"]})["phase"] == phase


# @spec ROUTE-BUILD-011
def test_qa_skeleton_edit_window(r):
    r.goto("QA_SKEL", **{"pass": 1})
    r.spoke_commit({"tests/test_a.py": "def test_a(): ...\n"})
    assert pauses(r.result("SKELETON_COMMITTED"), "edit_window")
    assert r.user("continue")["phase"] == "PRE_DEV"


# @spec ROUTE-BUILD-012, ROUTE-BUILD-013
@pytest.mark.parametrize("pass_no, status, phase, new_pass", [(1, "PARTIAL_REMAINING", "DEV_STUB", 1),
                                                              (2, "PARTIAL_REMAINING", "DEV_FULL", 2),
                                                              (1, "NOTHING_REMAINING", "PRE_QA", 2),
                                                              (2, "NOTHING_REMAINING", "VERIFY_FINAL", 2)])
def test_pre_dev(r, pass_no, status, phase, new_pass):
    r.goto("PRE_DEV", **{"pass": pass_no})
    assert r.result(status, scope={"completed": [], "remaining": ["src/**"]})["phase"] == phase
    assert r.state()["lifecycle"]["pass"] == new_pass


# @spec ROUTE-BUILD-014
def test_dev_stubs_edit_window(r):
    r.goto("DEV_STUB", **{"pass": 1})
    r.spoke_commit({"src/a.py": "def a(): raise NotImplementedError\n"})
    assert pauses(r.result("STUBS_COMMITTED"), "edit_window")
    decision = r.user("continue")
    assert decision["phase"] == "PRE_QA" and r.state()["lifecycle"]["pass"] == 2


# @spec ROUTE-BUILD-015
def test_qa_full_to_post_qa(r):
    r.goto("QA_FULL")
    r.spoke_commit({"tests/test_a.py": "def test_a(): assert False\n"})
    assert dispatches(r.result("TESTS_COMMITTED"), "forge-verifier", "POST_QA")


# @spec ROUTE-BUILD-016
def test_post_qa_collaborative_pauses(r):
    r.goto("POST_QA")
    assert pauses(r.result("PASS"), "edit_window")
    assert r.user("continue")["phase"] == "PRE_DEV"


# @spec ROUTE-BUILD-032
def test_post_qa_sequential_goes_to_dev(r):
    set_mode(r, collaborative=False)
    r.goto("POST_QA")
    assert dispatches(r.result("PASS"), "forge-dev", "full")


# @spec ROUTE-BUILD-017
def test_dev_full_to_verify_final(r):
    r.goto("DEV_FULL")
    r.spoke_commit({"src/a.py": "a\n"})
    assert dispatches(r.result("IMPLEMENTATION_COMPLETE"), "forge-verifier", "VERIFY_FINAL")


# @spec ROUTE-BUILD-018, ROUTE-BUILD-019
def test_verify_final_gate_passes(r):
    r.goto("DEV_FULL")
    r.spoke_commit({"src/a.py": "a\n"})
    r.result("IMPLEMENTATION_COMPLETE")
    decision = r.result("PASS")
    assert dispatches(decision, "forge-reviewer", "internal")
    assert r.state()["anchors"]["verifier_pass_sha"] == head(r.wt)


# @spec ROUTE-BUILD-018, ROUTE-BUILD-020
def test_verify_final_gate_covers_planner_commits(r):
    r.start()
    r.spoke_commit({"src/stray.py": "planner wrote production code\n"})
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    r.user("approve")
    r.goto("VERIFY_FINAL")
    decision = r.result("PASS")
    state = r.state()
    assert state["anchors"]["verifier_pass_sha"] is None
    [blocker] = state["blockers"]
    assert blocker["kind"] == "AUDIT_FAILED" and "BOUNDARY_VIOLATION" in json.dumps(blocker["details"])
    assert blocker["agent"] == "commit-gate"
    assert dispatches(decision, "forge-council", "AUDIT_FAILED")


# @spec ROUTE-BUILD-021
def test_review_approved(r):
    r.patch(anchors={"verifier_pass_sha": head(r.wt)})
    r.goto("REVIEW_INTERNAL")
    decision = r.result("REVIEW_APPROVED")
    assert pauses(decision, "publish_approval") and set(decision["options"]) == {"approve", "hold"}
    assert r.state()["anchors"]["reviewer_pass_sha"] == head(r.wt)


def at_publish_approval(r):
    r.patch(anchors={"verifier_pass_sha": head(r.wt)})
    r.goto("REVIEW_INTERNAL")
    return r.result("REVIEW_APPROVED")


# @spec ROUTE-BUILD-022
@pytest.mark.parametrize("target", ["push", "merge"])
def test_publish_approval(r, target):
    at_publish_approval(r)
    assert dispatches(r.user("approve", option=target), "forge-publisher", target)


# @spec ROUTE-BUILD-022
def test_publish_hold(r):
    at_publish_approval(r)
    assert pauses(r.user("hold"), "publish_approval")


def at_publish(r, target):
    at_publish_approval(r)
    return r.user("approve", option=target)


# @spec ROUTE-BUILD-023
def test_published_goes_to_report(r):
    at_publish(r, "push")
    assert dispatches(r.result("PUBLISHED", publish_target="push"), "forge-reporter", "draft")


# @spec ROUTE-BUILD-024, ROUTE-BUILD-025
def test_merge_ready_and_manual_merge(r):
    r.human_commit({"f.txt": "feature\n"})
    r.patch(anchors={"last_known_head_sha": head(r.wt)})
    at_publish(r, "merge")
    assert pauses(r.result("MERGE_READY", publish_target="merge"), "manual_merge")
    not_yet = r.user("done")
    assert pauses(not_yet, "manual_merge")
    git(r.ws["repo"], "merge", "-q", "--ff-only", "feat/demo")
    assert r.user("done")["action"] == "close"


# @spec ROUTE-BUILD-026
@pytest.mark.parametrize("status", ["MERGE_BLOCKED", "PUBLISH_REFUSED"])
def test_publish_failures_stop(r, status):
    at_publish(r, "merge")
    decision = r.result(status, publish_target="merge", escalation={"details": "conflict in c.txt"})
    assert stops(decision, status) and "c.txt" in decision["detail"]


def at_report(r):
    at_publish(r, "push")
    return r.result("PUBLISHED", publish_target="push")


# @spec ROUTE-BUILD-027
def test_report_draft_approve_and_revise(r):
    at_report(r)
    assert pauses(r.result("REPORT_DRAFTED"), "report_approval")
    revised = r.user("revise", notes="shorter")
    assert dispatches(revised, "forge-reporter", "draft") and "shorter" in json.dumps(revised["invoke"]["notes"])
    r.result("REPORT_DRAFTED")
    assert dispatches(r.user("approve"), "forge-reporter", "send")


# @spec ROUTE-BUILD-028
def test_report_sent_goes_to_ci_watch(r):
    at_report(r)
    assert dispatches(r.result("REPORT_SENT"), "forge-scout", "CI_WATCH")


# @spec ROUTE-BUILD-028
def test_report_skip_goes_to_ci_watch(r):
    at_report(r)
    r.result("REPORT_DRAFTED")
    assert dispatches(r.user("skip"), "forge-scout", "CI_WATCH")


# @spec ROUTE-BUILD-029
def test_ci_green_closes(r):
    r.goto("CI_WATCH")
    assert r.result("CI_GREEN")["action"] == "close"


# @spec ROUTE-BUILD-030
def test_ci_none(r):
    r.goto("CI_WATCH")
    assert pauses(r.result("CI_NONE"), "ci_none")
    assert dispatches(r.user("wait"), "forge-scout", "CI_WATCH")
    r.result("CI_NONE")
    assert r.user("approve")["action"] == "close"


# @spec ROUTE-BUILD-031
def test_reconcile_pass_resumes(r):
    r.goto("RECONCILE", resume_phase="QA_FULL")
    decision = r.result("PASS", scope={"completed": ["a/**"], "remaining": ["src/**"]})
    assert dispatches(decision, "forge-qa", "full")
    assert r.state()["scope_manifest"]["remaining"] == ["src/**"]


# --------------------------------------------------------------- pull request flow


@pytest.fixture
def pr(r):
    r.patch(identity={"flow": "pr_review", "pr": "https://github.com/acme/widgets/pull/7"})
    return r


# @spec ROUTE-PR-001
def test_pr_start(pr):
    assert dispatches(pr.start(), "forge-scout", "PR_FETCH")


# @spec ROUTE-PR-002
def test_pr_fetch_ok(pr):
    pr.start()
    decision = pr.result("OK")
    assert dispatches(decision, "forge-reviewer", "external_pr")
    assert decision["invoke"]["context_refs"]


# @spec ROUTE-PR-003
def test_pr_review_ready(pr):
    pr.start()
    pr.result("OK")
    assert dispatches(pr.result("PR_REVIEW_READY"), "forge-reporter", "draft")


# @spec ROUTE-PR-004
def test_pr_report_closes(pr):
    pr.start()
    pr.result("OK")
    pr.result("PR_REVIEW_READY")
    pr.result("REPORT_DRAFTED")
    assert pr.user("skip")["action"] == "close"


# @spec ROUTE-PR-004
def test_pr_report_sent_closes(pr):
    pr.start()
    pr.result("OK")
    pr.result("PR_REVIEW_READY")
    pr.result("REPORT_DRAFTED")
    pr.user("approve")
    assert pr.result("REPORT_SENT")["action"] == "close"


# ------------------------------------------------------------------ entry guards


# @spec ROUTE-GUARD-001
def test_review_requires_verifier_pass(r):
    assert dispatches(r.goto("REVIEW_INTERNAL"), "forge-verifier", "VERIFY_FINAL")


# @spec ROUTE-GUARD-002
@pytest.mark.parametrize("only", ["verifier_pass_sha", "reviewer_pass_sha"])
def test_publish_requires_both_passes(r, only):
    r.patch(anchors={only: head(r.wt)})
    assert dispatches(r.goto("PUBLISH"), "forge-verifier", "VERIFY_FINAL")


# --------------------------------------------------------------- blockers/council


BLOCKERS = [
    # kind, phase, raising agent, resume phase, panel
    ("CONCERNS", "PLAN", "forge-planner", "PLAN", ["pm", "architect", "critic"]),
    ("TEST_CHANGE_REQUESTED", "DEV_FULL", "forge-dev", "DEV_FULL", ["senior-dev", "architect"]),
    ("CONTRACT_MISMATCH", "DEV_FULL", "forge-dev", "DEV_FULL", ["senior-dev", "architect"]),
    ("AUDIT_FAILED", "POST_QA", "forge-verifier", "POST_QA", ["architect", "senior-dev"]),
    ("TESTS_FAILED", "VERIFY_FINAL", "forge-verifier", "VERIFY_FINAL", ["debugger", "senior-dev"]),
    ("CI_FAILED", "CI_WATCH", "forge-scout", "VERIFY_FINAL", ["debugger", "senior-dev"]),
]


def mentions(invoke, text):
    """True when text appears in the invocation itself or in a file its context_refs name."""
    if text in json.dumps(invoke):
        return True
    return any(Path(ref).is_file() and text in Path(ref).read_text() for ref in invoke["context_refs"])


def raise_blocker(r, kind, phase):
    r.goto(phase)
    return r.result(kind, escalation={"details": f"{kind} details"})


# @spec ROUTE-BLK-001, ROUTE-BLK-002, ROUTE-BLK-003, ROUTE-BLK-004, ROUTE-LIMIT-003
@pytest.mark.parametrize("kind, phase, agent, resume, panel", BLOCKERS)
def test_blocker_goes_to_council(r, kind, phase, agent, resume, panel):
    decision = raise_blocker(r, kind, phase)
    state = r.state()
    [blocker] = state["blockers"]
    assert (blocker["kind"], blocker["agent"], blocker["phase"]) == (kind, agent, phase)
    assert {"id", "details"} <= set(blocker)
    assert state["lifecycle"]["resume_phase"] == resume
    assert state["lifecycle"]["blocker_counts"][kind] == 1
    assert dispatches(decision, "forge-council", kind)
    assert decision["invoke"]["panel"] == panel
    assert mentions(decision["invoke"], f"{kind} details")
    blocker_file = r.sd / "council" / f"{blocker['id']}.json"
    assert decision["invoke"]["context_refs"][0] == str(blocker_file)
    assert json.loads(blocker_file.read_text())["id"] == blocker["id"]


def ruling(route_to=(), requires_user=False):
    return {"matrix_ref": "council/matrix.md", "requires_user": requires_user, "route_to": list(route_to),
            "delta": "change the assertion in test_a"}


# @spec ROUTE-BLK-005
def test_concerns_ruling_always_needs_signoff(r):
    set_mode(r, council_auto_resolve=True)
    raise_blocker(r, "CONCERNS", "PLAN")
    assert pauses(r.result("COUNCIL_RULING", ruling=ruling()), "council_signoff")


# @spec ROUTE-BLK-006
@pytest.mark.parametrize("auto, requires_user, paused", [(True, False, False), (True, True, True),
                                                         (False, False, True)])
def test_auto_resolve(r, auto, requires_user, paused):
    set_mode(r, council_auto_resolve=auto)
    raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    decision = r.result("COUNCIL_RULING", ruling=ruling(["forge-dev"], requires_user))
    if paused:
        assert pauses(decision, "council_signoff")
    else:
        assert dispatches(decision, "forge-dev", "repair")


# @spec ROUTE-BLK-007, ROUTE-BLK-018
def test_council_signoff_approve(r):
    raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    r.result("COUNCIL_RULING", ruling=ruling(["forge-dev"]))
    blocker_id = r.state()["blockers"][0]["id"]
    assert r.state()["blockers"][0]["ruling"] == ruling(["forge-dev"])
    decision = r.user("approve", option="B")
    [entry] = r.state()["council_decisions"]
    assert (entry["blocker_id"], entry["option"], entry["approved_by"]) == (blocker_id, "B", "user")
    assert entry["panel"] == ["debugger", "senior-dev"] and "id" in entry
    assert dispatches(decision, "forge-dev", "repair")


# @spec ROUTE-BLK-008
def test_council_signoff_reject(r):
    raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    r.result("COUNCIL_RULING", ruling=ruling(["forge-dev"]))
    decision = r.user("reject", notes="consider flaky fixture")
    assert dispatches(decision, "forge-council", "TESTS_FAILED")
    assert "consider flaky fixture" in json.dumps(decision["invoke"]["notes"])


# @spec ROUTE-BLK-009, ROUTE-BLK-017
def test_concerns_ruling_redispatches_planning(r):
    raise_blocker(r, "CONCERNS", "PLAN")
    r.result("COUNCIL_RULING", ruling=ruling())
    decision = r.user("approve", option="A")
    assert dispatches(decision, "forge-planner", "naive") and decision["phase"] == "PLAN"
    assert "council/matrix.md" in json.dumps(decision["invoke"]["context_refs"])
    assert r.state()["blockers"] == []


# @spec ROUTE-BLK-010
@pytest.mark.parametrize("kind", ["TEST_CHANGE_REQUESTED", "CONTRACT_MISMATCH"])
def test_dev_blocker_ruling_queues_qa_repair(r, kind):
    set_mode(r, council_auto_resolve=True)
    raise_blocker(r, kind, "DEV_FULL")
    decision = r.result("COUNCIL_RULING", ruling=ruling(["forge-qa"]))
    assert dispatches(decision, "forge-qa", "repair") and decision["phase"] == "REPAIR"
    assert "change the assertion in test_a" in json.dumps(decision["invoke"])


# @spec ROUTE-BLK-011
@pytest.mark.parametrize("kind, phase", [("AUDIT_FAILED", "POST_QA"), ("TESTS_FAILED", "VERIFY_FINAL"),
                                         ("CI_FAILED", "CI_WATCH")])
def test_ruling_queues_route_to_in_order(r, kind, phase):
    set_mode(r, council_auto_resolve=True)
    raise_blocker(r, kind, phase)
    decision = r.result("COUNCIL_RULING", ruling=ruling(["forge-dev", "forge-qa"]))
    assert dispatches(decision, "forge-dev", "repair")
    assert [item["agent"] for item in r.state()["lifecycle"]["repair_queue"]] == ["forge-dev", "forge-qa"]


# @spec ROUTE-BLK-020
def test_repair_items_name_their_blocker(r):
    set_mode(r, council_auto_resolve=True)
    raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    blocker_id = r.state()["blockers"][0]["id"]
    r.result("COUNCIL_RULING", ruling=ruling(["forge-qa", "forge-dev"]))
    assert {item["blocker_id"] for item in r.state()["lifecycle"]["repair_queue"]} == {blocker_id}


# @spec ROUTE-BLK-019
@pytest.mark.parametrize("kind, phase", [("AUDIT_FAILED", "POST_QA"), ("TESTS_FAILED", "VERIFY_FINAL")])
def test_empty_route_resumes_failed_phase(r, kind, phase):
    set_mode(r, council_auto_resolve=True)
    raise_blocker(r, kind, phase)
    decision = r.result("COUNCIL_RULING", ruling=ruling([]))
    assert dispatches(decision, "forge-verifier", phase)
    assert r.state()["blockers"] == []


# @spec ROUTE-BLK-012
def test_flaky_ci_reruns(r):
    set_mode(r, council_auto_resolve=True)
    raise_blocker(r, "CI_FAILED", "CI_WATCH")
    decision = r.result("COUNCIL_RULING", ruling=ruling([]))
    assert dispatches(decision, "forge-scout", "CI_RERUN") and decision["phase"] == "CI_WATCH"


def review_findings(r, routes):
    r.patch(anchors={"verifier_pass_sha": head(r.wt)})
    r.goto("REVIEW_INTERNAL")
    findings = [{"route_to": agent, "detail": f"fix for {agent}"} for agent in routes]
    return r.result("CHANGES_REQUESTED", escalation={"details": "see findings", "findings": findings})


# @spec ROUTE-BLK-013
def test_review_findings_queue_repairs_without_council(r):
    decision = review_findings(r, ["forge-dev", "forge-qa", "forge-dev"])
    assert dispatches(decision, "forge-qa", "repair")
    queue = r.state()["lifecycle"]["repair_queue"]
    assert [item["agent"] for item in queue] == ["forge-qa", "forge-dev"]
    assert "fix for forge-dev" in json.dumps(queue[1])
    assert r.state()["lifecycle"]["resume_phase"] == "VERIFY_FINAL"


# @spec ROUTE-BLK-014
def test_planner_findings_need_signoff(r):
    decision = review_findings(r, ["forge-dev", "forge-planner"])
    assert pauses(decision, "findings_signoff") and "fix for forge-planner" in json.dumps(decision)
    after = r.user("approve")
    assert dispatches(after, "forge-planner", "repair") and after["phase"] == "REPAIR"
    assert [i["agent"] for i in r.state()["lifecycle"]["repair_queue"]] == ["forge-planner", "forge-dev"]


# @spec ROUTE-BLK-016, ROUTE-BLK-017
def test_repair_queue_drains_then_resumes(r):
    set_mode(r, council_auto_resolve=True)
    raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    r.result("COUNCIL_RULING", ruling=ruling(["forge-qa", "forge-dev"]))
    r.spoke_commit({"tests/test_a.py": "fixed\n"})
    second = r.result("REPAIR_COMMITTED")
    assert dispatches(second, "forge-dev", "repair")
    assert [i["agent"] for i in r.state()["lifecycle"]["repair_queue"]] == ["forge-dev"]
    r.spoke_commit({"src/a.py": "fixed\n"})
    resumed = r.result("REPAIR_COMMITTED")
    assert dispatches(resumed, "forge-verifier", "VERIFY_FINAL")
    assert r.state()["blockers"] == [] and r.state()["lifecycle"]["repair_queue"] == []


# @spec ROUTE-LIMIT-001
def test_long_repair_queue_does_not_hit_attempt_limit(r):
    queue = [{"agent": "forge-dev", "notes": f"fix {i}", "context_refs": []} for i in range(5)]
    decision = r.goto("REPAIR", repair_queue=queue, resume_phase="VERIFY_FINAL")
    for i in range(5):
        assert dispatches(decision, "forge-dev", "repair"), i
        assert r.state()["lifecycle"]["attempts"]["REPAIR"] == 1
        r.spoke_commit({f"src/fix{i}.py": "x\n"})
        decision = r.result("REPAIR_COMMITTED")
    assert dispatches(decision, "forge-verifier", "VERIFY_FINAL")


# ------------------------------------------------------------- human-code approval


def escalate_human_code(r):
    r.goto("DEV_FULL")
    first = r.spoke_commit({"src/new.py": "x\n"})
    decision = r.result("HUMAN_CODE_CHANGE_REQUESTED",
                        escalation={"details": "must edit legacy parser", "paths": ["src/legacy.py"],
                                    "ranges": {"src/legacy.py": [[10, 14]]}})
    return decision, first


# @spec ROUTE-HC-001
def test_human_code_request_pauses(r):
    decision, _ = escalate_human_code(r)
    assert pauses(decision, "human_code_approval")
    assert "src/legacy.py" in json.dumps(decision) and "14" in json.dumps(decision)


# @spec ROUTE-HC-002
def test_human_code_approve(r):
    _, first = escalate_human_code(r)
    decision = r.user("approve")
    [approval] = r.state()["approvals"]
    assert approval["agent"] == "forge-dev" and approval["paths"] == ["src/legacy.py"]
    assert approval["base_sha"] == first  # the escalating envelope's head_sha, not the dispatch base
    assert approval["approved_by"] == "user" and approval["ranges"]
    assert dispatches(decision, "forge-dev", "full") and decision["phase"] == "DEV_FULL"
    assert approval["id"] in json.dumps(decision["invoke"]["notes"])


# @spec ROUTE-HC-003
def test_human_code_reject(r):
    escalate_human_code(r)
    decision = r.user("reject", notes="work around it")
    assert r.state()["approvals"] == []
    assert dispatches(decision, "forge-dev", "full")
    assert "work around it" in json.dumps(decision["invoke"]["notes"])


# ----------------------------------------------------------------- drift and dirty


# @spec ROUTE-DRIFT-001, ROUTE-DRIFT-002, ROUTE-DRIFT-003
def test_human_commit_during_dispatch_reconciles(r):
    r.goto("DEV_FULL")
    r.patch(anchors={"verifier_pass_sha": head(r.wt), "reviewer_pass_sha": head(r.wt),
                     "last_known_head_sha": head(r.wt)})
    r.human_commit({"src/h.py": "h\n"})
    decision = r.run()
    assert dispatches(decision, "forge-verifier", "RECONCILE")
    state = r.state()
    assert state["lifecycle"]["resume_phase"] == "DEV_FULL"
    assert state["lifecycle"]["dispatched"]["agent"] == "forge-verifier"  # the old dev dispatch was cleared
    assert state["anchors"]["verifier_pass_sha"] is None and state["anchors"]["reviewer_pass_sha"] is None
    assert state["anchors"]["last_known_head_sha"] == head(r.wt)


# @spec ROUTE-DRIFT-001
def test_orphaned_forge_commits_without_envelope_reconcile(r):
    r.goto("DEV_FULL")
    r.patch(anchors={"last_known_head_sha": head(r.wt)})
    r.spoke_commit({"src/a.py": "a\n"})  # the session died before the envelope arrived
    assert dispatches(r.run(), "forge-verifier", "RECONCILE")


# @spec ROUTE-DRIFT-001
def test_behind_drift_reconciles(r):
    r.goto("DEV_FULL")
    dev = r.spoke_commit({"src/a.py": "a\n"})
    r.result("IMPLEMENTATION_COMPLETE")
    r.result("PASS")  # verifier_pass_sha = dev, now dispatched reviewer
    r.patch(lifecycle={"dispatched": None, "current_phase": "REVIEW_INTERNAL"})
    git(r.wt, "reset", "-q", "--hard", "HEAD~1")
    decision = r.run()
    assert dispatches(decision, "forge-verifier", "RECONCILE")
    assert r.state()["anchors"]["verifier_pass_sha"] is None
    assert dev in r.state()["anchors"]["stale_shas"]


# @spec ROUTE-DRIFT-002
def test_drift_before_verifier_phase_continues(r):
    r.patch(anchors={"last_known_head_sha": head(r.wt)}, lifecycle={"current_phase": "VERIFY_FINAL"})
    r.human_commit({"src/h.py": "h\n"})
    assert dispatches(r.run(), "forge-verifier", "VERIFY_FINAL")


# @spec ROUTE-DRIFT-007
def test_drift_at_close_reconciles(r):
    r.goto("CI_WATCH")
    r.human_commit({"src/late.py": "late\n"})
    decision = r.result("CI_GREEN", head_sha=r.state()["lifecycle"]["dispatched"]["base_sha"])
    assert dispatches(decision, "forge-verifier", "RECONCILE")
    assert r.state()["lifecycle"]["resume_phase"] == "VERIFY_FINAL"


# @spec ROUTE-DRIFT-004
def test_drift_waits_for_pause_answer(r):
    r.goto("QA_SKEL", **{"pass": 1})
    r.spoke_commit({"tests/test_a.py": "skeleton\n"})
    r.result("SKELETON_COMMITTED")
    r.human_commit({"tests/test_b.py": "the user's own test\n"})
    assert pauses(r.run(), "edit_window")
    decision = r.user("continue")
    assert dispatches(decision, "forge-verifier", "PRE_DEV")
    assert r.state()["anchors"]["last_known_head_sha"] == head(r.wt)


# @spec ROUTE-DRIFT-005
def test_dirty_tree_pauses_writers(r):
    Path(r.wt, "wip.txt").write_text("wip\n")
    decision = r.start()
    assert pauses(decision, "dirty_tree") and set(decision["options"]) == {"checkpoint", "stash"}


# @spec ROUTE-DRIFT-005
def test_dirty_tree_allows_scout(r):
    r.patch(identity={"issue": "https://github.com/acme/widgets/issues/1"})
    Path(r.wt, "wip.txt").write_text("wip\n")
    assert dispatches(r.start(), "forge-scout", "PRE_PLAN")


# @spec ROUTE-DRIFT-008, ROUTE-REDO-002
def test_outstanding_dispatch_on_dirty_tree_pauses(r):
    first = r.goto("DEV_FULL")
    Path(r.wt, "src").mkdir(exist_ok=True)
    Path(r.wt, "src", "half.py").write_text("half-written\n")
    decision = r.run()
    assert pauses(decision, "dirty_tree")
    assert "unfinished" in decision["question"]
    Path(r.wt, "src", "half.py").unlink()
    again = r.user("stash")
    assert dispatches(again, "forge-dev", "full")
    assert again["invoke"]["dispatch_id"] != first["invoke"]["dispatch_id"]


# @spec ROUTE-DRIFT-008
def test_outstanding_scout_dispatch_ignores_dirty_tree(r):
    first = r.goto("RECON")
    Path(r.wt, "wip.txt").write_text("wip\n")
    assert r.run()["invoke"] == first["invoke"]


# @spec ROUTE-DRIFT-006
def test_dirty_tree_answer_rechecks(r):
    wip = Path(r.wt, "wip.txt")
    wip.write_text("wip\n")
    r.start()
    assert pauses(r.user("stash"), "dirty_tree")  # still dirty
    wip.unlink()
    assert dispatches(r.user("stash"), "forge-planner")


# ------------------------------------------------------------------ attempt limits


# @spec ROUTE-LIMIT-001
def test_attempts_count_and_reset(r):
    r.start()
    assert r.state()["lifecycle"]["attempts"]["PLAN"] == 1
    r.patch(lifecycle={"dispatched": None, "attempts": {"PLAN": 2}})
    r.run()
    assert r.state()["lifecycle"]["attempts"]["PLAN"] == 3
    r.goto("POST_PLAN", attempts={"POST_PLAN": 1, "PRE_QA": 2})
    r.result("PASS")  # POST_PLAN -> PRE_QA: entering from another phase restarts at 1
    assert r.state()["lifecycle"]["attempts"]["PRE_QA"] == 1


# @spec ROUTE-LIMIT-002
def test_fourth_automatic_dispatch_pauses(r):
    decision = r.goto("VERIFY_FINAL", attempts={"VERIFY_FINAL": 3})
    assert pauses(decision, "attempt_limit") and set(decision["options"]) == {"retry", "stop"}


# @spec ROUTE-LIMIT-004
def test_fourth_blocker_of_a_kind_pauses(r):
    r.patch(lifecycle={"blocker_counts": {"TESTS_FAILED": 3}})
    decision = raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    assert pauses(decision, "attempt_limit")
    assert r.state()["lifecycle"]["current_phase"] != "COUNCIL"


# @spec ROUTE-LIMIT-005
def test_attempt_limit_retry_and_stop(r):
    r.goto("VERIFY_FINAL", attempts={"VERIFY_FINAL": 3})
    retry = r.user("retry")
    assert dispatches(retry, "forge-verifier", "VERIFY_FINAL")
    r.patch(lifecycle={"dispatched": None, "attempts": {"VERIFY_FINAL": 3}})
    r.run()
    assert stops(r.user("stop"), "ATTEMPT_LIMIT")


# @spec ROUTE-LIMIT-006
def test_user_caused_redispatch_restarts_count(r):
    r.start()
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    r.patch(lifecycle={"attempts": {"PLAN": 3}})
    decision = r.user("revise", notes="again")
    assert dispatches(decision, "forge-planner")
    assert r.state()["lifecycle"]["attempts"]["PLAN"] == 1


# ------------------------------------------------------------------------- pauses


# @spec ROUTE-PAUSE-001
def test_pause_recorded_and_returned(r):
    r.start()
    decision = r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    assert {"awaiting", "question", "options", "context_refs"} <= set(decision)
    assert set(decision["options"]) == {"approve", "revise"}
    awaiting = r.state()["lifecycle"]["awaiting"]
    assert awaiting["kind"] == "plan_approval" and "payload" in awaiting


# @spec ROUTE-PAUSE-002
def test_invalid_user_event(r):
    r.start()
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    unchanged_stop(r, lambda: r.user("approve", awaiting="edit_window"), "EVENT_INVALID")
    unchanged_stop(r, lambda: r.user("merge"), "EVENT_INVALID")
