"""Tests for next_step.py (docs/intent/router/router-specs.md).

Each test starts from a workstream created by resolve_state.py `new` (naive repo,
config.mode collab by default) and drives the router through its CLI, editing
state.json directly to place the workstream in a phase.

ASSUMPTIONS about shapes the specs leave open:
- events: {"type": "start"}, {"type": "result", "envelope": {...}},
  {"type": "user", "awaiting", "decision", "option"?, "notes"?}, {"type": "mode", "mode"}
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (
    SCRIPTS,
    commit,
    forge_trailers,
    git,
    head,
    holder_args,
    read_state,
    run_script,
    set_origin,
    write_guard_record,
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
        write_guard_record(self.sp, d)
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

    def mode(self, mode):
        return self.run({"type": "mode", "mode": mode})

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
    set_origin(workstream["repo"], "https://example.com/demo.git")  # publish_approval offers push only with an origin
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


# @spec ROUTE-CLI-001
def test_wake_runs_before_mode_event(r):
    r.sp.write_text("{broken")
    assert stops(r.mode("delivery"), "STATE_INVALID")


# @spec ROUTE-CLI-003
def test_mode_event_from_file(r, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"type": "mode", "mode": "delivery"}))
    res = run_script("next_step.py", "--workstream", "feat-demo", *holder_args(), "--event", event, cwd=r.wt)
    assert res.json["action"] == "mode_set", res.json
    assert r.state()["config"]["mode"] == "delivery"


# @spec ROUTE-CLI-007
def test_router_writes_only_config_mode(r):
    r.start()
    before = r.state()
    r.mode("collab-dev")
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    r.user("approve")
    after = r.state()
    assert after["identity"] == before["identity"]
    assert after["config"] == {**before["config"], "mode": "collab-dev"}
    for anchor in ["workstream_base_sha", "stale_shas"]:
        assert after["anchors"][anchor] == before["anchors"][anchor]
    assert "dropped" not in after


# ------------------------------------------------------------------- kill switch


def stop_file(root):
    """Create .STOP_FORGE at root; return its absolute path."""
    path = Path(root, ".STOP_FORGE")
    path.write_text("")
    return os.path.realpath(path)


def kill_detail(decision):
    detail = decision["detail"]
    return detail if isinstance(detail, str) else json.dumps(detail)


# @spec ROUTE-KILL-001
@pytest.mark.parametrize("where", ["main", "worktree", "both"])
def test_kill_switch_stops(r, where):
    r.start()
    roots = {"main": [r.ws["repo"]], "worktree": [r.wt], "both": [r.ws["repo"], r.wt]}[where]
    paths = [stop_file(root) for root in roots]
    state_before, lock_before = r.sp.read_bytes(), (r.sd / "lock.json").read_bytes()
    decision = r.run()
    assert stops(decision, "KILL_SWITCH") and decision["phase"] is None
    for path in paths:
        assert kill_detail(decision).count(path) == 1, (path, decision)
    assert r.sp.read_bytes() == state_before and (r.sd / "lock.json").read_bytes() == lock_before


# @spec ROUTE-KILL-001
def test_kill_switch_in_main_worktree_listed_once(r):
    path = stop_file(r.ws["repo"])
    res = run_script("next_step.py", "--workstream", "feat-demo", *holder_args(), cwd=r.ws["repo"])
    assert res.returncode == 0, res.stderr
    assert stops(res.json, "KILL_SWITCH") and kill_detail(res.json).count(path) == 1


# @spec ROUTE-KILL-001, ROUTE-CLI-008, ROUTE-CLI-001
def test_kill_switch_before_wake(r):
    stop_file(r.wt)
    r.sp.write_text("{broken")
    assert stops(r.start(), "KILL_SWITCH")
    assert r.sp.read_text() == "{broken"


# @spec ROUTE-KILL-001, ROUTE-CLI-008
def test_kill_switch_before_lock(r):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        lock = r.sd / "lock.json"
        data = json.loads(lock.read_text())
        data.update(holder_pid=proc.pid, holder_session="other")
        lock.write_text(json.dumps(data))
        stop_file(r.ws["repo"])
        before = lock.read_bytes()
        assert stops(r.start(), "KILL_SWITCH")
        assert lock.read_bytes() == before
    finally:
        proc.kill()
        proc.wait()


# @spec ROUTE-KILL-001
def test_kill_switch_does_not_read_event(r):
    stop_file(r.wt)
    args = ["--workstream", "feat-demo", *holder_args(), "--event", "-"]
    res = run_script("next_step.py", *args, cwd=r.wt, stdin="{not json")
    assert res.returncode == 0, res.stderr
    assert stops(res.json, "KILL_SWITCH")


# @spec ROUTE-KILL-001
def test_kill_switch_stops_mode_event(r):
    r.start()
    stop_file(r.ws["repo"])
    before = r.sp.read_bytes()
    assert stops(r.mode("delivery"), "KILL_SWITCH")
    assert r.sp.read_bytes() == before


# ------------------------------------------------------------------------- modes


MODES = ["delivery", "collab-plan", "collab-qa", "collab-dev", "collab-code", "collab"]


def set_mode(r, **config):
    state = r.state()
    state["config"].update(config)
    write_state(r.sp, state)


def mode_set(decision, mode):
    assert decision["action"] == "mode_set", decision
    assert decision["mode"] == mode, decision
    return True


# @spec ROUTE-MODE-001, ROUTE-CLI-003
@pytest.mark.parametrize("mode", MODES)
def test_mode_event_sets_mode(r, mode):
    before = r.state()
    decision = r.mode(mode)
    assert mode_set(decision, mode) and decision["phase"] == "INIT"
    after = r.state()
    assert after["config"]["mode"] == mode
    assert after["lifecycle"] == before["lifecycle"]


# @spec ROUTE-MODE-001
def test_mode_event_same_mode_still_reports(r):
    set_mode(r, mode="collab")
    assert mode_set(r.mode("collab"), "collab")
    assert r.state()["config"]["mode"] == "collab"


# @spec ROUTE-MODE-001, ROUTE-CLI-008
def test_mode_event_skips_drift_dirty_and_dispatch(r):
    first = r.goto("DEV_FULL")
    r.patch(anchors={"last_known_head_sha": head(r.wt)})
    r.human_commit({"src/h.py": "h\n"})
    Path(r.wt, "wip.txt").write_text("wip\n")
    before = r.state()
    decision = r.mode("delivery")
    assert mode_set(decision, "delivery") and decision["phase"] == "DEV_FULL"
    after = r.state()
    assert after["lifecycle"]["dispatched"]["dispatch_id"] == first["invoke"]["dispatch_id"]
    assert after["lifecycle"]["awaiting"] is None
    assert after["lifecycle"].get("after_reconcile") == before["lifecycle"].get("after_reconcile")
    assert after["anchors"] == before["anchors"]


# @spec ROUTE-MODE-002
@pytest.mark.parametrize("value", ["turbo", "Collab", "", None])
def test_mode_event_unknown_mode(r, value):
    r.start()
    unchanged_stop(r, lambda: r.mode(value), "EVENT_INVALID")


# @spec ROUTE-MODE-002
def test_mode_event_at_done(r):
    r.patch(lifecycle={"current_phase": "DONE"})
    unchanged_stop(r, lambda: r.mode("delivery"), "EVENT_INVALID")


# @spec ROUTE-MODE-010
@pytest.mark.parametrize("event", [None, {"type": "start"}, {"type": "mode", "mode": "collab"}],
                         ids=["no-event", "start", "mode"])
def test_stored_mode_not_a_mode_stops(r, event):
    set_mode(r, mode="turbo")
    before = r.sp.read_bytes()
    decision = r.run(event)
    assert stops(decision, "STATE_INVALID") and "turbo" in decision["detail"]
    assert r.sp.read_bytes() == before


# @spec ROUTE-MODE-007
def test_no_mode_routes_as_collab(r):
    state = r.state()
    state["config"].pop("mode", None)
    # Keys of an older config are not modes; only a missing mode decides the routing.
    state["config"].update(repo_mode="LID", plan_mode="auto", collaborative=False, council_auto_resolve=True)
    write_state(r.sp, state)
    assert dispatches(r.start(), "forge-planner", "strict")
    assert pauses(r.result("STAGE_COMPLETE"), "plan_approval")
    r.patch(scope_manifest={"assigned": ["src/**"], "completed": [], "remaining": ["src/**"]})
    r.goto("POST_PLAN")
    assert dispatches(r.result("PASS"), "forge-verifier", "PRE_QA")
    assert r.state()["lifecycle"]["two_pass"] is True


def stage_approval(r):
    """LID workstream in collab paused on plan_approval after PLAN_S1's STAGE_COMPLETE."""
    set_mode(r, repo_mode="LID", mode="collab")
    r.start()
    assert pauses(r.result("STAGE_COMPLETE"), "plan_approval")


# @spec ROUTE-MODE-003, ROUTE-MODE-006
@pytest.mark.parametrize("mode", ["delivery", "collab-qa", "collab-dev", "collab-code"])
def test_mode_switch_approves_stage_pause(r, mode):
    stage_approval(r)
    assert mode_set(r.mode(mode), mode)
    lc = r.state()["lifecycle"]
    assert lc["awaiting"] is None and lc["dispatched"] is None
    decision = r.run()
    assert dispatches(decision, "forge-planner", "auto") and decision["phase"] == "PLAN_S2"


# @spec ROUTE-MODE-003
@pytest.mark.parametrize("mode", ["collab-plan", "collab"])
def test_mode_switch_keeps_stage_pause(r, mode):
    stage_approval(r)
    assert mode_set(r.mode(mode), mode)
    assert pauses(r.run(), "plan_approval")


def plan_approval(r):
    """Naive workstream paused on plan_approval of the finished plan."""
    r.start()
    assert pauses(r.result("READY_FOR_QA", scope={"assigned": ["src/**"]}), "plan_approval")


# @spec ROUTE-MODE-004, ROUTE-MODE-006
def test_mode_switch_to_delivery_approves_plan(r):
    plan_approval(r)
    assert mode_set(r.mode("delivery"), "delivery")
    state = r.state()
    assert state["lifecycle"]["awaiting"] is None and state["lifecycle"]["dispatched"] is None
    assert state["anchors"]["plan_base_sha"] == head(r.wt)
    assert state["scope_manifest"] == {"assigned": ["src/**"], "completed": [], "remaining": ["src/**"]}
    assert dispatches(r.run(), "forge-verifier", "POST_PLAN")


# @spec ROUTE-MODE-004
@pytest.mark.parametrize("mode", ["collab-plan", "collab-qa", "collab-dev", "collab-code"])
def test_mode_switch_keeps_plan_pause(r, mode):
    plan_approval(r)
    assert mode_set(r.mode(mode), mode)
    assert pauses(r.run(), "plan_approval")


def edit_window(r, raised_after, two_pass):
    """Pause edit_window after raised_after, in the mode that has that window."""
    set_mode(r, mode="collab-dev" if raised_after == "DEV_FULL" else "collab")
    r.patch(lifecycle={"two_pass": two_pass})
    r.goto(raised_after, **({"pass": 1} if raised_after in ("QA_SKEL", "DEV_STUB") else {}))
    status = {"QA_SKEL": "SKELETON_COMMITTED", "DEV_STUB": "STUBS_COMMITTED", "POST_QA": "PASS",
              "DEV_FULL": "IMPLEMENTATION_COMPLETE"}[raised_after]
    if raised_after != "POST_QA":
        r.spoke_commit({f"src/{raised_after.lower()}.py": "x\n"})
    assert pauses(r.result(status), "edit_window")


