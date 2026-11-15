"""Tests for forge_hub.py (docs/intent/commander/commander-specs.md, forge_hub.py sections).

ASSUMPTIONS about shapes the specs leave open:
- `brief` is also importable as forge_hub.brief(cwd, workstream, pid, session) -> dict, and
  calls the router through the module attribute forge_hub.next_step.route.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import HOLDER_SESSION, holder_args, load_module, read_state, run_script, write_state


def hub(*args, cwd):
    return run_script("forge_hub.py", *args, cwd=cwd)


def ok(res, status="OK"):
    assert res.returncode == 0, res.stderr
    assert res.json["status"] == status, res.json
    return res.json


def patch_state(ws, **sections):
    state = read_state(ws["state_path"])
    for key, value in sections.items():
        if isinstance(value, dict) and isinstance(state.get(key), dict):
            state[key].update(value)
        else:
            state[key] = value
    write_state(ws["state_path"], state)


def envelopes(ws):
    return ws["state_path"].parent / "envelopes"


def set_dispatched(ws, agent="forge-dev", dispatch_id="d-1"):
    dispatched = {"dispatch_id": dispatch_id, "agent": agent, "mode": "full", "base_sha": "0" * 40}
    patch_state(ws, lifecycle={"current_phase": "DEV_FULL", "dispatched": dispatched,
                               "invoke": {**dispatched, "state_path": str(ws["state_path"])}})
    envelopes(ws).mkdir(exist_ok=True)
    return envelopes(ws) / f"{dispatch_id}.json"


# ------------------------------------------------------------------ invocation


# @spec CMD-CLI-001, CMD-CLI-002
@pytest.mark.parametrize("args", [["bogus"], [], ["envelope"], ["answer", "--state", "x"],
                                  ["brief", "--workstream", "feat-demo"]])
def test_usage_errors(workstream, args):
    res = hub(*args, cwd=workstream["worktree"])
    assert res.returncode == 2 and res.stderr


# @spec CMD-CLI-001
def test_every_subcommand_prints_one_object(workstream):
    sp = str(workstream["state_path"])
    for args in [["classify", "list"], ["brief", "--workstream", "feat-demo", *holder_args()],
                 ["envelope", "--state", sp], ["answer", "--state", sp, "--decision", "approve"]]:
        res = hub(*args, cwd=workstream["worktree"])
        assert res.returncode == 0, (args, res.stderr)
        assert isinstance(json.loads(res.stdout), dict)


# @spec CMD-CLI-003
@pytest.mark.parametrize("args", [["classify", "list"], ["brief", *holder_args()]])
def test_outside_git_exits_1(tmp_path, forge_home, args):
    outside = tmp_path / "plain"
    outside.mkdir()
    res = hub(*args, cwd=outside)
    assert res.returncode == 1 and res.stderr


# ------------------------------------------------------------------- arguments


def classify(ws, *words):
    res = hub("classify", *words, cwd=ws["repo"])
    assert res.returncode == 0, res.stderr
    return res.json


# @spec CMD-ARG-001
def test_empty_is_current(workstream):
    assert classify(workstream)["kind"] == "current"


# @spec CMD-ARG-002
def test_list(workstream):
    assert classify(workstream, "list")["kind"] == "list"


# @spec CMD-ARG-003
@pytest.mark.parametrize("words, ref", [(["pr", "12"], "12"), (["pr", "#12"], "#12"),
                                        (["https://github.com/acme/widgets/pull/12"],
                                         "https://github.com/acme/widgets/pull/12")])
def test_pr(workstream, words, ref):
    assert classify(workstream, *words) == {"kind": "pr", "ref": ref}


# @spec CMD-ARG-004
def test_adopt(workstream):
    assert classify(workstream, "adopt", "feat/old-work") == {"kind": "adopt", "branch": "feat/old-work"}


# @spec CMD-ARG-005
@pytest.mark.parametrize("words", [["pr"], ["adopt"], ["pr", "12", "13"], ["adopt", "a", "b"]])
def test_invalid_forms(workstream, words):
    out = classify(workstream, *words)
    assert out["kind"] == "invalid" and out["detail"]


# @spec CMD-ARG-006
@pytest.mark.parametrize("word", ["https://github.com/acme/widgets/issues/42", "#42", "42"])
def test_issue(workstream, word):
    assert classify(workstream, word) == {"kind": "issue", "ref": word}


# @spec CMD-ARG-006
@pytest.mark.parametrize("word", ["0", "#0", "-3", "#x"])
def test_not_an_issue(workstream, word):
    assert classify(workstream, word)["kind"] != "issue"


# @spec CMD-ARG-007
def test_resume_existing(workstream):
    assert classify(workstream, "feat-demo") == {"kind": "resume", "workstream": "feat-demo"}


# @spec CMD-ARG-007
def test_resume_from_inside_worktree(workstream):
    res = hub("classify", "feat-demo", cwd=workstream["worktree"])
    assert res.json == {"kind": "resume", "workstream": "feat-demo"}


# @spec CMD-ARG-008
def test_unknown_slug(workstream):
    assert classify(workstream, "feat-dmeo") == {"kind": "unknown", "workstream": "feat-dmeo"}


# @spec CMD-ARG-009
@pytest.mark.parametrize("words", [["add", "rate", "limiting"], ["Refactor"], ["feat/x"]])
def test_describe(workstream, words):
    assert classify(workstream, *words) == {"kind": "describe", "text": " ".join(words)}


# @spec CMD-ARG-010
def test_order(workstream, forge_home):
    for name in ["list", "42"]:  # directories that look like workstreams never shadow earlier rules
        (workstream["state_path"].parent.parent / name).mkdir()
    assert classify(workstream, "list")["kind"] == "list"
    assert classify(workstream, "42")["kind"] == "issue"


# ---------------------------------------------------------------- resume brief


def brief(ws, *extra, workstream="feat-demo"):
    args = ["brief", *holder_args(), *extra]
    if workstream:
        args += ["--workstream", workstream]
    res = hub(*args, cwd=ws["worktree"])
    assert res.returncode == 0, res.stderr
    return res.json


def route(ws, event=None):
    args = ["--workstream", "feat-demo", *holder_args()]
    stdin = None
    if event is not None:
        args += ["--event", "-"]
        stdin = json.dumps(event)
    res = run_script("next_step.py", *args, cwd=ws["worktree"], stdin=stdin)
    assert res.returncode == 0, res.stderr
    return res.json


def hub_entry(ws, second, decisions, dead_ends=(), sha=None):
    body = {"timestamp": f"2026-11-16T09:30:{second:02d}Z", "sha_anchor": sha, "stage": "PLAN",
            "decisions": list(decisions), "dead_ends": list(dead_ends), "tooling_quirks": []}
    sha7 = sha[:7] if sha else "0000000"
    path = ws["state_path"].parent / "memory" / "hub" / f"20261116T0930{second:02d}Z-{sha7}.json"
    path.write_text(json.dumps(body))


# @spec CMD-BRIEF-001
def test_non_ok_wake_returned_unchanged(workstream):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        lock = workstream["state_path"].parent / "lock.json"
        data = json.loads(lock.read_text())
        data.update(holder_pid=proc.pid, holder_session="other")
        lock.write_text(json.dumps(data))
        out = brief(workstream)
        wake = run_script("resolve_state.py", "wake", "--workstream", "feat-demo", *holder_args(),
                          cwd=workstream["worktree"]).json
        assert out["status"] == "LOCKED" and out == wake
    finally:
        proc.kill()
        proc.wait()


# @spec CMD-BRIEF-002, CMD-BRIEF-003, CMD-BRIEF-004, CMD-BRIEF-005, CMD-BRIEF-006, CMD-BRIEF-008
def test_fresh_workstream_brief(workstream):
    out = brief(workstream)
    assert out["status"] == "OK" and out["pending_envelope"] is False
    assert out["lines"] == [
        "Workstream: local/project/feat-demo (NAIVE, collaborative, plan strict)",
        "Phase: INIT (nothing run yet)",
        "Progress: no plan yet",
        "Hub context: none recorded",
        "Next step: Start routing (nothing has run yet)",
    ]


# @spec CMD-BRIEF-002
def test_brief_without_workstream_uses_current_branch(workstream):
    assert brief(workstream, workstream=None)["lines"][0].startswith("Workstream: local/project/feat-demo ")


# @spec CMD-BRIEF-003
def test_sequential_and_auto(workstream):
    patch_state(workstream, config={"collaborative": False, "plan_mode": "auto", "repo_mode": "LID"})
    assert brief(workstream)["lines"][0] == "Workstream: local/project/feat-demo (LID, sequential, plan auto)"


# @spec CMD-BRIEF-004
def test_phase_with_last_status(workstream):
    patch_state(workstream, lifecycle={"current_phase": "PRE_DEV", "last_agent": "forge-verifier",
                                       "last_status": "PARTIAL_REMAINING", "pass": 1})
    assert brief(workstream)["lines"][1] == "Phase: PRE_DEV (forge-verifier → PARTIAL_REMAINING)"


# @spec CMD-BRIEF-005
def test_progress(workstream):
    patch_state(workstream, scope_manifest={"assigned": ["a/**", "b/**", "c/**"], "completed": ["a/**"],
                                            "remaining": ["b/**", "c/**"]},
                lifecycle={"current_phase": "PRE_DEV", "pass": 1})
    assert brief(workstream)["lines"][2] == "Progress: 1 of 3 scope patterns complete; remaining: b/**, c/**"


# @spec CMD-BRIEF-005
def test_progress_nothing_remaining_with_blockers(workstream):
    patch_state(workstream, scope_manifest={"assigned": ["a/**"], "completed": ["a/**"], "remaining": []},
                blockers=[{"id": "blk-1", "kind": "TESTS_FAILED", "agent": "forge-verifier",
                           "phase": "VERIFY_FINAL", "details": "red"}],
                lifecycle={"current_phase": "COUNCIL"})
    assert brief(workstream)["lines"][2] == \
        "Progress: 1 of 1 scope patterns complete; remaining: none; open blockers: 1"


# @spec CMD-BRIEF-006
def test_hub_context_skips_entries_without_decisions(workstream):
    hub_entry(workstream, 1, ["User wants per-IP limits only."])
    hub_entry(workstream, 2, [], dead_ends=["tried account limits"])
    assert brief(workstream)["lines"][3] == "Hub context: User wants per-IP limits only."


# @spec CMD-BRIEF-006
def test_hub_context_skips_historical(workstream):
    hub_entry(workstream, 1, ["current intent"])
    hub_entry(workstream, 2, ["rolled back intent"], sha="e" * 40)
    assert brief(workstream)["lines"][3] == "Hub context: current intent"


# @spec CMD-BRIEF-007, CMD-BRIEF-010
def test_pending_envelope(workstream):
    path = set_dispatched(workstream)
    assert brief(workstream)["pending_envelope"] is False
    path.write_text(json.dumps({"dispatch_id": "d-1", "agent": "forge-dev", "status": "IMPLEMENTATION_COMPLETE"}))
    out = brief(workstream)
    assert out["pending_envelope"] is True
    assert out["lines"][4] == "Next step: Apply the result forge-dev left (IMPLEMENTATION_COMPLETE)"


# @spec CMD-BRIEF-007
def test_pending_envelope_unreadable(workstream):
    set_dispatched(workstream).write_text("{broken")
    out = brief(workstream)
    assert out["pending_envelope"] is True
    assert out["lines"][4] == "Next step: Apply the result forge-dev left (unreadable)"


# @spec CMD-BRIEF-009
def test_next_step_for_pause(workstream):
    route(workstream, {"type": "start"})
    d = read_state(workstream["state_path"])["lifecycle"]["dispatched"]
    head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workstream["worktree"], capture_output=True,
                              text=True).stdout.strip()
    paused = route(workstream, {"type": "result", "envelope": {
        "dispatch_id": d["dispatch_id"], "agent": d["agent"], "status": "READY_FOR_QA", "head_sha": head_sha,
        "files_touched": [], "summary": "s", "scope": {"assigned": ["src/**"]}}})
    assert brief(workstream)["lines"][4] == f"Next step: Waiting for you: {paused['question']}"


# @spec CMD-BRIEF-009
def test_next_step_for_dispatch(workstream):
    first = route(workstream, {"type": "start"})
    assert brief(workstream)["lines"][4] == f"Next step: {first['summary']}"


# @spec CMD-BRIEF-009
def test_next_step_for_stop(workstream, monkeypatch):
    forge_hub = load_module("forge_hub")
    patch_state(workstream, lifecycle={"current_phase": "PLAN"})
    monkeypatch.setattr(forge_hub.next_step, "route", lambda *a, **k: {
        "action": "stop", "phase": "PLAN", "status": "ATTEMPT_LIMIT", "detail": "stopped at PLAN",
        "summary": "Stopped: ATTEMPT_LIMIT"})
    out = forge_hub.brief(str(workstream["worktree"]), "feat-demo", os.getpid(), HOLDER_SESSION)
    assert out["lines"][4] == "Next step: Stopped: ATTEMPT_LIMIT: stopped at PLAN"


# @spec CMD-BRIEF-011
def test_brief_does_not_change_state(workstream):
    route(workstream, {"type": "start"})
    before = workstream["state_path"].read_bytes()
    brief(workstream)
    assert workstream["state_path"].read_bytes() == before


# ------------------------------------------------------------- envelope pickup


def envelope(ws, *extra):
    return hub("envelope", "--state", ws["state_path"], *extra, cwd=ws["worktree"])


# @spec CMD-ENV-001
def test_envelope_wrapped_as_event(workstream):
    body = {"dispatch_id": "d-1", "agent": "forge-dev", "status": "IMPLEMENTATION_COMPLETE", "head_sha": "x",
            "summary": "quote \" and\nnewline"}
    set_dispatched(workstream).write_text(json.dumps(body))
    out = ok(envelope(workstream))
    event_path = Path(out["event_path"])
    assert event_path.is_absolute() and event_path == envelopes(workstream) / "d-1.event.json"
    assert json.loads(event_path.read_text()) == {"type": "result", "envelope": body}


# @spec CMD-ENV-002
def test_no_dispatch(workstream):
    ok(envelope(workstream), "NO_DISPATCH")


# @spec CMD-ENV-003
def test_envelope_missing(workstream):
    path = set_dispatched(workstream)
    out = ok(envelope(workstream), "ENVELOPE_MISSING")
    assert str(path) in json.dumps(out)


# @spec CMD-ENV-004
@pytest.mark.parametrize("raw", ["{broken", "[]"])
def test_envelope_unreadable(workstream, raw):
    set_dispatched(workstream).write_text(raw)
    out = ok(envelope(workstream), "ENVELOPE_UNREADABLE")
    assert out["detail"]
    assert not (envelopes(workstream) / "d-1.event.json").exists()


# @spec CMD-ENV-005
def test_discard(workstream):
    path = set_dispatched(workstream)
    path.write_text("{broken")
    out = ok(envelope(workstream, "--discard"), "DISCARDED")
    moved = Path(out["path"])
    assert not path.exists() and moved.exists() and moved.parent == envelopes(workstream)
    assert re.fullmatch(r"d-1\.rejected-\d{8}T\d{6}Z\.json", moved.name)
    ok(envelope(workstream, "--discard"), "ENVELOPE_MISSING")


# @spec CMD-ENV-006
@pytest.mark.parametrize("sub", [["envelope"], ["answer", "--decision", "approve"]])
@pytest.mark.parametrize("content", [None, "{broken"])
def test_state_invalid(workstream, tmp_path, sub, content):
    state = tmp_path / "state.json"
    if content is not None:
        state.write_text(content)
    res = hub(sub[0], "--state", state, *sub[1:], cwd=workstream["worktree"])
    out = ok(res, "STATE_INVALID")
    assert out["detail"]


# @spec CMD-ENV-007
def test_envelopes_dir_created(workstream):
    set_dispatched(workstream)
    envelopes(workstream).rmdir()
    ok(envelope(workstream), "ENVELOPE_MISSING")
    assert envelopes(workstream).is_dir()


# --------------------------------------------------------------- answer events


def answer(ws, *extra):
    return hub("answer", "--state", ws["state_path"], *extra, cwd=ws["worktree"])


def set_awaiting(ws, kind="plan_approval"):
    patch_state(ws, lifecycle={"awaiting": {"kind": kind, "payload": {}}})


# @spec CMD-ANS-001
def test_answer_event(workstream, tmp_path):
    set_awaiting(workstream, "council_signoff")
    notes = tmp_path / "notes.txt"
    notes.write_text('She said "no"\nsecond line')
    out = ok(answer(workstream, "--decision", "approve", "--option", "B", "--notes-file", notes))
    path = Path(out["event_path"])
    assert path.is_absolute() and path.parent == envelopes(workstream)
    assert re.fullmatch(r"answer-\d{8}T\d{6}Z\.event\.json", path.name)
    assert json.loads(path.read_text()) == {"type": "user", "awaiting": "council_signoff", "decision": "approve",
                                            "option": "B", "notes": 'She said "no"\nsecond line'}


# @spec CMD-ANS-001
def test_answer_without_option_or_notes(workstream):
    set_awaiting(workstream)
    out = ok(answer(workstream, "--decision", "revise"))
    assert json.loads(Path(out["event_path"]).read_text()) == {"type": "user", "awaiting": "plan_approval",
                                                               "decision": "revise"}


# @spec CMD-ANS-002
def test_not_paused(workstream):
    before = sorted(p.name for p in envelopes(workstream).iterdir()) if envelopes(workstream).exists() else []
    ok(answer(workstream, "--decision", "approve"), "NOT_PAUSED")
    after = sorted(p.name for p in envelopes(workstream).iterdir()) if envelopes(workstream).exists() else []
    assert after == before


# @spec CMD-ANS-003
def test_unreadable_notes_file(workstream, tmp_path):
    set_awaiting(workstream)
    res = answer(workstream, "--decision", "revise", "--notes-file", tmp_path / "missing.txt")
    assert res.returncode == 2 and res.stderr
    assert not envelopes(workstream).exists() or not list(envelopes(workstream).glob("answer-*"))
