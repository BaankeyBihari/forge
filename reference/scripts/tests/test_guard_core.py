"""Tests for guard_core.py (docs/intent/guard/guard-specs.md).

ASSUMPTION: guard_core exposes decide(call) -> {"allow": bool, "code": str | None, "reason": str},
where call carries the adapter's mapped fields: agent_type, agent_id, session_id, cwd,
scratchpad_dir, tool_name, tool_input, and `target` (write tools) or `command` (Bash).
"""

import json
import os
import time
from pathlib import Path

import pytest

from conftest import HOLDER_SESSION, load_module

AGENTS = ["forge-planner", "forge-qa", "forge-dev", "forge-verifier", "forge-reviewer",
          "forge-scout", "forge-publisher", "forge-reporter", "forge-council"]


@pytest.fixture
def guard():
    return load_module("guard_core")


@pytest.fixture
def ctx(workstream, tmp_path, guard):
    """Returns a call(agent, **fields) helper bound to the demo workstream."""
    scratch = tmp_path / "scratchpad"
    scratch.mkdir()
    counter = {"n": 0}

    def call(agent, tool_name="Read", target=None, command=None, session=HOLDER_SESSION,
             cwd=None, agent_id=None, tool_input=None, unique=True):
        if tool_input is None:
            tool_input = {}
            if target is not None:
                tool_input = {"file_path": str(target), "content": "x"}
            if command is not None:
                tool_input = {"command": command}
            if unique:  # keep the circuit breaker out of tests that are not about it
                counter["n"] += 1
                tool_input["_n"] = counter["n"]
        payload = {
            "agent_type": agent,
            "agent_id": agent_id or f"id-{agent}",
            "session_id": session,
            "cwd": str(cwd or workstream["worktree"]),
            "scratchpad_dir": str(scratch),
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
        if target is not None:
            payload["target"] = str(target)
        if command is not None:
            payload["command"] = command
        return guard.decide(payload)

    call.ws = workstream
    call.scratch = scratch
    call.sd = workstream["state_path"].parent
    return call


def write(ctx, agent, path):
    return ctx(agent, tool_name="Write", target=path)


def bash(ctx, agent, command):
    return ctx(agent, tool_name="Bash", command=command)


def blocked(decision, code):
    return decision["allow"] is False and decision["code"] == code


# ------------------------------------------------------------ active workstream


# @spec GUARD-WS-001
def test_matching_session_finds_workstream(ctx):
    assert write(ctx, "forge-dev", ctx.ws["worktree"] / "src/a.py")["allow"] is True


# @spec GUARD-WS-002
def test_other_session_has_no_workstream(ctx):
    d = ctx("forge-dev", tool_name="Write", target=ctx.ws["worktree"] / "src/a.py", session="stranger")
    assert blocked(d, "NO_ACTIVE_WORKSTREAM")


# @spec GUARD-WS-002
def test_cwd_outside_repository(ctx, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert blocked(ctx("forge-dev", cwd=outside), "NO_ACTIVE_WORKSTREAM")


# ------------------------------------------------------------------ kill switch


# @spec GUARD-KILL-001
def test_kill_switch_blocks_everything(ctx):
    (ctx.ws["worktree"] / ".STOP_FORGE").write_text("")
    assert blocked(ctx("forge-verifier", tool_name="Read"), "KILL_SWITCH")
    assert blocked(write(ctx, "forge-dev", ctx.ws["worktree"] / "src/a.py"), "KILL_SWITCH")
    (ctx.ws["worktree"] / ".STOP_FORGE").unlink()
    assert ctx("forge-verifier", tool_name="Read")["allow"] is True


# @spec GUARD-KILL-001
def test_kill_switch_in_main_worktree_stops_workstreams(ctx):
    (ctx.ws["repo"] / ".STOP_FORGE").write_text("")
    assert blocked(ctx("forge-verifier", tool_name="Read"), "KILL_SWITCH")
    (ctx.ws["repo"] / ".STOP_FORGE").unlink()
    assert ctx("forge-verifier", tool_name="Read")["allow"] is True


def dispatched(ctx, agent, dispatch_id="d-123"):
    state = json.loads(ctx.ws["state_path"].read_text())
    state["lifecycle"]["dispatched"] = {"dispatch_id": dispatch_id, "agent": agent, "mode": "full",
                                        "base_sha": "0" * 40}
    ctx.ws["state_path"].write_text(json.dumps(state))
    return ctx.sd / "envelopes"


# @spec GUARD-WRITE-009
def test_dispatched_agent_writes_its_envelope(ctx):
    env = dispatched(ctx, "forge-dev")
    assert write(ctx, "forge-dev", env / "d-123.json")["allow"] is True


# @spec GUARD-WRITE-009
@pytest.mark.parametrize("agent, name", [("forge-qa", "d-123.json"), ("forge-dev", "d-999.json"),
                                         ("forge-dev", "d-123.event.json"), ("forge-council-critic", "d-123.json")])
def test_other_envelope_writes_blocked(ctx, agent, name):
    env = dispatched(ctx, "forge-dev")
    assert blocked(write(ctx, agent, env / name), "BOUNDARY_VIOLATION")


# @spec GUARD-WRITE-009
def test_no_envelope_write_without_dispatch(ctx):
    assert blocked(write(ctx, "forge-dev", ctx.sd / "envelopes" / "d-123.json"), "BOUNDARY_VIOLATION")


# -------------------------------------------------------------- duplicate calls


# @spec GUARD-DUP-001
def test_fingerprint_file(ctx, forge_home):
    ctx("forge-dev", tool_name="Read", tool_input={"file_path": "x"}, agent_id="agent-1")
    assert (forge_home / "guard" / HOLDER_SESSION / "agent-1.json").is_file()


# @spec GUARD-DUP-002
def test_fourth_identical_call_is_blocked(ctx):
    same = dict(tool_name="Read", tool_input={"file_path": "x"}, agent_id="a1")
    for _ in range(3):
        assert ctx("forge-dev", **same)["allow"] is True
    assert blocked(ctx("forge-dev", **same), "DUPLICATE_CALL")


# @spec GUARD-DUP-002
def test_different_call_resets_the_run(ctx):
    same = dict(tool_name="Read", tool_input={"file_path": "x"}, agent_id="a1")
    for _ in range(3):
        ctx("forge-dev", **same)
    ctx("forge-dev", tool_name="Read", tool_input={"file_path": "y"}, agent_id="a1")
    assert ctx("forge-dev", **same)["allow"] is True


# @spec GUARD-DUP-002
def test_runs_are_per_agent(ctx):
    same = dict(tool_name="Read", tool_input={"file_path": "x"})
    for _ in range(3):
        ctx("forge-dev", agent_id="a1", **same)
    assert ctx("forge-dev", agent_id="a2", **same)["allow"] is True


# @spec GUARD-DUP-002
def test_fingerprint_uses_canonical_json(ctx):
    for i in range(3):
        ctx("forge-dev", tool_name="Read", agent_id="a1", tool_input={"a": 1, "b": 2})
    assert blocked(ctx("forge-dev", tool_name="Read", agent_id="a1", tool_input={"b": 2, "a": 1}),
                   "DUPLICATE_CALL")


# @spec GUARD-DUP-003
def test_old_fingerprint_dirs_are_pruned(ctx, forge_home):
    old = forge_home / "guard" / "old-session"
    recent = forge_home / "guard" / "recent-session"
    for d in (old, recent):
        d.mkdir(parents=True)
        (d / "a.json").write_text("[]")
    stale = time.time() - 25 * 3600
    os.utime(old, (stale, stale))
    ctx("forge-dev", tool_name="Read")
    assert not old.exists() and recent.exists()


# ---------------------------------------------------------------- write targets


# @spec GUARD-WRITE-001
def test_relative_and_symlinked_targets_are_resolved(ctx):
    wt = ctx.ws["worktree"]
    (wt / "tests").mkdir()
    (wt / "tests/t.py").write_text("")
    (wt / "src").mkdir()
    (wt / "src/alias.py").symlink_to(wt / "tests/t.py")
    assert blocked(write(ctx, "forge-dev", "src/alias.py"), "BOUNDARY_VIOLATION")
    assert write(ctx, "forge-qa", "src/alias.py")["allow"] is True
    assert write(ctx, "forge-dev", "src/real.py")["allow"] is True


# @spec GUARD-WRITE-002
@pytest.mark.parametrize(
    "path, owner",
    [("tests/test_a.py", "forge-qa"), ("docs/design.md", "forge-planner"), ("PLAN.md", "forge-planner"),
     ("src/a.py", "forge-dev")],
)
def test_worktree_path_classes(ctx, path, owner):
    for agent in AGENTS:
        d = write(ctx, agent, ctx.ws["worktree"] / path)
        if agent == owner:
            assert d["allow"] is True, agent
        else:
            assert blocked(d, "BOUNDARY_VIOLATION"), agent


# @spec GUARD-WRITE-002
def test_worktree_uses_workstream_test_patterns(ctx):
    from conftest import read_state, write_state
    state = read_state(ctx.ws["state_path"])
    state["config"]["test_patterns"] = ["spec/**"]
    write_state(ctx.ws["state_path"], state)
    assert write(ctx, "forge-qa", ctx.ws["worktree"] / "spec/a_spec.rb")["allow"] is True
    assert write(ctx, "forge-dev", ctx.ws["worktree"] / "tests/test_a.py")["allow"] is True


# @spec GUARD-WRITE-003
def test_dev_on_test_path_is_told_to_escalate(ctx):
    d = write(ctx, "forge-dev", ctx.ws["worktree"] / "tests/test_a.py")
    assert blocked(d, "BOUNDARY_VIOLATION")
    assert "TEST_CHANGE_REQUESTED" in d["reason"]


# @spec GUARD-WRITE-004
@pytest.mark.parametrize(
    "partition, owner",
    [("planner", "forge-planner"), ("qa", "forge-qa"), ("dev", "forge-dev"), ("verifier", "forge-verifier"),
     ("reviewer", "forge-reviewer"), ("scout", "forge-scout"), ("hub", None), ("shared", None)],
)
def test_memory_partitions(ctx, partition, owner):
    target = ctx.sd / "memory" / partition / "note.md"
    for agent in AGENTS:
        d = write(ctx, agent, target)
        assert d["allow"] is (agent == owner), (agent, partition)


# @spec GUARD-WRITE-005
@pytest.mark.parametrize("subdir, owner", [("scout", "forge-scout"), ("council", "forge-council")])
def test_scout_and_council_dirs(ctx, subdir, owner):
    for agent in AGENTS:
        assert write(ctx, agent, ctx.sd / subdir / "brief.md")["allow"] is (agent == owner), agent


# @spec GUARD-WRITE-006
def test_scratchpad(ctx):
    for agent in AGENTS:
        assert write(ctx, agent, ctx.scratch / "tmp.txt")["allow"] is True, agent


# @spec GUARD-WRITE-007
def test_everything_else_is_blocked(ctx, tmp_path):
    for target in [tmp_path / "random.txt", ctx.ws["repo"] / "src/a.py", ctx.sd / "state.json",
                   ctx.sd / "lock.json"]:
        for agent in AGENTS:
            assert blocked(write(ctx, agent, target), "BOUNDARY_VIOLATION"), (agent, target)


# @spec GUARD-WRITE-008
def test_council_personas_write_nowhere(ctx):
    for target in [ctx.scratch / "x.txt", ctx.sd / "council/x.md", ctx.ws["worktree"] / "docs/x.md"]:
        assert blocked(write(ctx, "forge-council-architect", target), "BOUNDARY_VIOLATION")


# @spec GUARD-WRITE-001, GUARD-WRITE-002
@pytest.mark.parametrize("tool", ["Edit", "NotebookEdit"])
def test_other_write_tools(ctx, tool):
    d = ctx("forge-dev", tool_name=tool, target=ctx.ws["worktree"] / "tests/nb.ipynb")
    assert blocked(d, "BOUNDARY_VIOLATION")


# --------------------------------------------------------------------- commands


# @spec GUARD-CMD-001
@pytest.mark.parametrize(
    "command",
    ["echo hi && git push", "git status; git push", "true || git push", "git log | git push",
     "echo a\ngit push", "FOO=1 BAR=2 git push", "env FOO=1 git push", "env -i PATH=/bin git push",
     "/usr/bin/git push", "./bin/git push"],
)
def test_command_parts_are_found(ctx, command):
    assert blocked(bash(ctx, "forge-dev", command), "COMMAND_NOT_ALLOWED")


# @spec GUARD-CMD-001
@pytest.mark.parametrize("command", ["echo 'git push'", "git status | cat", "ls; pwd", "grep -r 'git push' ."])
def test_harmless_commands_pass(ctx, command):
    assert bash(ctx, "forge-dev", command)["allow"] is True


# @spec GUARD-CMD-001
def test_gh_by_basename(ctx):
    assert blocked(bash(ctx, "forge-dev", "/opt/homebrew/bin/gh pr merge 3"), "COMMAND_NOT_ALLOWED")


# @spec GUARD-CMD-002
def test_push_only_by_publisher(ctx):
    assert bash(ctx, "forge-publisher", "git push origin feat/demo")["allow"] is True
    for agent in AGENTS:
        if agent != "forge-publisher":
            assert blocked(bash(ctx, agent, "git push origin feat/demo"), "COMMAND_NOT_ALLOWED"), agent


# @spec GUARD-CMD-003
@pytest.mark.parametrize("flags", ["--force", "-f", "-fu origin feat/demo", "--force-with-lease",
                                   "--force-if-includes", "--mirror", "--delete",
                                   "origin +feat/demo", "origin +HEAD:feat/demo", "origin HEAD:+feat/demo"])
def test_destructive_push_blocked_for_everyone(ctx, flags):
    assert blocked(bash(ctx, "forge-publisher", f"git push {flags}"), "COMMAND_NOT_ALLOWED")


# @spec GUARD-CMD-004
def test_commit_rules(ctx):
    for agent in ["forge-planner", "forge-qa", "forge-dev"]:
        assert bash(ctx, agent, "git commit -m 'x'")["allow"] is True, agent
        assert blocked(bash(ctx, agent, "git commit --amend --no-edit"), "COMMAND_NOT_ALLOWED"), agent
    for agent in ["forge-verifier", "forge-reviewer", "forge-scout", "forge-publisher", "forge-reporter",
                  "forge-council"]:
        assert blocked(bash(ctx, agent, "git commit -m 'x'"), "COMMAND_NOT_ALLOWED"), agent


# @spec GUARD-CMD-005
@pytest.mark.parametrize(
    "command",
    ["git merge main", "git checkout main", "git checkout -- file", "git switch main", "git worktree add x",
     "git rebase main", "git reset --hard", "git clean -fd", "git stash", "git restore f", "git branch -d x",
     "git branch -D x", "git tag -d v1", "git update-ref refs/heads/x HEAD", "git filter-branch"],
)
def test_history_and_branch_commands_blocked_for_everyone(ctx, command):
    for agent in ["forge-publisher", "forge-dev", "forge-reporter"]:
        assert blocked(bash(ctx, agent, command), "COMMAND_NOT_ALLOWED"), agent


# @spec GUARD-CMD-005
def test_plain_branch_listing_allowed(ctx):
    assert bash(ctx, "forge-dev", "git branch --list")["allow"] is True


# @spec GUARD-CMD-006
@pytest.mark.parametrize(
    "command",
    ["gh pr create --fill", "gh pr edit 3 --title x", "gh pr review 3 --approve", "gh pr comment 3 -b hi",
     "gh issue comment 3 -b hi", "gh api -X POST repos/a/b/issues", "gh api --method PATCH repos/a/b",
     "gh api repos/a/b/issues -f title=x"],  # ASSUMPTION: -f/-F imply POST, as in gh itself
)
def test_outward_gh_only_by_reporter(ctx, command):
    assert bash(ctx, "forge-reporter", command)["allow"] is True
    assert blocked(bash(ctx, "forge-scout", command), "COMMAND_NOT_ALLOWED")


# @spec GUARD-CMD-006
@pytest.mark.parametrize("command", ["gh api repos/a/b/pulls", "gh api --method GET repos/a/b", "gh pr view 3"])
def test_read_only_gh_allowed(ctx, command):
    assert bash(ctx, "forge-scout", command)["allow"] is True


# @spec GUARD-CMD-007
def test_run_rerun_only_by_scout(ctx):
    assert bash(ctx, "forge-scout", "gh run rerun 123 --failed")["allow"] is True
    assert blocked(bash(ctx, "forge-reporter", "gh run rerun 123"), "COMMAND_NOT_ALLOWED")


# @spec GUARD-CMD-008
@pytest.mark.parametrize("command", ["gh pr merge 3", "gh release create v1", "gh repo delete a/b",
                                     "gh repo edit --visibility public"])
def test_gh_blocked_for_everyone(ctx, command):
    for agent in ["forge-reporter", "forge-publisher", "forge-scout"]:
        assert blocked(bash(ctx, agent, command), "COMMAND_NOT_ALLOWED"), agent


# @spec GUARD-CMD-008
@pytest.mark.parametrize("command", ["gh repo view", "gh repo clone a/b"])
def test_gh_repo_view_and_clone_allowed(ctx, command):
    assert bash(ctx, "forge-scout", command)["allow"] is True


# @spec GUARD-CMD-009
def test_reason_names_the_part(ctx):
    d = bash(ctx, "forge-dev", "make build && git push origin x")
    assert "git push origin x" in d["reason"]


# @spec GUARD-CMD-010
@pytest.mark.parametrize("command", ["bash -c 'git push'", "sh -lc \"git rebase main\"", "zsh -ec 'gh pr merge 1'",
                                     "/bin/bash -c 'echo && git push'", "bash -c 'bash -c \"git push\"'"])
def test_shell_wrappers_are_unwrapped(ctx, command):
    assert blocked(bash(ctx, "forge-dev", command), "COMMAND_NOT_ALLOWED")


# @spec GUARD-CMD-010
def test_shell_without_c_is_not_unwrapped(ctx):
    assert bash(ctx, "forge-dev", "bash ./scripts/build.sh")["allow"] is True


# @spec GUARD-CMD-011
@pytest.mark.parametrize("command", ["git -C .. push", "git -c user.name=x push", "git --git-dir=.git push",
                                     "git --git-dir .git push", "git --no-pager push", "git -C . -c a=b rebase x",
                                     "git --work-tree . --namespace n push"])
def test_git_global_options_are_skipped(ctx, command):
    assert blocked(bash(ctx, "forge-dev", command), "COMMAND_NOT_ALLOWED")


# @spec GUARD-CMD-011
def test_global_options_do_not_hide_allowed_commands(ctx):
    assert bash(ctx, "forge-dev", "git -C . status")["allow"] is True
    assert bash(ctx, "forge-dev", "git --no-pager log -1")["allow"] is True


# ------------------------------------------------------------------------ order


# @spec GUARD-ORDER-001
def test_kill_switch_before_workstream_lookup(ctx):
    (ctx.ws["worktree"] / ".STOP_FORGE").write_text("")
    assert blocked(ctx("forge-dev", session="stranger"), "KILL_SWITCH")


# @spec GUARD-ORDER-001
def test_workstream_lookup_before_duplicates(ctx):
    same = dict(tool_name="Read", tool_input={"file_path": "x"}, agent_id="a1", session="stranger")
    for _ in range(4):
        d = ctx("forge-dev", **same)
    assert blocked(d, "NO_ACTIVE_WORKSTREAM")


# @spec GUARD-ORDER-001
def test_duplicates_before_write_check(ctx):
    target = ctx.ws["worktree"] / "tests/t.py"
    same = dict(tool_name="Write", target=target, agent_id="a1",
                tool_input={"file_path": str(target), "content": "x"})
    for _ in range(3):
        assert blocked(ctx("forge-dev", **same), "BOUNDARY_VIOLATION")
    assert blocked(ctx("forge-dev", **same), "DUPLICATE_CALL")


# @spec GUARD-ORDER-001
def test_other_tools_pass_after_early_checks(ctx):
    for tool in ["Read", "Grep", "Glob", "WebFetch"]:
        assert ctx("forge-council-critic", tool_name=tool)["allow"] is True