# @spec ROUTE-MODE-005, ROUTE-MODE-006
@pytest.mark.parametrize("raised_after, two_pass, mode, agent, phase", [
    ("QA_SKEL", True, "delivery", "forge-verifier", "PRE_DEV"),
    ("QA_SKEL", True, "collab-qa", "forge-verifier", "PRE_DEV"),
    ("DEV_STUB", True, "collab-dev", "forge-verifier", "PRE_QA"),
    ("POST_QA", True, "collab-plan", "forge-verifier", "PRE_DEV"),
    ("POST_QA", False, "collab-dev", "forge-dev", "DEV_FULL"),
    ("DEV_FULL", False, "collab", "forge-verifier", "VERIFY_FINAL"),
    ("DEV_FULL", False, "delivery", "forge-verifier", "VERIFY_FINAL"),
])
def test_mode_switch_continues_edit_window(r, raised_after, two_pass, mode, agent, phase):
    edit_window(r, raised_after, two_pass)
    assert mode_set(r.mode(mode), mode)
    lc = r.state()["lifecycle"]
    assert lc["awaiting"] is None and lc["dispatched"] is None
    if raised_after == "DEV_STUB":
        assert lc["pass"] == 2
    decision = r.run()
    assert dispatches(decision, agent) and decision["phase"] == phase


# @spec ROUTE-MODE-005
@pytest.mark.parametrize("raised_after, mode", [
    ("QA_SKEL", "collab-code"), ("DEV_STUB", "collab-code"), ("POST_QA", "collab-qa"),
    ("POST_QA", "collab-code"), ("DEV_FULL", "collab-dev"),
])
def test_mode_switch_keeps_edit_window(r, raised_after, mode):
    edit_window(r, raised_after, True)
    assert mode_set(r.mode(mode), mode)
    assert pauses(r.run(), "edit_window")


def council_signoff(r, kind="TESTS_FAILED", phase="VERIFY_FINAL", requires_user=False):
    set_mode(r, mode="collab")
    raise_blocker(r, kind, phase)
    assert pauses(r.result("COUNCIL_RULING", ruling=ruling(["forge-dev"], requires_user)), "council_signoff")


# @spec ROUTE-MODE-009, ROUTE-MODE-006
def test_mode_switch_to_delivery_applies_ruling(r):
    council_signoff(r)
    blocker_id = r.state()["blockers"][0]["id"]
    assert mode_set(r.mode("delivery"), "delivery")
    state = r.state()
    [entry] = state["council_decisions"]
    assert (entry["blocker_id"], entry["option"], entry["approved_by"]) == (blocker_id, "A", "auto")
    assert state["lifecycle"]["awaiting"] is None and state["lifecycle"]["dispatched"] is None
    assert [i["agent"] for i in state["lifecycle"]["repair_queue"]] == ["forge-dev"]
    assert dispatches(r.run(), "forge-dev", "repair")


# @spec ROUTE-MODE-006, ROUTE-MODE-009
@pytest.mark.parametrize("kind, phase, agent, agent_mode", [("TEST_CHANGE_REQUESTED", "DEV_FULL", "forge-dev", "full"),
                                                            ("CI_FAILED", "CI_WATCH", "forge-scout", "CI_RERUN")])
def test_mode_switch_ruling_keeps_dispatch_options(r, kind, phase, agent, agent_mode):
    set_mode(r, mode="collab")
    raise_blocker(r, kind, phase)
    assert pauses(r.result("COUNCIL_RULING", ruling=ruling([])), "council_signoff")
    assert mode_set(r.mode("delivery"), "delivery")
    decision = r.run()
    assert dispatches(decision, agent, agent_mode) and decision["phase"] == phase
    if kind == "TEST_CHANGE_REQUESTED":
        assert DELTA_A in json.dumps(decision["invoke"]["notes"])


# @spec ROUTE-MODE-009
@pytest.mark.parametrize("kind, phase, requires_user, mode", [
    ("TESTS_FAILED", "VERIFY_FINAL", True, "delivery"),
    ("CONCERNS", "PLAN", False, "delivery"),
    ("TESTS_FAILED", "VERIFY_FINAL", False, "collab-qa"),
])
def test_mode_switch_keeps_council_signoff(r, kind, phase, requires_user, mode):
    council_signoff(r, kind, phase, requires_user)
    assert mode_set(r.mode(mode), mode)
    assert r.state()["council_decisions"] == []
    assert pauses(r.run(), "council_signoff")


# @spec ROUTE-MODE-008
@pytest.mark.parametrize("first, switched, planner_mode", [("collab", "collab-qa", "strict"),
                                                           ("collab-qa", "collab", "auto")])
def test_revise_keeps_replaced_planner_mode(r, first, switched, planner_mode):
    set_mode(r, repo_mode="LID", mode=first)
    r.goto("PLAN_S4")
    assert pauses(r.result("READY_FOR_QA", scope={"assigned": ["src/**"]}), "plan_approval")
    assert mode_set(r.mode(switched), switched)
    decision = r.user("revise", notes="split the parser")
    assert dispatches(decision, "forge-planner", planner_mode) and decision["phase"] == "PLAN_S4"


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


def copied_router(tmp_path, prefix_file=None):
    """A copy of reference/scripts, optionally with forge_install.json holding prefix_file."""
    scripts = tmp_path / "installed-scripts"
    scripts.mkdir()
    for f in SCRIPTS.glob("*.py"):
        shutil.copy(f, scripts)
    if prefix_file is not None:
        (scripts / "forge_install.json").write_text(prefix_file)
    return scripts


