"""Tests for forge_memory.py (docs/intent/forge-memory/forge-memory-specs.md).

Each test uses the demo workstream from resolve_state.py `new`, writes entry files
into its memory partitions, and runs `forge_memory.py load`.
"""

import json
from pathlib import Path

import pytest

from conftest import commit, git, head, read_state, run_script, write_state


def load(ws, agent, *extra):
    return run_script("forge_memory.py", "load", "--state", ws["state_path"], "--agent", agent, *extra,
                      cwd=ws["worktree"])


def entry(ws, partition, sha, second=0, raw=None, name=None, **fields):
    """Write a valid entry (or raw text) and return its path."""
    body = {"timestamp": f"2026-11-16T09:30:{second:02d}Z", "sha_anchor": sha, "stage": "LID-S6",
            "decisions": ["use a sliding window"], "dead_ends": [], "tooling_quirks": [], **fields}
    sha7 = sha[:7] if isinstance(sha, str) else "0000000"
    path = ws["state_path"].parent / "memory" / partition / (name or f"20261116T0930{second:02d}Z-{sha7}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else json.dumps(body))
    return path


def ok(res):
    assert res.returncode == 0, res.stderr
    assert res.json["status"] == "OK", res.json
    return res.json


@pytest.fixture
def ws(workstream):
    return workstream


# ------------------------------------------------------------------- invocation


# @spec MEM-CLI-001
@pytest.mark.parametrize("args", [["load", "--agent", "hub"], ["load", "--state", "x"],
                                  ["load", "--state", "x", "--agent", "forge-council-critic"],
                                  ["load", "--state", "x", "--agent", "hub", "--limit", "0"], ["bogus"]])
def test_usage_errors(ws, args):
    res = run_script("forge_memory.py", *args, cwd=ws["worktree"])
    assert res.returncode == 2 and res.stderr


# @spec MEM-CLI-001
def test_default_limit_is_20(ws):
    sha = head(ws["worktree"])
    for i in range(25):
        entry(ws, "dev", sha, second=i)
    assert len(ok(load(ws, "forge-dev"))["entries"]) == 20


# @spec MEM-CLI-002
def test_one_json_object_exit_zero(ws, tmp_path):
    for state in [ws["state_path"], tmp_path / "missing.json"]:
        res = run_script("forge_memory.py", "load", "--state", state, "--agent", "hub", cwd=ws["worktree"])
        assert res.returncode == 0
        assert isinstance(json.loads(res.stdout), dict)


# @spec MEM-CLI-003
def test_load_writes_nothing(ws):
    sd = ws["state_path"].parent
    entry(ws, "dev", head(ws["worktree"]))
    entry(ws, "dev", "f" * 40, second=1)  # historical
    entry(ws, "dev", None, second=2, raw="{broken")
    before = {p: p.read_bytes() for p in sd.rglob("*") if p.is_file()}
    ok(load(ws, "forge-dev"))
    after = {p: p.read_bytes() for p in sd.rglob("*") if p.is_file()}
    assert after == before


# ------------------------------------------------------------------- partitions


# @spec MEM-LOAD-001
@pytest.mark.parametrize("agent, partitions", [
    ("hub", ["hub", "shared"]),
    ("forge-planner", ["planner", "shared"]), ("forge-qa", ["qa", "shared"]), ("forge-dev", ["dev", "shared"]),
    ("forge-verifier", ["verifier", "shared"]), ("forge-reviewer", ["reviewer", "shared"]),
    ("forge-scout", ["scout", "shared"]),
    ("forge-publisher", ["shared"]), ("forge-reporter", ["shared"]), ("forge-council", ["shared"]),
])
def test_partitions_per_agent(ws, agent, partitions):
    sha = head(ws["worktree"])
    for i, part in enumerate(["hub", "shared", "planner", "qa", "dev", "verifier", "reviewer", "scout"]):
        entry(ws, part, sha, second=i)
    got = [e["partition"] for e in ok(load(ws, agent))["entries"]]
    assert got == partitions


# @spec MEM-LOAD-002
def test_missing_partition_is_empty(ws):
    import shutil
    shutil.rmtree(ws["state_path"].parent / "memory" / "dev")
    assert ok(load(ws, "forge-dev"))["entries"] == []


# @spec MEM-LOAD-003
def test_newest_first_and_limit(ws):
    sha = head(ws["worktree"])
    for i in range(5):
        entry(ws, "qa", sha, second=i)
    entry(ws, "shared", sha, second=9)
    got = ok(load(ws, "forge-qa", "--limit", "3"))["entries"]
    names = [Path(e["file"]).name for e in got]
    assert names[:3] == [f"20261116T09300{i}Z-{sha[:7]}.json" for i in (4, 3, 2)]
    assert [e["partition"] for e in got] == ["qa", "qa", "qa", "shared"]


# @spec MEM-LOAD-004
def test_entry_fields(ws):
    sha = head(ws["worktree"])
    path = entry(ws, "dev", sha, dead_ends=["async wrapper broke errors"], tooling_quirks=["use -q"])
    [e] = ok(load(ws, "forge-dev"))["entries"]
    assert e == {"partition": "dev", "file": str(path), "historical": False,
                 "timestamp": "2026-11-16T09:30:00Z", "sha_anchor": sha, "stage": "LID-S6",
                 "decisions": ["use a sliding window"], "dead_ends": ["async wrapper broke errors"],
                 "tooling_quirks": ["use -q"]}


# @spec MEM-LOAD-005
def test_only_json_files(ws):
    sha = head(ws["worktree"])
    entry(ws, "dev", sha, name=f"20261116T093000Z-{sha[:7]}.md")
    (ws["state_path"].parent / "memory" / "dev" / "notes.txt").write_text("x")
    out = ok(load(ws, "forge-dev"))
    assert out["entries"] == [] and out["invalid"] == []


# --------------------------------------------------------------------- validity


def invalid(ws, path, word):
    out = ok(load(ws, "forge-dev"))
    assert all(e["file"] != str(path) for e in out["entries"])
    [bad] = [i for i in out["invalid"] if i["file"] == str(path)]
    assert word in bad["reason"].lower(), bad
    return bad


# @spec MEM-ENTRY-001, MEM-ENTRY-008
@pytest.mark.parametrize("name", ["note.json", "20261116T093000Z.json", "2026-11-16-abcdef0.json",
                                  "20261116T093000Z-1234567.json"])
def test_bad_file_name(ws, name):
    invalid(ws, entry(ws, "dev", head(ws["worktree"]), name=name), "name")


# @spec MEM-ENTRY-001
def test_null_anchor_name(ws):
    path = entry(ws, "dev", None)
    assert path.name.endswith("-0000000.json")
    assert [e["file"] for e in ok(load(ws, "forge-dev"))["entries"]] == [str(path)]


# @spec MEM-ENTRY-002
def test_oversize_entry(ws):
    invalid(ws, entry(ws, "dev", head(ws["worktree"]), decisions=["x" * 1700]), "size")


# @spec MEM-ENTRY-002
def test_entry_at_cap_is_valid(ws):
    sha = head(ws["worktree"])
    path = entry(ws, "dev", sha)
    pad = 1600 - len(path.read_bytes())
    path = entry(ws, "dev", sha, decisions=["use a sliding window" + "x" * pad])
    assert len(path.read_bytes()) == 1600
    assert ok(load(ws, "forge-dev"))["entries"]


# @spec MEM-ENTRY-003
@pytest.mark.parametrize("raw", ["{broken", "[1, 2]", "\"text\""])
def test_not_a_json_object(ws, raw):
    invalid(ws, entry(ws, "dev", head(ws["worktree"]), raw=raw), "json")


# @spec MEM-ENTRY-004
@pytest.mark.parametrize("fields, word", [({"timestamp": "yesterday"}, "timestamp"),
                                          ({"timestamp": "2026-11-16 09:30:00"}, "timestamp"),
                                          ({"stage": ""}, "stage"), ({"stage": 5}, "stage")])
def test_bad_timestamp_or_stage(ws, fields, word):
    invalid(ws, entry(ws, "dev", head(ws["worktree"]), **fields), word)


# @spec MEM-ENTRY-004
def test_missing_stage(ws):
    sha = head(ws["worktree"])
    body = {"timestamp": "2026-11-16T09:30:00Z", "sha_anchor": sha, "decisions": ["x"], "dead_ends": [],
            "tooling_quirks": []}
    invalid(ws, entry(ws, "dev", sha, raw=json.dumps(body)), "stage")


# @spec MEM-ENTRY-005
@pytest.mark.parametrize("anchor", ["ABCDEF0" + "0" * 33, "abc123", 7])
def test_bad_anchor(ws, anchor):
    name = f"20261116T093000Z-{str(anchor)[:7]}.json"
    invalid(ws, entry(ws, "dev", head(ws["worktree"]), name=name, sha_anchor=anchor), "sha_anchor")


# @spec MEM-ENTRY-006
@pytest.mark.parametrize("fields", [{"decisions": "one"}, {"dead_ends": [1]}, {"decisions": []},
                                    {"tooling_quirks": None}])
def test_bad_lists(ws, fields):
    invalid(ws, entry(ws, "dev", head(ws["worktree"]), **fields), "")


# @spec MEM-ENTRY-006
def test_missing_list(ws):
    sha = head(ws["worktree"])
    body = {"timestamp": "2026-11-16T09:30:00Z", "sha_anchor": sha, "stage": "x", "decisions": ["x"],
            "dead_ends": []}
    invalid(ws, entry(ws, "dev", sha, raw=json.dumps(body)), "tooling_quirks")


# @spec MEM-ENTRY-007
def test_extra_keys_ignored(ws):
    entry(ws, "dev", head(ws["worktree"]), mood="tired")
    [e] = ok(load(ws, "forge-dev"))["entries"]
    assert "mood" not in e


# @spec MEM-ENTRY-008
def test_invalid_listed_with_reason(ws):
    sha = head(ws["worktree"])
    good = entry(ws, "dev", sha)
    bad = entry(ws, "dev", sha, second=1, raw="{broken")
    out = ok(load(ws, "forge-dev"))
    assert [e["file"] for e in out["entries"]] == [str(good)]
    assert out["invalid"][0]["file"] == str(bad) and out["invalid"][0]["reason"]


# ------------------------------------------------------------------ historical


# @spec MEM-HIST-001
def test_stale_sha_is_historical(ws):
    sha = head(ws["worktree"])
    state = read_state(ws["state_path"])
    state["anchors"]["stale_shas"] = [sha]
    write_state(ws["state_path"], state)
    entry(ws, "dev", sha)
    assert ok(load(ws, "forge-dev"))["entries"][0]["historical"] is True


# @spec MEM-HIST-002
def test_reset_away_commit_is_historical(ws):
    wt = ws["worktree"]
    gone = commit(wt, {"src/a.py": "a\n"}, "later reset away")
    git(wt, "reset", "-q", "--hard", "HEAD~1")
    entry(ws, "dev", gone)
    assert ok(load(ws, "forge-dev"))["entries"][0]["historical"] is True


# @spec MEM-HIST-002
def test_unknown_commit_is_historical(ws):
    entry(ws, "dev", "e" * 40)
    assert ok(load(ws, "forge-dev"))["entries"][0]["historical"] is True


# @spec MEM-HIST-002
def test_commit_on_other_branch_is_historical(ws):
    wt = ws["worktree"]
    git(wt, "checkout", "-q", "-b", "side")
    side = commit(wt, {"s.txt": "s\n"}, "side")
    git(wt, "checkout", "-q", "feat/demo")
    entry(ws, "dev", side)
    assert ok(load(ws, "forge-dev"))["entries"][0]["historical"] is True


# @spec MEM-HIST-003
def test_null_anchor_is_current(ws):
    entry(ws, "hub", None)
    assert ok(load(ws, "hub"))["entries"][0]["historical"] is False


# @spec MEM-HIST-004
def test_ancestor_anchor_is_current(ws):
    wt = ws["worktree"]
    old = head(wt)
    commit(wt, {"src/b.py": "b\n"}, "more work")
    entry(ws, "dev", old)
    entry(ws, "dev", head(wt), second=1)
    assert [e["historical"] for e in ok(load(ws, "forge-dev"))["entries"]] == [False, False]


# --------------------------------------------------------------------- statuses


# @spec MEM-OUT-001
def test_ok_shape(ws):
    out = ok(load(ws, "hub"))
    assert set(out) >= {"status", "entries", "invalid"}


# @spec MEM-OUT-002
@pytest.mark.parametrize("content", [None, "{broken", "[]"])
def test_state_invalid(ws, tmp_path, content):
    state = tmp_path / "state.json"
    if content is not None:
        state.write_text(content)
    res = run_script("forge_memory.py", "load", "--state", state, "--agent", "hub", cwd=ws["worktree"])
    assert res.json["status"] == "STATE_INVALID" and res.json["detail"]


# @spec MEM-OUT-003
def test_worktree_missing(ws, tmp_path):
    state = read_state(ws["state_path"])
    state["identity"]["worktree_path"] = str(tmp_path / "gone")
    write_state(ws["state_path"], state)
    res = load(ws, "hub")
    assert res.json["status"] == "WORKTREE_MISSING" and str(tmp_path / "gone") in json.dumps(res.json)