def run_copy(r, scripts, event=None):
    args = ["--workstream", r.ws["slug"], *holder_args()]
    if event is not None:
        args += ["--event", "-"]
    proc = subprocess.run([sys.executable, str(scripts / "next_step.py"), *args], cwd=r.wt,
                          input=json.dumps(event) if event is not None else None, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# @spec ROUTE-DISP-010
def test_dispatch_without_install_file_has_bare_name(r):
    inv = r.start()["invoke"]
    assert (inv["agent_prefix"], inv["dispatch_as"]) == ("", inv["agent"])
    assert r.state()["lifecycle"]["invoke"]["dispatch_as"] == inv["agent"]


# @spec ROUTE-DISP-010
def test_dispatch_with_plugin_prefix(r, tmp_path):
    scripts = copied_router(tmp_path, '{"agent_prefix": "forge:"}\n')
    inv = run_copy(r, scripts, {"type": "start"})["invoke"]
    assert inv["agent_prefix"] == "forge:"
    assert inv["dispatch_as"] == "forge:" + inv["agent"] and inv["agent"].startswith("forge-")
    assert r.state()["lifecycle"]["dispatched"]["agent"] == inv["agent"]


# @spec ROUTE-DISP-011
@pytest.mark.parametrize("content", ["{bad", "[]", "{}", '{"agent_prefix": 1}'])
def test_unreadable_install_file_stops(r, tmp_path, content):
    scripts = copied_router(tmp_path, content)
    before = r.sp.read_bytes()
    decision = run_copy(r, scripts, {"type": "start"})
    assert stops(decision, "INSTALL_INVALID")
    assert str(scripts / "forge_install.json") in decision["detail"]
    assert r.sp.read_bytes() == before


# @spec ROUTE-REDO-002
def test_redispatch_refreshes_prefix_only(r, tmp_path):
    scripts = copied_router(tmp_path)
    first = run_copy(r, scripts, {"type": "start"})["invoke"]
    (scripts / "forge_install.json").write_text('{"agent_prefix": "forge:"}\n')
    again = run_copy(r, scripts)["invoke"]
    assert again == {**first, "agent_prefix": "forge:", "dispatch_as": "forge:" + first["agent"]}


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


def keeps_refs(refs, previous):
    return refs[:len(previous)] == previous and len(set(refs)) == len(refs)


def with_context_refs(r, refs):
    """Give the outstanding dispatch's stored invocation these context_refs."""
    state = r.state()
    state["lifecycle"]["invoke"]["context_refs"] = refs
    write_state(r.sp, state)


# @spec ROUTE-DISP-008
def test_plan_revise_keeps_context_refs(r):
    r.patch(identity={"issue": "https://github.com/acme/widgets/issues/1"})
    r.start()
    previous = r.result("OK")["invoke"]["context_refs"]
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    revised = r.user("revise", notes="split the parser")
    assert previous and keeps_refs(revised["invoke"]["context_refs"], previous), revised["invoke"]


def with_task(r):
    task = r.sd / "task.md"
    task.write_text("Add top_words(text, n); ties broken alphabetically.\n")
    return str(task)


# @spec ROUTE-DISP-009
def test_planner_gets_task_first(r):
    task = with_task(r)
    refs = r.start()["invoke"]["context_refs"]
    assert refs[0] == task and refs.count(task) == 1


# @spec ROUTE-DISP-009
def test_task_before_scout_brief_on_issue(r):
    task = with_task(r)
    r.patch(identity={"issue": "https://github.com/acme/widgets/issues/1"})
    r.start()
    refs = r.result("OK")["invoke"]["context_refs"]
    assert refs[0] == task and refs.count(task) == 1 and len(refs) == 2


# @spec ROUTE-DISP-009, ROUTE-DISP-008
def test_task_listed_once_after_revise(r):
    task = with_task(r)
    r.start()
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    refs = r.user("revise", notes="split parser")["invoke"]["context_refs"]
    assert refs[0] == task and refs.count(task) == 1


# @spec ROUTE-DISP-009
def test_planner_repair_gets_task_first(r):
    task = with_task(r)
    decision = r.goto("REPAIR", resume_phase="VERIFY_FINAL", repair_queue=[
        {"agent": "forge-planner", "notes": "fix", "context_refs": ["council/m.md", task]}])
    assert dispatches(decision, "forge-planner")
    assert decision["invoke"]["context_refs"] == [task, "council/m.md"]


# @spec ROUTE-DISP-009
def test_internal_reviewer_gets_task_first(r):
    task = with_task(r)
    r.patch(anchors={"verifier_pass_sha": head(r.wt)})
    decision = r.goto("REVIEW_INTERNAL")
    assert dispatches(decision, "forge-reviewer", "internal")
    assert decision["invoke"]["context_refs"][0] == task


# @spec ROUTE-DISP-009
@pytest.mark.parametrize("phase, agent", [("DEV_FULL", "forge-dev"), ("QA_FULL", "forge-qa"),
                                          ("VERIFY_FINAL", "forge-verifier")])
def test_other_agents_do_not_get_task(r, phase, agent):
    task = with_task(r)
    decision = r.goto(phase)
    assert dispatches(decision, agent) and task not in decision["invoke"]["context_refs"]


# @spec ROUTE-DISP-009
def test_no_task_file_no_task_ref(r):
    refs = r.start()["invoke"]["context_refs"]
    assert not any(str(x).endswith("task.md") for x in refs)


# @spec ROUTE-DISP-008
@pytest.mark.parametrize("answer", ["approve", "reject"])
def test_human_code_redispatch_keeps_context_refs(r, answer):
    r.goto("DEV_FULL")
    with_context_refs(r, ["specs/dev.md"])
    r.result("HUMAN_CODE_CHANGE_REQUESTED", escalation=ESCALATION)
    decision = r.user(answer, notes="go ahead")
    assert dispatches(decision, "forge-dev", "full")
    assert keeps_refs(decision["invoke"]["context_refs"], ["specs/dev.md"]), decision["invoke"]


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


def guard_record(r):
    d = r.state()["lifecycle"]["dispatched"]
    return r.sd / "envelopes" / f"{d['dispatch_id']}.guard.json"


def unguarded_result(r, status="READY_FOR_QA"):
    d = r.state()["lifecycle"]["dispatched"]
    return r.run({"type": "result", "envelope": {"dispatch_id": d["dispatch_id"], "agent": d["agent"],
                                                 "status": status, "head_sha": head(r.wt),
                                                 "files_touched": [], "summary": "s"}})


# @spec ROUTE-ENV-015
def test_missing_guard_record_stops(r):
    r.start()
    unchanged_stop(r, lambda: unguarded_result(r), "GUARD_INACTIVE")
    assert r.state()["lifecycle"]["dispatched"] is not None


# @spec ROUTE-ENV-015
@pytest.mark.parametrize("content", ["{not json", "[]", "{}", '{"agents": "forge-scout"}'])
def test_unreadable_guard_record_stops(r, content):
    r.start()
    path = guard_record(r)
    path.parent.mkdir(exist_ok=True)
    path.write_text(content)
    unchanged_stop(r, lambda: unguarded_result(r), "GUARD_INACTIVE")


# @spec ROUTE-ENV-015
def test_guard_record_without_dispatched_agent_stops(r):
    r.start()
    write_guard_record(r.sp, r.state()["lifecycle"]["dispatched"], agents=["forge-council-critic", "forge-dev"])
    unchanged_stop(r, lambda: unguarded_result(r), "GUARD_INACTIVE")


# @spec ROUTE-ENV-015
def test_guard_check_before_other_envelope_checks(r):
    r.start()
    unchanged_stop(r, lambda: unguarded_result(r, status="TESTS_COMMITTED"), "GUARD_INACTIVE")


# @spec ROUTE-ENV-015
def test_dispatch_match_before_guard_check(r):
    r.start()
    d = r.state()["lifecycle"]["dispatched"]
    env = {"dispatch_id": "nope", "agent": d["agent"], "status": "READY_FOR_QA", "head_sha": head(r.wt),
           "files_touched": [], "summary": "s"}
    unchanged_stop(r, lambda: r.run({"type": "result", "envelope": env}))


# @spec ROUTE-ENV-015
def test_dry_run_guard_check(r):
    r.start()
    assert stops(r.run({"type": "result", "envelope": {
        **{k: r.state()["lifecycle"]["dispatched"][k] for k in ("dispatch_id", "agent")},
        "status": "READY_FOR_QA", "head_sha": head(r.wt), "files_touched": [], "summary": "s"}}, "--dry-run"),
        "GUARD_INACTIVE")


# @spec ROUTE-ENV-016
def test_guard_inactive_detail_names_record_and_causes(r):
    r.start()
    path = guard_record(r)
    detail = unguarded_result(r)["detail"]
    assert str(path) in detail
    for cause in ["project-scope install", "-p", "workspace trust", "managed", "python3"]:
        assert cause in detail, cause


# @spec ROUTE-ENV-002
def test_status_not_allowed_in_phase(r):
    r.start()
    unchanged_stop(r, lambda: r.result("TESTS_COMMITTED"))


ESCALATION = {"details": "escalated", "paths": ["src/legacy.py"], "ranges": {"src/legacy.py": [[10, 14]]}}


def place(r, phase, queue_agent=None):
    """Dispatch phase; in REPAIR, with one queue item for queue_agent."""
    if phase == "REPAIR":
        return r.goto("REPAIR", repair_queue=[{"agent": queue_agent, "notes": "fix", "context_refs": []}],
                      resume_phase="VERIFY_FINAL")
    return r.goto(phase, **({"pass": 1} if phase in {"QA_SKEL", "DEV_STUB"} else {}))


# @spec ROUTE-ENV-014
@pytest.mark.parametrize("phase, queue_agent, status", [
    ("QA_SKEL", None, "CONTRACT_MISMATCH"), ("QA_FULL", None, "CONTRACT_MISMATCH"),
    ("QA_SKEL", None, "HUMAN_CODE_CHANGE_REQUESTED"), ("QA_FULL", None, "HUMAN_CODE_CHANGE_REQUESTED"),
    ("DEV_STUB", None, "HUMAN_CODE_CHANGE_REQUESTED"), ("DEV_FULL", None, "HUMAN_CODE_CHANGE_REQUESTED"),
    ("REPAIR", "forge-qa", "CONTRACT_MISMATCH"), ("REPAIR", "forge-qa", "HUMAN_CODE_CHANGE_REQUESTED"),
    ("REPAIR", "forge-dev", "HUMAN_CODE_CHANGE_REQUESTED"),
])
def test_status_accepted_in_phase(r, phase, queue_agent, status):
    place(r, phase, queue_agent)
    decision = r.result(status, escalation=ESCALATION)
    assert decision.get("status") != "ENVELOPE_INVALID", decision
    assert r.state()["lifecycle"]["last_status"] == status


# @spec ROUTE-ENV-002, ROUTE-ENV-014
@pytest.mark.parametrize("phase, queue_agent, status", [
    ("QA_SKEL", None, "TEST_CHANGE_REQUESTED"), ("QA_FULL", None, "TEST_CHANGE_REQUESTED"),
    ("POST_PLAN", None, "TESTS_FAILED"), ("RECONCILE", None, "AUDIT_FAILED"), ("PLAN", None, "STAGE_COMPLETE"),
    ("REPAIR", "forge-dev", "IMPLEMENTATION_COMPLETE"),
])
def test_status_outside_phase_table_rejected(r, phase, queue_agent, status):
    place(r, phase, queue_agent)
    unchanged_stop(r, lambda: r.result(status, escalation=ESCALATION))


# @spec ROUTE-ENV-002, ROUTE-ENV-012
@pytest.mark.parametrize("case, status", [
    ("strict-PLAN_S1", "READY_FOR_QA"), ("strict-PLAN_S3", "READY_FOR_QA"), ("strict-PLAN_S4", "STAGE_COMPLETE"),
    ("auto-PLAN_S1", "STAGE_COMPLETE"), ("draft", "REPORT_SENT"), ("send", "REPORT_DRAFTED"),
    ("planner", "HUMAN_CODE_CHANGE_REQUESTED"), ("planner-repair", "HUMAN_CODE_CHANGE_REQUESTED"),
    ("dev-repair", "CONTRACT_MISMATCH"),
])
def test_status_excluded_by_agent_or_mode(r, case, status):
    if case.startswith("strict-"):
        set_mode(r, repo_mode="LID")
        r.goto(case.split("-")[1])
    elif case == "auto-PLAN_S1":
        set_mode(r, repo_mode="LID")
        r.goto("PLAN_S1")
        state = r.state()  # statuses are checked against the dispatch's recorded agent mode
        state["lifecycle"]["dispatched"]["mode"] = "auto"
        write_state(r.sp, state)
    elif case == "draft":
        at_report(r)
    elif case == "send":
        at_report(r)
        r.result("REPORT_DRAFTED")
        r.user("approve")
    elif case == "planner":
        r.start()
    else:
        place(r, "REPAIR", "forge-planner" if case == "planner-repair" else "forge-dev")
    unchanged_stop(r, lambda: r.result(status, escalation=ESCALATION))


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


# @spec ROUTE-ENV-005
def test_council_option_route_outside_builders(r):
    r.goto("VERIFY_FINAL")
    r.result("TESTS_FAILED", escalation={"details": "red"})
    unchanged_stop(r, lambda: r.result("COUNCIL_RULING", ruling=ruling(["forge-dev"], alt_route=["forge-reviewer"])))


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


def envelope_file(r, dispatch_id):
    return str(r.sd / "envelopes" / f"{dispatch_id}.json")


def dispatched_id(r):
    return r.state()["lifecycle"]["dispatched"]["dispatch_id"]


# @spec ROUTE-ENV-013
@pytest.mark.parametrize("status", ["PUBLISHED", "PR_REVIEW_READY", "REPORT_DRAFTED"])
def test_report_input_set_from_envelope(r, status):
    if status == "PUBLISHED":
        at_publish(r, "push")
    elif status == "PR_REVIEW_READY":
        r.patch(identity={"flow": "pr_review", "pr": "https://github.com/acme/widgets/pull/7"})
        r.start()
        r.result("OK")
    else:
        at_report(r)
    did = dispatched_id(r)
    r.result(status, **({"publish_target": "push"} if status == "PUBLISHED" else {}))
    assert r.state()["lifecycle"].get("report_input") == envelope_file(r, did)


# --------------------------------------------------------------------- build flow


# @spec ROUTE-BUILD-001
def test_start_with_issue_goes_to_recon(r):
    r.patch(identity={"issue": "https://github.com/acme/widgets/issues/1"})
    assert dispatches(r.start(), "forge-scout", "PRE_PLAN")
    assert r.state()["lifecycle"]["current_phase"] == "RECON"


# @spec ROUTE-BUILD-001, ROUTE-BUILD-003
@pytest.mark.parametrize("repo_mode, mode, phase, planner_mode",
                         [("LID", m, "PLAN_S1", "strict" if m in ("collab-plan", "collab") else "auto") for m in MODES]
                         + [("NAIVE", "collab", "PLAN", "naive"), ("NAIVE", "delivery", "PLAN", "naive")])
def test_start_without_issue(r, repo_mode, mode, phase, planner_mode):
    set_mode(r, repo_mode=repo_mode, mode=mode)
    decision = r.start()
    assert dispatches(decision, "forge-planner", planner_mode) and decision["phase"] == phase


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


# @spec ROUTE-BUILD-004
@pytest.mark.parametrize("mode", ["delivery", "collab-qa", "collab-dev", "collab-code"])
def test_stage_complete_without_stage_approval(r, mode):
    set_mode(r, repo_mode="LID", mode="collab")
    r.start()  # a strict dispatch, then the mode changes while it runs
    set_mode(r, mode=mode)
    decision = r.result("STAGE_COMPLETE")
    assert dispatches(decision, "forge-planner", "auto") and decision["phase"] == "PLAN_S2"


# @spec ROUTE-BUILD-004
def test_stage_complete_collab_plan_pauses(r):
    set_mode(r, repo_mode="LID", mode="collab-plan")
    r.start()
    assert pauses(r.result("STAGE_COMPLETE"), "plan_approval")
    assert dispatches(r.user("approve"), "forge-planner", "strict")


# @spec ROUTE-BUILD-005
@pytest.mark.parametrize("mode", ["collab-plan", "collab-qa", "collab-dev", "collab-code", "collab"])
def test_ready_for_qa_pauses_unless_delivery(r, mode):
    set_mode(r, mode=mode)
    r.start()
    assert pauses(r.result("READY_FOR_QA", scope={"assigned": ["src/**"]}), "plan_approval")
    assert dispatches(r.user("approve"), "forge-verifier", "POST_PLAN")


# @spec ROUTE-BUILD-041
@pytest.mark.parametrize("assigned", [["src/**", "lib/**"], []])
def test_ready_for_qa_in_delivery(r, assigned):
    set_mode(r, mode="delivery")
    r.patch(scope_manifest={"assigned": [], "completed": ["old/**"], "remaining": ["old/**"]})
    r.start()
    decision = r.result("READY_FOR_QA", scope={"assigned": assigned})
    assert dispatches(decision, "forge-verifier", "POST_PLAN")
    state = r.state()
    assert state["anchors"]["plan_base_sha"] == head(r.wt)
    assert state["scope_manifest"] == {"assigned": assigned, "completed": [], "remaining": assigned}


# @spec ROUTE-BUILD-005
def test_ready_for_qa(r):
    r.patch(scope_manifest={"assigned": [], "completed": ["old/**"], "remaining": ["old/**"]})
    r.start()
    assert pauses(r.result("READY_FOR_QA", scope={"assigned": ["src/**", "lib/**"]}), "plan_approval")
    assert r.state()["scope_manifest"]["assigned"] == ["src/**", "lib/**"]
    decision = r.user("approve")
    assert dispatches(decision, "forge-verifier", "POST_PLAN")
    state = r.state()
    assert state["anchors"]["plan_base_sha"] == head(r.wt)
    assert state["scope_manifest"] == {"assigned": ["src/**", "lib/**"], "completed": [],
                                       "remaining": ["src/**", "lib/**"]}


# @spec ROUTE-BUILD-005
def test_ready_for_qa_with_empty_scope(r):
    r.start()
    assert pauses(r.result("READY_FOR_QA", scope={"assigned": []}), "plan_approval")
    assert r.state()["scope_manifest"]["assigned"] == []
    assert dispatches(r.user("approve"), "forge-verifier", "POST_PLAN")


# @spec ROUTE-BUILD-006
def test_plan_revise(r):
    r.start()
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    decision = r.user("revise", notes="split the parser")
    assert dispatches(decision, "forge-planner", "naive") and decision["phase"] == "PLAN"
    assert "split the parser" in json.dumps(decision["invoke"]["notes"])


# @spec ROUTE-BUILD-007
@pytest.mark.parametrize("mode", MODES)
def test_post_plan_pass(r, mode):
    two_pass = mode in ("collab-code", "collab")
    set_mode(r, mode=mode)
    r.patch(scope_manifest={"assigned": ["src/**"], "completed": [], "remaining": ["src/**"]})
    r.goto("POST_PLAN")
    decision = r.result("PASS")
    lc = r.state()["lifecycle"]
    assert lc["two_pass"] is two_pass
    if two_pass:
        assert dispatches(decision, "forge-verifier", "PRE_QA") and lc["pass"] == 1
    else:
        assert dispatches(decision, "forge-qa", "full") and decision["phase"] == "QA_FULL"


# @spec ROUTE-BUILD-007
@pytest.mark.parametrize("mode, two_pass", [("collab", False), ("delivery", True)])
def test_post_plan_pass_keeps_build_flow(r, mode, two_pass):
    set_mode(r, mode=mode)
    r.patch(scope_manifest={"assigned": ["src/**"], "completed": [], "remaining": ["src/**"]})
    r.goto("POST_PLAN", two_pass=two_pass)
    decision = r.result("PASS")
    assert r.state()["lifecycle"]["two_pass"] is two_pass
    assert decision["phase"] == ("PRE_QA" if two_pass else "QA_FULL")


# @spec ROUTE-BUILD-035
@pytest.mark.parametrize("mode", ["collab", "delivery"])
def test_post_plan_pass_with_empty_scope_goes_to_verify_final(r, mode):
    set_mode(r, mode=mode)
    r.goto("POST_PLAN")
    assert dispatches(r.result("PASS"), "forge-verifier", "VERIFY_FINAL")


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


# @spec ROUTE-BUILD-011
@pytest.mark.parametrize("mode, paused", [(m, m in ("collab-code", "collab")) for m in MODES])
def test_qa_skeleton_edit_window_by_mode(r, mode, paused):
    set_mode(r, mode=mode)
    r.goto("QA_SKEL", **{"pass": 1})
    r.spoke_commit({"tests/test_a.py": "def test_a(): ...\n"})
    decision = r.result("SKELETON_COMMITTED")
    if paused:
        assert pauses(decision, "edit_window")
    else:
        assert dispatches(decision, "forge-verifier", "PRE_DEV")


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


# @spec ROUTE-BUILD-014
@pytest.mark.parametrize("mode, paused", [(m, m in ("collab-code", "collab")) for m in MODES])
def test_dev_stubs_edit_window_by_mode(r, mode, paused):
    set_mode(r, mode=mode)
    r.goto("DEV_STUB", **{"pass": 1})
    r.spoke_commit({"src/a.py": "def a(): raise NotImplementedError\n"})
    decision = r.result("STUBS_COMMITTED")
    if paused:
        assert pauses(decision, "edit_window") and r.state()["lifecycle"]["pass"] == 1
    else:
        assert dispatches(decision, "forge-verifier", "PRE_QA") and r.state()["lifecycle"]["pass"] == 2


# @spec ROUTE-BUILD-015
def test_qa_full_to_post_qa(r):
    r.goto("QA_FULL")
    r.spoke_commit({"tests/test_a.py": "def test_a(): assert False\n"})
    assert dispatches(r.result("TESTS_COMMITTED"), "forge-verifier", "POST_QA")


# @spec ROUTE-BUILD-016
@pytest.mark.parametrize("mode", ["collab-qa", "collab-code", "collab"])
@pytest.mark.parametrize("two_pass, phase", [(True, "PRE_DEV"), (False, "DEV_FULL")])
def test_post_qa_edit_window(r, mode, two_pass, phase):
    set_mode(r, mode=mode)
    r.goto("POST_QA", two_pass=two_pass)
    assert pauses(r.result("PASS"), "edit_window")
    assert r.user("continue")["phase"] == phase


# @spec ROUTE-BUILD-032
@pytest.mark.parametrize("mode", ["delivery", "collab-plan", "collab-dev"])
@pytest.mark.parametrize("two_pass, agent, phase", [(True, "forge-verifier", "PRE_DEV"),
                                                    (False, "forge-dev", "DEV_FULL")])
def test_post_qa_without_edit_window(r, mode, two_pass, agent, phase):
    set_mode(r, mode=mode)
    r.goto("POST_QA", two_pass=two_pass)
    decision = r.result("PASS")
    assert dispatches(decision, agent) and decision["phase"] == phase


# @spec ROUTE-BUILD-017
@pytest.mark.parametrize("mode", [m for m in MODES if m != "collab-dev"])
def test_dev_full_to_verify_final(r, mode):
    set_mode(r, mode=mode)
    r.goto("DEV_FULL")
    r.spoke_commit({"src/a.py": "a\n"})
    assert dispatches(r.result("IMPLEMENTATION_COMPLETE"), "forge-verifier", "VERIFY_FINAL")


# @spec ROUTE-BUILD-017
def test_dev_full_edit_window_in_collab_dev(r):
    set_mode(r, mode="collab-dev")
    r.goto("DEV_FULL")
    r.spoke_commit({"src/a.py": "a\n"})
    assert pauses(r.result("IMPLEMENTATION_COMPLETE"), "edit_window")
    assert dispatches(r.user("continue"), "forge-verifier", "VERIFY_FINAL")


# @spec ROUTE-BUILD-018, ROUTE-BUILD-019
def test_verify_final_gate_passes(r):
    r.goto("DEV_FULL")
    r.spoke_commit({"src/a.py": "a\n"})
    r.result("IMPLEMENTATION_COMPLETE")
    decision = r.result("PASS")
    assert dispatches(decision, "forge-reviewer", "internal")
    assert r.state()["anchors"]["verifier_pass_sha"] == head(r.wt)


# @spec ROUTE-BUILD-018, ROUTE-BUILD-038
def test_verify_final_gate_covers_planner_commits(r):
    r.start()
    r.spoke_commit({"src/stray.py": "planner wrote production code\n"})
    r.result("READY_FOR_QA", scope={"assigned": ["src/**"]})
    r.user("approve")
    r.goto("VERIFY_FINAL")
    decision = r.result("PASS")
    state = r.state()
    assert state["anchors"]["verifier_pass_sha"] is None
    assert stops(decision, "GATE_UNREPAIRABLE") and "BOUNDARY_VIOLATION" in json.dumps(decision)
    assert state["blockers"] == []


def gate_finds_human_code(r, extra=None):
    """Two dev commits that each modify a protected human line; extra adds a third dev commit."""
    r.human_commit({"src/h.py": "h1\nh2\n"})  # before the first dispatch: workstream human code, not drift
    r.goto("DEV_FULL")
    dev1 = r.spoke_commit({"README.md": "changed\n"})  # baseline line 1
    dev2 = r.spoke_commit({"src/h.py": "h1\nX\n"})  # workstream line 2
    if extra:
        r.spoke_commit(extra)
    r.result("IMPLEMENTATION_COMPLETE")
    return r.result("PASS"), dev1, dev2


# @spec ROUTE-BUILD-020
def test_gate_human_code_findings_pause_for_approval(r):
    decision, dev1, dev2 = gate_finds_human_code(r)
    assert pauses(decision, "human_code_approval")
    shown = json.dumps(decision)
    for text in (dev1, "README.md", dev2, "src/h.py"):
        assert text in shown, text
    assert r.state()["anchors"]["verifier_pass_sha"] is None


# @spec ROUTE-BUILD-036
def test_gate_human_code_approve_records_approvals_and_reverifies(r):
    paused, dev1, dev2 = gate_finds_human_code(r)
    assert pauses(paused, "human_code_approval")
    decision = r.user("approve")
    approvals = sorted(r.state()["approvals"], key=lambda a: a["paths"])
    assert all(a.get("id") for a in approvals)
    assert [{k: v for k, v in a.items() if k != "id"} for a in approvals] == [
        {"agent": "forge-dev", "paths": ["README.md"], "ranges": {"README.md": [[1, 1]]},
         "base_sha": git(r.wt, "rev-parse", f"{dev1}^"), "approved_by": "user"},
        {"agent": "forge-dev", "paths": ["src/h.py"], "ranges": {"src/h.py": [[2, 2]]},
         "base_sha": dev1, "approved_by": "user"},
    ]
    assert dispatches(decision, "forge-verifier", "VERIFY_FINAL")


# @spec ROUTE-BUILD-037
def test_gate_human_code_reject_stops(r):
    assert pauses(gate_finds_human_code(r)[0], "human_code_approval")
    assert stops(r.user("reject", notes="no"), "GATE_REJECTED")


# @spec ROUTE-BUILD-038
def test_gate_mixed_findings_stop_unrepairable(r):
    decision, _, _ = gate_finds_human_code(r, extra={"tests/test_x.py": "dev wrote a test\n"})
    assert stops(decision, "GATE_UNREPAIRABLE")
    assert "BOUNDARY_VIOLATION" in json.dumps(decision) and "HUMAN_CODE_MODIFIED" in json.dumps(decision)
    assert r.state()["anchors"]["verifier_pass_sha"] is None


# @spec ROUTE-BUILD-021
def test_review_approved(r):
    r.patch(anchors={"verifier_pass_sha": head(r.wt)})
    r.goto("REVIEW_INTERNAL")
    decision = r.result("REVIEW_APPROVED")
    assert pauses(decision, "publish_approval") and set(decision["options"]) == {"approve", "hold"}
    assert r.state()["anchors"]["reviewer_pass_sha"] == head(r.wt)


DEFAULT_PUBLISH_QUESTION = "Publish? Approve with option push or merge, or hold."


def review_after_merge_from_main(r, main_specs):
    """Merge main into the workstream branch (main gaining main_specs), then approve the review."""
    if main_specs is not None:
        commit(r.ws["repo"], {"docs/intent/b/b-specs.md": "# Specs\n\n" + "\n".join(main_specs) + "\n"}, "main spec")
    git(r.wt, "merge", "-q", "--no-ff", "main", "-m", "merge main")
    sha = head(r.wt)
    r.patch(anchors={"verifier_pass_sha": sha, "last_known_head_sha": sha})
    r.goto("REVIEW_INTERNAL")
    return r.result("REVIEW_APPROVED")


# @spec ROUTE-BUILD-039
def test_publish_approval_names_merged_specs(r):
    set_mode(r, repo_mode="LID")
    decision = review_after_merge_from_main(r, ["- [ ] **B-001**: from main.", "- [ ] **B-002**: also."])
    assert pauses(decision, "publish_approval")
    assert r.state()["lifecycle"]["awaiting"]["payload"]["merged_specs"] == ["B-001", "B-002"]
    assert decision["question"].startswith(DEFAULT_PUBLISH_QUESTION)
    assert "B-001" in decision["question"] and "B-002" in decision["question"]


def commit_unmarked_spec(r):
    """A workstream spec still `[ ]` that a test cites and code tags: lid_check lists it in unmarked_specs."""
    r.human_commit({"docs/intent/a/a-specs.md": "# Specs\n\n- [ ] **A-001**: tested and tagged.\n",
                    "tests/test_a.py": "# @" + "spec A-001\ndef test_a(): pass\n",
                    "src/a.py": "# @" + "spec A-001\ndef a(): pass\n"})


def publish_payload(r):
    return r.state()["lifecycle"]["awaiting"].get("payload") or {}


# @spec ROUTE-BUILD-040
@pytest.mark.parametrize("repo_mode, main_specs, unmarked", [
    ("naive", ["- [ ] **B-001**: from main."], False),
    ("naive", None, True),
    ("LID", None, False),
], ids=["naive-repo", "naive-unmarked", "nothing-listed"])
def test_publish_approval_unchanged_without_listed_specs(r, repo_mode, main_specs, unmarked):
    set_mode(r, repo_mode=repo_mode)
    if unmarked:
        commit_unmarked_spec(r)
    decision = review_after_merge_from_main(r, main_specs)
    assert pauses(decision, "publish_approval")
    assert decision["question"] == DEFAULT_PUBLISH_QUESTION
    assert not {"merged_specs", "unmarked_specs"} & set(publish_payload(r))


# @spec ROUTE-BUILD-040
def test_publish_approval_merged_only_has_no_unmarked_line(r):
    set_mode(r, repo_mode="LID")
    decision = review_after_merge_from_main(r, ["- [ ] **B-001**: from main."])
    assert "unmarked_specs" not in publish_payload(r)
    lines = decision["question"].splitlines()
    assert lines[0] == DEFAULT_PUBLISH_QUESTION and all("B-001" in line for line in lines[1:])


# @spec ROUTE-BUILD-042, ROUTE-BUILD-040
def test_publish_approval_names_unmarked_specs(r):
    set_mode(r, repo_mode="LID")
    commit_unmarked_spec(r)
    decision = review_after_merge_from_main(r, None)
    assert pauses(decision, "publish_approval")
    payload = publish_payload(r)
    assert payload.get("unmarked_specs") == ["A-001"] and "merged_specs" not in payload
    lines = decision["question"].splitlines()
    assert lines[0] == DEFAULT_PUBLISH_QUESTION and len(lines) > 1 and all("A-001" in line for line in lines[1:])


# @spec ROUTE-BUILD-042, ROUTE-BUILD-039
def test_publish_approval_names_merged_and_unmarked_specs(r):
    set_mode(r, repo_mode="LID")
    commit_unmarked_spec(r)
    decision = review_after_merge_from_main(r, ["- [ ] **B-001**: from main."])
    payload = publish_payload(r)
    assert (payload.get("merged_specs"), payload.get("unmarked_specs")) == (["B-001"], ["A-001"])
    assert "A-001" in decision["question"] and "B-001" in decision["question"]


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
def test_merge_blocked_stops(r):
    at_publish(r, "merge")
    decision = r.result("MERGE_BLOCKED", publish_target="merge", escalation={"details": "conflict in c.txt"})
    assert stops(decision, "MERGE_BLOCKED") and "c.txt" in decision["detail"]


def remove_origin(r):
    git(r.ws["repo"], "remote", "remove", "origin")


def refused_publish(r, target="push", details="push rejected: non-fast-forward"):
    at_publish(r, target)
    return r.result("PUBLISH_REFUSED", publish_target=target, escalation={"details": details})


# @spec ROUTE-BUILD-043
def test_publish_approval_without_origin_marks_no_origin(r):
    remove_origin(r)
    decision = at_publish_approval(r)
    assert pauses(decision, "publish_approval")
    assert publish_payload(r).get("no_origin") is True
    lines = decision["question"].splitlines()
    assert lines[0] == DEFAULT_PUBLISH_QUESTION and len(lines) > 1 and "push" in lines[1]


# @spec ROUTE-BUILD-043
def test_publish_approval_with_origin_leaves_no_origin_out(r):
    decision = at_publish_approval(r)
    assert "no_origin" not in publish_payload(r) and decision["question"] == DEFAULT_PUBLISH_QUESTION


# @spec ROUTE-BUILD-043, ROUTE-BUILD-022
def test_hold_recomputes_no_origin(r):
    remove_origin(r)
    at_publish_approval(r)
    set_origin(r.ws["repo"], "https://example.com/demo.git")
    decision = r.user("hold")
    assert pauses(decision, "publish_approval")
    assert "no_origin" not in publish_payload(r) and decision["question"] == DEFAULT_PUBLISH_QUESTION


# @spec ROUTE-BUILD-043, ROUTE-BUILD-022
def test_hold_marks_no_origin_after_origin_removed(r):
    at_publish_approval(r)
    remove_origin(r)
    decision = r.user("hold")
    assert pauses(decision, "publish_approval") and publish_payload(r).get("no_origin") is True


# @spec ROUTE-BUILD-044
def test_push_at_no_origin_pause_is_invalid(r):
    remove_origin(r)
    at_publish_approval(r)
    decision = r.user("approve", option="push")
    assert stops(decision, "EVENT_INVALID") and "push needs an origin remote" in decision["detail"]
    assert r.state()["lifecycle"]["awaiting"]["kind"] == "publish_approval"


# @spec ROUTE-BUILD-044
def test_push_at_no_origin_pause_decides_from_stored_payload(r):
    remove_origin(r)
    at_publish_approval(r)
    set_origin(r.ws["repo"], "https://example.com/demo.git")
    assert stops(r.user("approve", option="push"), "EVENT_INVALID")


# @spec ROUTE-BUILD-044, ROUTE-BUILD-022
def test_merge_at_no_origin_pause_dispatches_publisher(r):
    remove_origin(r)
    at_publish_approval(r)
    assert dispatches(r.user("approve", option="merge"), "forge-publisher", "merge")


# @spec ROUTE-BUILD-045
def test_publish_refused_returns_to_publish_approval(r):
    decision = refused_publish(r)
    assert pauses(decision, "publish_approval")
    assert publish_payload(r).get("refused") == "push rejected: non-fast-forward"
    assert "push rejected: non-fast-forward" in decision["question"]


# @spec ROUTE-BUILD-045, ROUTE-BUILD-043
def test_publish_refused_without_origin_offers_merge_only_again(r):
    remove_origin(r)
    at_publish_approval(r)
    r.user("approve", option="merge")
    decision = r.result("PUBLISH_REFUSED", publish_target="merge", escalation={"details": "no origin"})
    assert pauses(decision, "publish_approval") and publish_payload(r).get("no_origin") is True
    assert dispatches(r.user("approve", option="merge"), "forge-publisher", "merge")


# @spec ROUTE-BUILD-046
def test_hold_after_refusal_drops_refused(r):
    refused_publish(r)
    decision = r.user("hold")
    assert pauses(decision, "publish_approval")
    assert "refused" not in publish_payload(r) and decision["question"] == DEFAULT_PUBLISH_QUESTION


# @spec ROUTE-BUILD-039, ROUTE-BUILD-042, ROUTE-BUILD-045
def test_refused_pause_lists_merged_and_unmarked_specs_again(r):
    set_mode(r, repo_mode="LID")
    commit_unmarked_spec(r)
    review_after_merge_from_main(r, ["- [ ] **B-001**: from main."])
    r.user("approve", option="push")
    decision = r.result("PUBLISH_REFUSED", publish_target="push", escalation={"details": "rejected"})
    payload = publish_payload(r)
    assert (payload.get("merged_specs"), payload.get("unmarked_specs")) == (["B-001"], ["A-001"])
    assert "A-001" in decision["question"] and "B-001" in decision["question"] and "rejected" in decision["question"]


def at_report(r):
    at_publish(r, "push")
    return r.result("PUBLISHED", publish_target="push")


# @spec ROUTE-BUILD-027
def test_report_draft_approve_and_revise(r):
    at_report(r)
    draft = dispatched_id(r)
    paused = r.result("REPORT_DRAFTED")
    assert pauses(paused, "report_approval")
    assert paused["context_refs"] == [envelope_file(r, draft)]
    revised = r.user("revise", notes="shorter")
    assert dispatches(revised, "forge-reporter", "draft") and "shorter" in json.dumps(revised["invoke"]["notes"])
    r.result("REPORT_DRAFTED")
    assert dispatches(r.user("approve"), "forge-reporter", "send")


# @spec ROUTE-BUILD-033
def test_report_draft_reads_published_envelope(r):
    at_publish(r, "push")
    publisher = dispatched_id(r)
    decision = r.result("PUBLISHED", publish_target="push")
    assert dispatches(decision, "forge-reporter", "draft")
    assert decision["invoke"]["context_refs"] == [envelope_file(r, publisher)]


# @spec ROUTE-BUILD-033
def test_pr_report_draft_reads_review_envelope(pr):
    pr.start()
    pr.result("OK")
    reviewer = dispatched_id(pr)
    decision = pr.result("PR_REVIEW_READY")
    assert decision["invoke"]["context_refs"] == [envelope_file(pr, reviewer)]


# @spec ROUTE-BUILD-033, ROUTE-DISP-008
@pytest.mark.parametrize("answer, mode", [("approve", "send"), ("revise", "draft")])
def test_report_redispatch_reads_latest_draft(r, answer, mode):
    at_report(r)
    draft = dispatched_id(r)
    r.result("REPORT_DRAFTED")
    decision = r.user(answer, **({"notes": "shorter"} if answer == "revise" else {}))
    assert dispatches(decision, "forge-reporter", mode)
    assert decision["invoke"]["context_refs"] == [envelope_file(r, draft)]


# @spec ROUTE-BUILD-034
@pytest.mark.parametrize("phase", ["QA_SKEL", "QA_FULL", "REPAIR"])
def test_qa_contract_mismatch_raises_blocker(r, phase):
    place(r, phase, "forge-qa")
    decision = r.result("CONTRACT_MISMATCH", escalation={"details": "spec says int, stub returns str"})
    [blocker] = r.state()["blockers"]
    assert (blocker["kind"], blocker["agent"], blocker["phase"]) == ("CONTRACT_MISMATCH", "forge-qa", phase)
    assert dispatches(decision, "forge-council", "CONTRACT_MISMATCH")


# @spec ROUTE-BUILD-028
def test_report_sent_goes_to_ci_watch(r):
    at_report(r)
    r.result("REPORT_DRAFTED")
    r.user("approve")
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
    r.goto("RECONCILE", after_reconcile="QA_FULL", resume_phase="VERIFY_FINAL")
    decision = r.result("PASS", scope={"completed": ["a/**"], "remaining": ["src/**"]})
    assert dispatches(decision, "forge-qa", "full")
    state = r.state()
    assert (state["scope_manifest"]["completed"], state["scope_manifest"]["remaining"]) == (["a/**"], ["src/**"])
    assert state["lifecycle"]["after_reconcile"] is None


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
    ("CONTRACT_MISMATCH", "QA_FULL", "forge-qa", "QA_FULL", ["senior-dev", "architect"]),
    ("AUDIT_FAILED", "POST_PLAN", "forge-verifier", "POST_PLAN", ["architect", "senior-dev"]),
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


# @spec ROUTE-BLK-021
def test_council_context_follows_blocked_dispatch(r):
    r.patch(identity={"issue": "https://github.com/acme/widgets/issues/1"})
    r.start()
    blocked = r.result("OK")["invoke"]["context_refs"]
    decision = r.result("CONCERNS", escalation={"details": "the issue is ambiguous"})
    blocker_file = str(r.sd / "council" / f"{r.state()['blockers'][0]['id']}.json")
    assert dispatches(decision, "forge-council", "CONCERNS")
    assert decision["invoke"]["context_refs"] == [blocker_file, *blocked]


DELTA_A = "change the assertion in test_a"
DELTA_B = "change the code in a.py"


def ruling(route_to=(), requires_user=False, panel=("debugger", "senior-dev"), alt_route=("forge-dev",)):
    """A ruling whose recommended option A routes to route_to; option B routes to alt_route."""
    options = [{"label": "A", "summary": "recommended", "route_to": list(route_to), "delta": DELTA_A},
               {"label": "B", "summary": "alternative", "route_to": list(alt_route), "delta": DELTA_B}]
    return {"matrix_ref": "council/matrix.md", "requires_user": requires_user, "panel": list(panel),
            "options": options, "recommended": "A", "route_to": list(route_to), "delta": DELTA_A}


def approve_ruling(r, shown):
    """Return the ruling, then approve its recommended option A at council_signoff (paused in mode collab)."""
    assert pauses(r.result("COUNCIL_RULING", ruling=shown), "council_signoff")
    return r.user("approve", option="A")


# @spec ROUTE-BLK-005
def test_concerns_ruling_always_needs_signoff(r):
    set_mode(r, mode="delivery")
    raise_blocker(r, "CONCERNS", "PLAN")
    assert pauses(r.result("COUNCIL_RULING", ruling=ruling()), "council_signoff")


# @spec ROUTE-BLK-006
@pytest.mark.parametrize("mode, requires_user, paused",
                         [("delivery", False, False), ("delivery", True, True)]
                         + [(m, False, True) for m in MODES if m != "delivery"])
def test_auto_resolve(r, mode, requires_user, paused):
    set_mode(r, mode=mode)
    raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    decision = r.result("COUNCIL_RULING", ruling=ruling(["forge-dev"], requires_user))
    if paused:
        assert pauses(decision, "council_signoff")
    else:
        assert dispatches(decision, "forge-dev", "repair")


# @spec ROUTE-BLK-006
def test_auto_resolve_records_decision(r):
    set_mode(r, mode="delivery")
    raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    blocker_id = r.state()["blockers"][0]["id"]
    r.result("COUNCIL_RULING", ruling=ruling(["forge-dev"]))
    [entry] = r.state()["council_decisions"]
    assert entry.get("id")
    assert {k: v for k, v in entry.items() if k != "id"} == {
        "blocker_id": blocker_id, "panel": ["debugger", "senior-dev"], "option": "A", "approved_by": "auto"}


# @spec ROUTE-BLK-007, ROUTE-BLK-018
def test_council_signoff_approve(r):
    raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    shown = ruling(["forge-qa"], panel=["debugger"], alt_route=["forge-dev"])
    r.result("COUNCIL_RULING", ruling=shown)
    blocker_id = r.state()["blockers"][0]["id"]
    assert r.state()["blockers"][0]["ruling"] == shown
    decision = r.user("approve", option="B")
    [entry] = r.state()["council_decisions"]
    assert (entry["blocker_id"], entry["option"], entry["approved_by"]) == (blocker_id, "B", "user")
    assert entry["panel"] == ["debugger"] and "id" in entry  # the ruling's panel, the personas convened
    assert dispatches(decision, "forge-dev", "repair")  # option B's route_to, not the recommended one
    assert decision["invoke"]["notes"] == DELTA_B
    assert [i["agent"] for i in r.state()["lifecycle"]["repair_queue"]] == ["forge-dev"]


# @spec ROUTE-BLK-022
def test_council_signoff_unknown_option(r):
    raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    r.result("COUNCIL_RULING", ruling=ruling(["forge-dev"]))
    unchanged_stop(r, lambda: r.user("approve", option="Z"), "EVENT_INVALID")


# @spec ROUTE-BLK-008
def test_council_signoff_reject(r):
    raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    r.result("COUNCIL_RULING", ruling=ruling(["forge-dev"]))
    decision = r.user("reject", notes="consider flaky fixture")
    assert dispatches(decision, "forge-council", "TESTS_FAILED")
    assert "consider flaky fixture" in json.dumps(decision["invoke"]["notes"])


# @spec ROUTE-BLK-009
def test_concerns_ruling_redispatches_planning(r):
    r.patch(identity={"issue": "https://github.com/acme/widgets/issues/1"})
    r.start()
    blocked = r.result("OK")["invoke"]["context_refs"]
    r.result("CONCERNS", escalation={"details": "CONCERNS details"})
    r.result("COUNCIL_RULING", ruling=ruling())
    decision = r.user("approve", option="B")
    assert dispatches(decision, "forge-planner", "naive") and decision["phase"] == "PLAN"
    assert decision["invoke"]["context_refs"] == [*blocked, "council/matrix.md"]
    assert DELTA_B in json.dumps(decision["invoke"]["notes"])


# @spec ROUTE-BLK-017
def test_concerns_ruling_removes_blocker(r):
    raise_blocker(r, "CONCERNS", "PLAN")
    r.result("COUNCIL_RULING", ruling=ruling())
    r.user("approve", option="A")
    assert r.state()["blockers"] == []


# @spec ROUTE-BLK-010
@pytest.mark.parametrize("kind", ["TEST_CHANGE_REQUESTED", "CONTRACT_MISMATCH"])
def test_dev_blocker_ruling_queues_route_to(r, kind):
    raise_blocker(r, kind, "DEV_FULL")
    decision = approve_ruling(r, ruling(["forge-dev", "forge-qa"]))
    assert dispatches(decision, "forge-dev", "repair") and decision["phase"] == "REPAIR"
    assert [item["agent"] for item in r.state()["lifecycle"]["repair_queue"]] == ["forge-dev", "forge-qa"]


# @spec ROUTE-BLK-023
@pytest.mark.parametrize("kind", ["TEST_CHANGE_REQUESTED", "CONTRACT_MISMATCH"])
def test_dev_blocker_empty_route_reruns_blocked_phase(r, kind):
    raise_blocker(r, kind, "DEV_FULL")
    decision = approve_ruling(r, ruling([]))
    assert dispatches(decision, "forge-dev", "full") and decision["phase"] == "DEV_FULL"
    assert DELTA_A in json.dumps(decision["invoke"]["notes"])
    assert r.state()["blockers"] == []


# @spec ROUTE-BLK-023
def test_repair_blocker_empty_route_reruns_interrupted_item(r):
    place(r, "REPAIR", "forge-qa")
    r.result("CONTRACT_MISMATCH", escalation={"details": "contract details"})
    decision = approve_ruling(r, ruling([]))
    assert dispatches(decision, "forge-qa", "repair") and decision["phase"] == "REPAIR"
    notes = json.dumps(decision["invoke"]["notes"])
    assert "fix" in notes and DELTA_A in notes
    assert r.state()["lifecycle"]["repair_queue"][0]["agent"] == "forge-qa"
    assert len(r.state()["lifecycle"]["repair_queue"]) == 1
    assert r.state()["blockers"] == []


# @spec ROUTE-BLK-026
def test_queued_repair_carries_delta_and_blocked_context(r):
    r.goto("DEV_FULL")
    with_context_refs(r, ["specs/dev.md"])
    r.result("TEST_CHANGE_REQUESTED", escalation={"details": "test_a asserts the wrong value"})
    approve_ruling(r, ruling(["forge-qa"]))
    [item] = r.state()["lifecycle"]["repair_queue"]
    assert item["notes"] == DELTA_A
    assert item["context_refs"] == ["council/matrix.md", "specs/dev.md"]


# @spec ROUTE-BLK-011
@pytest.mark.parametrize("kind, phase", [("AUDIT_FAILED", "POST_QA"), ("TESTS_FAILED", "VERIFY_FINAL"),
                                         ("CI_FAILED", "CI_WATCH")])
def test_ruling_queues_route_to_in_order(r, kind, phase):
    raise_blocker(r, kind, phase)
    decision = approve_ruling(r, ruling(["forge-dev", "forge-qa"]))
    assert dispatches(decision, "forge-dev", "repair")
    assert [item["agent"] for item in r.state()["lifecycle"]["repair_queue"]] == ["forge-dev", "forge-qa"]


# @spec ROUTE-BLK-020
def test_repair_items_name_their_blocker(r):
    raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    blocker_id = r.state()["blockers"][0]["id"]
    approve_ruling(r, ruling(["forge-qa", "forge-dev"]))
    assert {item["blocker_id"] for item in r.state()["lifecycle"]["repair_queue"]} == {blocker_id}


# @spec ROUTE-BLK-019
@pytest.mark.parametrize("kind, phase", [("AUDIT_FAILED", "POST_QA"), ("TESTS_FAILED", "VERIFY_FINAL")])
def test_empty_route_resumes_failed_phase(r, kind, phase):
    raise_blocker(r, kind, phase)
    decision = approve_ruling(r, ruling([]))
    assert dispatches(decision, "forge-verifier", phase)
    assert r.state()["blockers"] == []


# @spec ROUTE-BLK-012
def test_flaky_ci_reruns(r):
    raise_blocker(r, "CI_FAILED", "CI_WATCH")
    decision = approve_ruling(r, ruling([]))
    assert dispatches(decision, "forge-scout", "CI_RERUN") and decision["phase"] == "CI_WATCH"
    assert r.state()["blockers"] == []


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


# @spec ROUTE-BLK-016, ROUTE-BLK-020
def test_repair_queue_drains_then_resumes(r):
    raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    approve_ruling(r, ruling(["forge-qa", "forge-dev"]))
    r.spoke_commit({"tests/test_a.py": "fixed\n"})
    second = r.result("REPAIR_COMMITTED")
    assert dispatches(second, "forge-dev", "repair")
    assert [i["agent"] for i in r.state()["lifecycle"]["repair_queue"]] == ["forge-dev"]
    r.spoke_commit({"src/a.py": "fixed\n"})
    resumed = r.result("REPAIR_COMMITTED")
    assert dispatches(resumed, "forge-verifier", "VERIFY_FINAL")
    assert r.state()["blockers"] == [] and r.state()["lifecycle"]["repair_queue"] == []


def blocker(blocker_id, kind, agent, phase):
    return {"id": blocker_id, "kind": kind, "agent": agent, "phase": phase, "details": f"{kind} details"}


def item(agent, notes, blocker_id, context_refs=()):
    return {"agent": agent, "notes": notes, "context_refs": list(context_refs), "blocker_id": blocker_id}


# @spec ROUTE-BLK-020
def test_completed_repair_removes_only_its_own_blocker(r):
    r.patch(blockers=[blocker("blk-outer", "TESTS_FAILED", "forge-verifier", "VERIFY_FINAL"),
                      blocker("blk-inner", "CONTRACT_MISMATCH", "forge-qa", "REPAIR")])
    r.goto("REPAIR", repair_queue=[item("forge-dev", "inner fix", "blk-inner"),
                                   item("forge-qa", "outer fix", "blk-outer")], resume_phase="VERIFY_FINAL")
    r.spoke_commit({"src/a.py": "fixed\n"})
    assert dispatches(r.result("REPAIR_COMMITTED"), "forge-qa", "repair")
    assert [b["id"] for b in r.state()["blockers"]] == ["blk-outer"]


# @spec ROUTE-BLK-025
def test_blocker_in_repair_queues_its_repairs_first(r):
    r.patch(blockers=[blocker("blk-outer", "TESTS_FAILED", "forge-verifier", "VERIFY_FINAL")])
    r.goto("REPAIR", repair_queue=[item("forge-qa", "outer qa", "blk-outer"),
                                   item("forge-dev", "outer dev", "blk-outer")], resume_phase="VERIFY_FINAL")
    raised = r.result("CONTRACT_MISMATCH", escalation={"details": "the spec and the stub disagree"})
    assert dispatches(raised, "forge-council", "CONTRACT_MISMATCH")
    lc = r.state()["lifecycle"]
    assert lc["resume_phase"] == "VERIFY_FINAL"
    assert [i["notes"] for i in lc["repair_queue"]] == ["outer qa", "outer dev"]
    decision = approve_ruling(r, ruling(["forge-dev"]))
    assert dispatches(decision, "forge-dev", "repair")
    assert [i["notes"] for i in r.state()["lifecycle"]["repair_queue"]] == [DELTA_A, "outer qa", "outer dev"]
    assert r.state()["lifecycle"]["resume_phase"] == "VERIFY_FINAL"


# @spec ROUTE-BLK-024
@pytest.mark.parametrize("assigned, completed, remaining", [(["a/**", "c/**"], ["a/**"], ["c/**"]),
                                                            ([], [], [])])
def test_planner_repair_replaces_scope(r, assigned, completed, remaining):
    r.patch(scope_manifest={"assigned": ["a/**", "b/**"], "completed": ["a/**", "b/**"], "remaining": []},
            blockers=[blocker("blk-1", "CHANGES_REQUESTED", "forge-reviewer", "REVIEW_INTERNAL")])
    r.goto("REPAIR", repair_queue=[item("forge-planner", "rework the plan", "blk-1"),
                                   item("forge-dev", "fix the code", "blk-1")], resume_phase="VERIFY_FINAL")
    r.spoke_commit({"docs/plan.md": "revised plan\n"})
    assert dispatches(r.result("REPAIR_COMMITTED", scope={"assigned": assigned}), "forge-dev", "repair")
    state = r.state()
    assert state["scope_manifest"] == {"assigned": assigned, "completed": completed, "remaining": remaining}
    assert state["lifecycle"]["resume_phase"] == "POST_PLAN"


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


# @spec ROUTE-HC-001
@pytest.mark.parametrize("phase, queue_agent", [("QA_SKEL", None), ("QA_FULL", None), ("DEV_STUB", None),
                                                ("REPAIR", "forge-qa"), ("REPAIR", "forge-dev")])
def test_human_code_request_pauses_in_every_builder_phase(r, phase, queue_agent):
    place(r, phase, queue_agent)
    decision = r.result("HUMAN_CODE_CHANGE_REQUESTED", escalation=ESCALATION)
    assert pauses(decision, "human_code_approval")
    assert "src/legacy.py" in json.dumps(decision) and "14" in json.dumps(decision)


def in_order(notes, *parts):
    text = json.dumps(notes)
    positions = [text.find(p) for p in parts]
    return -1 not in positions and positions == sorted(positions)


def escalate_in_repair(r):
    r.goto("REPAIR", repair_queue=[item("forge-dev", "fix the parser", None, ["x.md"])], resume_phase="VERIFY_FINAL")
    r.result("HUMAN_CODE_CHANGE_REQUESTED", escalation=ESCALATION)


# @spec ROUTE-HC-002
def test_human_code_approve_keeps_replaced_notes(r):
    escalate_in_repair(r)
    decision = r.user("approve")
    [approval] = r.state()["approvals"]
    assert dispatches(decision, "forge-dev", "repair")
    assert in_order(decision["invoke"]["notes"], "fix the parser", approval["id"]), decision["invoke"]["notes"]


# @spec ROUTE-HC-003
def test_human_code_reject_keeps_replaced_notes(r):
    escalate_in_repair(r)
    decision = r.user("reject", notes="work around it")
    assert dispatches(decision, "forge-dev", "repair")
    assert in_order(decision["invoke"]["notes"], "fix the parser", "work around it"), decision["invoke"]["notes"]


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
    assert state["lifecycle"].get("after_reconcile") == "DEV_FULL"
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


# @spec ROUTE-DRIFT-002
@pytest.mark.parametrize("phase", ["REPORT", "CI_WATCH"])
def test_drift_after_publishing_reconciles_to_verify_final(r, phase):
    r.patch(anchors={"last_known_head_sha": head(r.wt)}, lifecycle={"current_phase": phase})
    r.human_commit({"src/late.py": "late\n"})
    assert dispatches(r.run(), "forge-verifier", "RECONCILE")
    assert r.state()["lifecycle"].get("after_reconcile") == "VERIFY_FINAL"


# @spec ROUTE-DRIFT-009
def test_pr_review_drift_stops_with_worktree_moved(pr):
    pr.start()
    pr.result("OK")
    pr.human_commit({"src/h.py": "h\n"})
    unchanged_stop(pr, pr.run, "WORKTREE_MOVED")


# @spec ROUTE-DRIFT-010
def test_drift_leaves_resume_phase(r):
    r.goto("REPAIR", repair_queue=[item("forge-dev", "fix", None)], resume_phase="VERIFY_FINAL")
    r.human_commit({"src/h.py": "h\n"})
    assert dispatches(r.run(), "forge-verifier", "RECONCILE")
    assert r.state()["lifecycle"]["resume_phase"] == "VERIFY_FINAL"


# @spec ROUTE-DRIFT-007
def test_drift_at_close_reconciles(r):
    r.goto("CI_WATCH")
    r.human_commit({"src/late.py": "late\n"})
    decision = r.result("CI_GREEN", head_sha=r.state()["lifecycle"]["dispatched"]["base_sha"])
    assert dispatches(decision, "forge-verifier", "RECONCILE")
    assert r.state()["lifecycle"].get("after_reconcile") == "VERIFY_FINAL"


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
    r.patch(scope_manifest={"assigned": ["src/**"], "completed": [], "remaining": ["src/**"]})
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


# ------------------------------------------------------------- idea and triage flows

MODE_OF = {"idea": "IDEA", "triage": "TRIAGE"}
ISSUE_URL = "https://example.com/local/project/issues/5"
TRIAGE_ISSUE = "https://github.com/acme/widgets/issues/9"


@pytest.fixture(params=["idea", "triage"])
def study(r, request):
    ident = {"flow": request.param}
    if request.param == "triage":
        ident["issue"] = TRIAGE_ISSUE
    r.patch(identity=ident)
    r.flow = request.param
    return r


@pytest.fixture
def idea(r):
    r.patch(identity={"flow": "idea"})
    return r


def proposal_path(r):
    return r.sd / "proposal.md"


def write_proposal(r, text="# proposal\n"):
    """Write proposal.md and push its mtime a second past any earlier value."""
    path = proposal_path(r)
    path.write_text(text)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    return path.stat().st_mtime_ns


def at_propose(r):
    r.start()
    return r.result("OK")


def at_report_draft(r):
    at_propose(r)
    write_proposal(r)
    return r.result("PROPOSAL_READY")


def at_report_approval(r):
    at_report_draft(r)
    return r.result("REPORT_DRAFTED")


def at_report_send(r):
    at_report_approval(r)
    return r.user("approve")


def at_promotion(r, url=ISSUE_URL):
    at_report_send(r)
    return r.result("REPORT_SENT", report={"url": url})


def propose_ruling():
    return ruling([], alt_route=["forge-planner"])


def at_propose_council_signoff(r):
    blocked = at_propose(r)["invoke"]["context_refs"]
    r.result("CONCERNS", escalation={"details": "open question"})
    assert pauses(r.result("COUNCIL_RULING", ruling=propose_ruling()), "council_signoff")
    return blocked


# @spec ROUTE-REDO-005
def test_redo_005_done_with_promote_returns_close_again(study):
    promote = {"issue": ISSUE_URL, "proposal": str(proposal_path(study))}
    study.patch(lifecycle={"current_phase": "DONE", "promote": promote})
    before = study.sp.read_bytes()
    decision = study.run()
    assert decision["action"] == "close" and decision["promote"] == promote
    assert study.sp.read_bytes() == before


# @spec ROUTE-ENV-017
def test_env_017_guard_inactive_detail_reports_head_and_porcelain_in_idea_triage(study):
    study.start()
    base = study.state()["lifecycle"]["dispatched"]["base_sha"]
    Path(study.wt, "wip.txt").write_text("wip\n")
    detail = unguarded_result(study, status="OK")["detail"]
    assert "wip.txt" in detail and base in detail
    moved = study.human_commit({"other.txt": "o\n"})
    detail = unguarded_result(study, status="OK")["detail"]
    assert moved in detail and base in detail


# @spec ROUTE-ENV-017
def test_env_017_build_flow_detail_has_no_porcelain(r):
    r.start()
    Path(r.wt, "wip.txt").write_text("wip\n")
    assert "wip.txt" not in unguarded_result(r)["detail"]


# @spec ROUTE-DRIFT-011
def test_drift_011_dirty_tree_offers_only_stash_in_idea_triage(study):
    Path(study.wt, "wip.txt").write_text("wip\n")
    decision = study.goto("PROPOSE")
    assert pauses(decision, "dirty_tree") and set(decision["options"]) == {"stash"}


# @spec ROUTE-IDEA-001
def test_idea_001_idea_triage_start_dispatches_scout_idea_or_triage_mode(study):
    assert dispatches(study.start(), "forge-scout", MODE_OF[study.flow])
    assert study.state()["lifecycle"]["current_phase"] == "RECON"


# @spec ROUTE-IDEA-002
def test_idea_002_idea_triage_recon_ok_dispatches_propose_with_scout_dir(study):
    study.start()
    decision = study.result("OK")
    assert dispatches(decision, "forge-planner", "propose")
    assert str(study.sd / "scout") in [c.rstrip("/") for c in decision["invoke"]["context_refs"]]
    assert study.state()["lifecycle"]["current_phase"] == "PROPOSE"


# @spec ROUTE-IDEA-003
def test_idea_003_propose_dispatch_sets_propose_context_refs(study):
    decision = at_propose(study)
    assert study.state()["lifecycle"]["propose_context_refs"] == decision["invoke"]["context_refs"]


# @spec ROUTE-IDEA-004
def test_idea_004_proposal_ready_goes_to_report_draft(study):
    assert dispatches(at_report_draft(study), "forge-reporter", "draft")
    assert study.state()["lifecycle"]["current_phase"] == "REPORT"


# @spec ROUTE-IDEA-005
def test_idea_005_report_draft_context_refs_report_input_then_proposal(study):
    at_propose(study)
    write_proposal(study)
    planner = dispatched_id(study)
    decision = study.result("PROPOSAL_READY")
    assert decision["invoke"]["context_refs"] == [envelope_file(study, planner), str(proposal_path(study))]
    study.result("REPORT_DRAFTED")
    drafted = study.state()["lifecycle"]["report_input"]
    revised = study.user("revise", notes="shorter")
    assert dispatches(revised, "forge-reporter", "draft")
    assert revised["invoke"]["context_refs"] == [drafted, str(proposal_path(study))]


# @spec ROUTE-IDEA-006
def test_idea_006_report_send_context_refs_only_report_input(study):
    at_report_approval(study)
    report_input = study.state()["lifecycle"]["report_input"]
    decision = study.user("approve")
    assert dispatches(decision, "forge-reporter", "send")
    assert decision["invoke"]["context_refs"] == [report_input]


# @spec ROUTE-IDEA-007
def test_idea_007_report_approval_options_approve_revise_replan(study):
    assert set(at_report_approval(study)["options"]) == {"approve", "revise", "replan"}


# @spec ROUTE-IDEA-009
def test_idea_009_report_approval_replan_redispatches_propose(study):
    propose = at_propose(study)["invoke"]["context_refs"]
    write_proposal(study)
    study.result("PROPOSAL_READY")
    study.result("REPORT_DRAFTED")
    report_input = study.state()["lifecycle"]["report_input"]
    decision = study.user("replan", notes="cover the cache")
    assert dispatches(decision, "forge-planner", "propose")
    assert "cover the cache" in json.dumps(decision["invoke"]["notes"])
    assert decision["invoke"]["context_refs"] == [*propose, str(proposal_path(study)), report_input]
    assert study.state()["lifecycle"]["current_phase"] == "PROPOSE"


# @spec ROUTE-IDEA-009
def test_idea_009_replan_leaves_out_refs_already_held(study):
    at_report_approval(study)
    report_input = study.state()["lifecycle"]["report_input"]
    held = [str(study.sd / "scout"), str(proposal_path(study)), report_input]
    study.patch(lifecycle={"propose_context_refs": held})
    decision = study.user("replan")
    assert decision["invoke"]["context_refs"] == held


# @spec ROUTE-IDEA-010
def test_idea_010_report_sent_sets_report_url_and_pauses_promotion(study):
    decision = at_promotion(study)
    assert pauses(decision, "promotion") and set(decision["options"]) == {"promote", "close"}
    assert ISSUE_URL in decision["question"]
    assert study.state()["lifecycle"]["report_url"] == ISSUE_URL


# @spec ROUTE-IDEA-011
def test_idea_011_promotion_promote_sets_promote_and_closes(study):
    at_promotion(study)
    decision = study.user("promote")
    issue = ISSUE_URL if study.flow == "idea" else TRIAGE_ISSUE
    expected = {"issue": issue, "proposal": str(proposal_path(study))}
    assert decision["action"] == "close" and decision["promote"] == expected
    assert study.state()["lifecycle"]["promote"] == expected


# @spec ROUTE-IDEA-012
def test_idea_012_promotion_close_closes_without_promote(study):
    at_promotion(study)
    decision = study.user("close")
    assert decision["action"] == "close" and "promote" not in decision
    assert study.state()["lifecycle"].get("promote") is None


# @spec ROUTE-IDEA-013
def test_idea_013_council_signoff_options_approve_reject_leave_open(study):
    at_propose(study)
    study.result("CONCERNS", escalation={"details": "open question"})
    decision = study.result("COUNCIL_RULING", ruling=propose_ruling())
    assert pauses(decision, "council_signoff") and set(decision["options"]) == {"approve", "reject", "leave_open"}


# @spec ROUTE-IDEA-008
def test_idea_008_build_report_approval_options_approve_revise_skip(r):
    at_report(r)
    assert set(r.result("REPORT_DRAFTED")["options"]) == {"approve", "revise", "skip"}


# @spec ROUTE-IDEA-008
def test_idea_008_pr_review_report_approval_options_approve_revise_skip(pr):
    pr.start()
    pr.result("OK")
    pr.result("PR_REVIEW_READY")
    assert set(pr.result("REPORT_DRAFTED")["options"]) == {"approve", "revise", "skip"}


# @spec ROUTE-IDEA-014
def test_idea_014_build_council_signoff_options_approve_reject(r):
    raise_blocker(r, "TESTS_FAILED", "VERIFY_FINAL")
    decision = r.result("COUNCIL_RULING", ruling=ruling(["forge-dev"]))
    assert pauses(decision, "council_signoff") and set(decision["options"]) == {"approve", "reject"}


# @spec ROUTE-IDEA-015
def test_idea_015_leave_open_records_council_decision_and_removes_blocker(study):
    at_propose_council_signoff(study)
    blocker_id = study.state()["blockers"][0]["id"]
    study.user("leave_open")
    state = study.state()
    assert state["blockers"] == []
    [entry] = state["council_decisions"]
    assert entry.get("id")
    assert {k: v for k, v in entry.items() if k != "id"} == {
        "blocker_id": blocker_id, "panel": ["debugger", "senior-dev"], "option": None,
        "left_open": True, "approved_by": "user"}


# @spec ROUTE-IDEA-016
def test_idea_016_leave_open_redispatches_propose_with_matrix_ref_and_notes(study):
    blocked = at_propose_council_signoff(study)
    decision = study.user("leave_open", notes="keep both designs")
    assert dispatches(decision, "forge-planner", "propose")
    assert decision["invoke"]["context_refs"] == [*blocked, "council/matrix.md"]
    notes = json.dumps(decision["invoke"]["notes"])
    assert "keep both designs" in notes and "recommend" in notes.lower()


# @spec ROUTE-IDEA-017
@pytest.mark.parametrize("bad", [
    ruling(["forge-dev"], alt_route=["forge-planner"]),
    ruling([], alt_route=["forge-qa"]),
    ruling(["forge-planner", "forge-dev"], alt_route=[]),
], ids=["route_to", "option", "mixed"])
def test_idea_017_propose_council_route_to_other_than_planner_is_envelope_invalid(study, bad):
    at_propose(study)
    study.result("CONCERNS", escalation={"details": "open question"})
    unchanged_stop(study, lambda: study.result("COUNCIL_RULING", ruling=bad))


# @spec ROUTE-IDEA-017
@pytest.mark.parametrize("good", [propose_ruling(), ruling(["forge-planner"], alt_route=[])], ids=["empty", "planner"])
def test_idea_017_propose_council_planner_or_empty_route_accepted(study, good):
    at_propose(study)
    study.result("CONCERNS", escalation={"details": "open question"})
    assert pauses(study.result("COUNCIL_RULING", ruling=good), "council_signoff")


# @spec ROUTE-IDEA-018
def test_idea_018_idea_triage_result_with_moved_head_is_worktree_moved(study):
    study.start()
    study.human_commit({"h.txt": "h\n"})
    unchanged_stop(study, lambda: study.result("OK"), "WORKTREE_MOVED")


# @spec ROUTE-IDEA-018
def test_idea_018_guard_check_comes_first(study):
    study.start()
    study.human_commit({"h.txt": "h\n"})
    unchanged_stop(study, lambda: unguarded_result(study, status="OK"), "GUARD_INACTIVE")


# @spec ROUTE-IDEA-019
@pytest.mark.parametrize("content", [None, "", " \n\t\n"], ids=["missing", "empty", "blank"])
def test_idea_019_proposal_ready_missing_or_blank_proposal_is_envelope_invalid(study, content):
    at_propose(study)
    if content is not None:
        write_proposal(study, content)
    unchanged_stop(study, lambda: study.result("PROPOSAL_READY"))


# @spec ROUTE-IDEA-020
def test_idea_020_proposal_ready_with_files_touched_is_envelope_invalid(study):
    at_propose(study)
    write_proposal(study)
    unchanged_stop(study, lambda: study.result("PROPOSAL_READY", files_touched=["src/a.py"]))


# @spec ROUTE-IDEA-021
@pytest.mark.parametrize("fields", [{}, {"report": {}}, {"report": {"url": ""}}, {"report": {"url": 5}},
                                    {"report": "https://x"}],
                         ids=["no-report", "no-url", "empty", "non-string", "not-object"])
def test_idea_021_report_sent_without_url_is_envelope_invalid(study, fields):
    at_report_send(study)
    unchanged_stop(study, lambda: study.result("REPORT_SENT", **fields))


# @spec ROUTE-IDEA-022
@pytest.mark.parametrize("url", [
    "https://example.com/local/project/pull/5",
    "https://example.com/local/project/issues/",
    "https://example.com/local/project/issues/x5",
    "https://example.com/local/project/issues/5/",
    "https://example.com/local/project/issues/5#c1",
    "https://example.com/project/issues/5",
    "https://example.com/other/project/issues/5",
    "https://example.com/local/other/issues/5",
    "https://example.com//project/issues/5",
    "example.com/local/project/issues/5",
    "https://example.com/group/sub/local/project/issues/12",
], ids=lambda u: u.replace("/", "_"))
def test_idea_022_idea_report_url_not_matching_repo_issue_is_envelope_invalid(idea, url):
    at_report_send(idea)
    unchanged_stop(idea, lambda: idea.result("REPORT_SENT", report={"url": url}))


# @spec ROUTE-IDEA-022
@pytest.mark.parametrize("org, url", [
    ("local", ISSUE_URL),
    ("group-sub", "https://example.com/group/sub/project/issues/12"),
], ids=["local", "group-sub"])
def test_idea_022_idea_report_url_matching_repo_issue_is_accepted(idea, org, url):
    idea.patch(identity={"org": org, "repo": "project"})
    at_report_send(idea)
    assert pauses(idea.result("REPORT_SENT", report={"url": url}), "promotion")


# @spec ROUTE-IDEA-022
def test_idea_022_triage_report_url_is_not_pattern_checked(r):
    r.patch(identity={"flow": "triage", "issue": TRIAGE_ISSUE})
    at_report_send(r)
    assert pauses(r.result("REPORT_SENT", report={"url": "https://example.com/elsewhere/comment-1"}), "promotion")


# @spec ROUTE-IDEA-023
def test_idea_023_proposal_ready_with_unchanged_proposal_mtime_is_envelope_invalid(study):
    write_proposal(study, "# old\n")
    at_propose(study)
    unchanged_stop(study, lambda: study.result("PROPOSAL_READY"))
    write_proposal(study, "# new\n")
    assert dispatches(study.result("PROPOSAL_READY"), "forge-reporter", "draft")


# @spec ROUTE-IDEA-024
def test_idea_024_propose_dispatch_records_proposal_mtime_ns(study):
    study.start()
    assert "proposal_mtime_ns" not in study.state()["lifecycle"]["dispatched"]
    study.result("OK")
    assert study.state()["lifecycle"]["dispatched"]["proposal_mtime_ns"] is None
    mtime = write_proposal(study)
    study.result("PROPOSAL_READY")
    study.result("REPORT_DRAFTED")
    study.user("replan")
    assert study.state()["lifecycle"]["dispatched"]["proposal_mtime_ns"] == mtime


# @spec ROUTE-IDEA-025
def test_idea_025_redo_of_propose_keeps_proposal_mtime_ns(study):
    study.start()
    mtime = write_proposal(study, "# old\n")
    study.result("OK")
    write_proposal(study, "# rewritten\n")
    again = study.run()
    assert dispatches(again, "forge-planner", "propose")
    assert study.state()["lifecycle"]["dispatched"]["proposal_mtime_ns"] == mtime


# @spec ROUTE-IDEA-025
def test_idea_025_dirty_tree_redispatch_of_propose_keeps_proposal_mtime_ns(study):
    study.start()
    mtime = write_proposal(study, "# old\n")
    first = study.result("OK")
    wip = Path(study.wt, "wip.txt")
    wip.write_text("wip\n")
    assert pauses(study.run(), "dirty_tree")
    wip.unlink()
    write_proposal(study, "# rewritten\n")
    again = study.user("stash")
    assert dispatches(again, "forge-planner", "propose")
    assert again["invoke"]["dispatch_id"] != first["invoke"]["dispatch_id"]
    assert study.state()["lifecycle"]["dispatched"]["proposal_mtime_ns"] == mtime
    changed = {"dispatch_id", "base_sha", "agent_prefix", "dispatch_as"}
    assert {k: v for k, v in again["invoke"].items() if k not in changed} == \
        {k: v for k, v in first["invoke"].items() if k not in changed}


# @spec ROUTE-DISP-002
def test_disp_002_propose_invocation_has_no_stage(study):
    assert "stage" not in at_propose(study)["invoke"]


# @spec ROUTE-DISP-009
def test_disp_009_task_first_in_recon_and_propose_refs(study):
    task = with_task(study)
    for decision in (study.start(), study.result("OK")):
        refs = decision["invoke"]["context_refs"]
        assert refs[0] == task and refs.count(task) == 1


# @spec ROUTE-ENV-004
def test_env_004_propose_head_sha_differing_from_base_sha_is_envelope_invalid(study):
    study.human_commit({"extra.txt": "extra\n"})
    at_propose(study)
    write_proposal(study)
    parent = subprocess.run(["git", "-C", str(study.wt), "rev-parse", "HEAD~1"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert parent != head(study.wt)
    before = study.sp.read_bytes()
    decision = study.result("PROPOSAL_READY", head_sha=parent)
    assert stops(decision, "ENVELOPE_INVALID")
    assert "differs from the dispatch's base_sha" in json.dumps(decision)
    assert study.sp.read_bytes() == before


# @spec ROUTE-ENV-013
def test_env_013_proposal_ready_sets_report_input_to_planner_envelope(study):
    at_propose(study)
    write_proposal(study)
    planner = dispatched_id(study)
    study.result("PROPOSAL_READY")
    assert study.state()["lifecycle"]["report_input"] == envelope_file(study, planner)


# @spec ROUTE-ENV-002, ROUTE-ENV-014
@pytest.mark.parametrize("status", ["READY_FOR_QA", "STAGE_COMPLETE", "OK"])
def test_env_014_propose_rejects_statuses_outside_its_table(study, status):
    at_propose(study)
    unchanged_stop(study, lambda: study.result(status))


# @spec ROUTE-BLK-002
def test_blk_002_propose_concerns_sets_resume_phase_propose(study):
    at_propose(study)
    study.result("CONCERNS", escalation={"details": "open question"})
    assert study.state()["lifecycle"]["resume_phase"] == "PROPOSE"


# @spec ROUTE-BLK-009, ROUTE-IDEA-026
def test_blk_009_propose_concerns_ruling_redispatches_propose(study):
    blocked = at_propose_council_signoff(study)
    decision = study.user("approve", option="B")
    assert dispatches(decision, "forge-planner", "propose")
    assert study.state()["lifecycle"]["current_phase"] == "PROPOSE"
    assert decision["invoke"]["context_refs"] == [*blocked, "council/matrix.md"]
    assert DELTA_B in json.dumps(decision["invoke"]["notes"])


# @spec ROUTE-DRIFT-009
@pytest.mark.parametrize("phase", ["RECON", "PROPOSE"])
def test_drift_009_idea_triage_drift_stops_with_worktree_moved(study, phase):
    study.start()
    if phase == "PROPOSE":
        study.result("OK")
    study.human_commit({"src/h.py": "h\n"})
    unchanged_stop(study, study.run, "WORKTREE_MOVED")


# @spec ROUTE-IDEA-026
def test_idea_026_every_propose_dispatch_uses_planner_propose_mode(study):
    assert dispatches(at_propose(study), "forge-planner", "propose")
    study.result("CONCERNS", escalation={"details": "open question"})
    study.result("COUNCIL_RULING", ruling=propose_ruling())
    assert dispatches(study.user("leave_open"), "forge-planner", "propose")
    write_proposal(study)
    study.result("PROPOSAL_READY")
    study.result("REPORT_DRAFTED")
    assert dispatches(study.user("replan"), "forge-planner", "propose")


# @spec ROUTE-IDEA-027
def test_idea_027_every_idea_triage_recon_dispatch_uses_scout_idea_or_triage_mode(study):
    study.start()
    assert stops(study.result("ERROR", escalation={"details": "boom"}), "AGENT_ERROR")
    assert dispatches(study.run(), "forge-scout", MODE_OF[study.flow])
    assert dispatches(study.goto("RECON"), "forge-scout", MODE_OF[study.flow])
