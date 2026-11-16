#!/usr/bin/env python3
"""Forge spoke helper: the values a spoke would otherwise copy by hand, from state.json and git.

Design: docs/intent/spokes/protocol/protocol-design.md. Specs: docs/intent/spokes/protocol/protocol-specs.md.

    forge_spoke.py invocation --state PATH --dispatch ID
    forge_spoke.py commit     --state PATH --dispatch ID --message-file FILE [--stage STAGE]
    forge_spoke.py finish     --state PATH --dispatch ID
    forge_spoke.py scope      --state PATH

Prints one JSON object with a status and exits 0; a usage error exits 2 with the message on stderr.
"""

# @spec SPOKE-PROTO-CLI-006
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import next_step  # noqa: E402
import verify_gate  # noqa: E402
from verify_gate import BOUNDARY, classify_path, matches  # noqa: E402

BUILDERS = next_step.BUILDERS
AUTO_STAGES = ["LID-S1", "LID-S2", "LID-S3", "LID-S4"]
ESCALATING = {"ERROR", "CONCERNS", "TEST_CHANGE_REQUESTED", "CONTRACT_MISMATCH", "AUDIT_FAILED", "TESTS_FAILED",
              "CI_FAILED", "CHANGES_REQUESTED", "HUMAN_CODE_CHANGE_REQUESTED", "PUBLISH_REFUSED", "MERGE_BLOCKED"}
PUBLISHING = {"PUBLISHED", "MERGE_READY", "MERGE_BLOCKED", "PUBLISH_REFUSED"}
REPORT_FIELDS = {
    "pr_create": {"title": str, "body": str, "base": str, "head": str},
    "pr_update": {"number": int, "title": str, "body": str},
    "pr_review": {"number": int, "commit_id": str, "body": str},
}


def load_state(path):
    """(state, None) or (None, detail saying why it is unusable)."""
    try:
        text = Path(path).read_text()
    except OSError as e:
        return None, f"state file {path} cannot be read: {e.strerror}"
    try:
        state = json.loads(text)
    except ValueError:
        state = None
    if not isinstance(state, dict):
        return None, f"state file {path} is not a JSON object"
    return state, None


def first_parent_commits(repo, rev_range):
    """[(sha, Forge-Agent or None, [changed paths])] for the first-parent commits in rev_range, oldest first."""
    fmt = "%x1e%H%x1f%(trailers:key=Forge-Agent,valueonly,separator=%x1f)"
    out = repo.out("log", "--first-parent", "--diff-merges=first-parent", "--reverse", "--no-renames",
                   "--name-only", f"--format={fmt}", rev_range)
    commits = []
    for chunk in out.split("\x1e")[1:]:
        header, _, rest = chunk.partition("\n")
        sha, *agents = header.split("\x1f")
        agents = [a.strip() for a in agents if a.strip()]
        commits.append((sha, agents[0] if agents else None, [p for p in rest.splitlines() if p]))
    return commits


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def text(v):
    return isinstance(v, str) and v != ""


def line_pairs(v):
    return isinstance(v, list) and all(isinstance(p, list) and len(p) == 2 and all(map(is_int, p)) for p in v)


def route_list(v):
    return isinstance(v, list) and all(a in BUILDERS for a in v)


def review_ok(r):
    return (isinstance(r, dict) and r.get("verdict") in ("approve", "request_changes", "comment")
            and isinstance(r.get("body"), str) and isinstance(r.get("comments"), list)
            and all(isinstance(c, dict) and isinstance(c.get("path"), str) and is_int(c.get("line"))
                    and c["line"] > 0 and isinstance(c.get("body"), str) for c in r["comments"]))


def report_ok(r):
    if not isinstance(r, dict) or not isinstance(r.get("kind"), str) or r["kind"] not in REPORT_FIELDS:
        return False
    if not all(is_int(r.get(k)) if t is int else isinstance(r.get(k), t) for k, t in REPORT_FIELDS[r["kind"]].items()):
        return False
    return r["kind"] != "pr_review" or (
        r.get("event") in ("APPROVE", "REQUEST_CHANGES", "COMMENT") and isinstance(r.get("comments"), list)
        and all(isinstance(c, dict) and {"path", "line", "side", "body"} <= c.keys() for c in r["comments"]))


def ruling_problems(ruling, council):
    if not isinstance(ruling, dict):
        return ["ruling must be an object"]
    problems = []
    ref = ruling.get("matrix_ref")
    if not (isinstance(ref, str) and os.path.isabs(ref) and Path(ref).is_file()
            and Path(ref).resolve().is_relative_to(council.resolve())):
        problems.append(f"ruling.matrix_ref must be the absolute path of an existing file under {council}")
    if not isinstance(ruling.get("requires_user"), bool):
        problems.append("ruling.requires_user must be a boolean")
    if not (str_list(ruling.get("panel")) and ruling["panel"]):
        problems.append("ruling.panel must be a non-empty list of strings")
    options = ruling.get("options")
    if not (isinstance(options, list) and options and all(
            isinstance(o, dict) and isinstance(o.get("label"), str) and isinstance(o.get("summary"), str)
            and isinstance(o.get("delta"), str) and route_list(o.get("route_to")) for o in options)):
        problems.append("ruling.options must be a non-empty list of {label, summary, route_to, delta} with string "
                        "label, summary, and delta, and route_to a list of forge-planner, forge-qa, forge-dev")
        return problems
    labels = [o["label"] for o in options]
    if len(set(labels)) != len(labels):
        problems.append("ruling.options labels must be distinct")
    recommended = next((o for o in options if o["label"] == ruling.get("recommended")), None)
    if recommended is None:
        problems.append("ruling.recommended must be one option's label")
    elif ruling.get("route_to") != recommended["route_to"] or ruling.get("delta") != recommended["delta"]:
        problems.append("ruling.route_to and ruling.delta must equal the recommended option's")
    return problems


# --------------------------------------------------------------------- commands


# @spec SPOKE-PROTO-INV-001, SPOKE-PROTO-INV-002
def invocation(state):
    lc = state["lifecycle"]
    return {"status": "OK", "invoke": lc.get("invoke"), "phase": lc.get("current_phase"), "state": state}


# @spec SPOKE-PROTO-COMMIT-001, SPOKE-PROTO-COMMIT-002, SPOKE-PROTO-COMMIT-003, SPOKE-PROTO-COMMIT-004
# @spec SPOKE-PROTO-COMMIT-005, SPOKE-PROTO-COMMIT-007
def commit(state, repo, message, stage):
    invoke = state["lifecycle"]["invoke"]
    agent = invoke.get("agent")
    if agent not in BUILDERS:
        return {"status": "NOT_A_BUILDER", "detail": f"{agent} does not commit"}
    # strict with notes: a revision, whose changes may belong to earlier stages
    labels_stages = invoke.get("mode") == "auto" or (invoke.get("mode") == "strict" and invoke.get("notes"))
    if stage is not None and not (agent == "forge-planner" and labels_stages and stage in AUTO_STAGES):
        return {"status": "STAGE_INVALID",
                "detail": "--stage is for forge-planner in auto mode or a strict revision, and names one of "
                + ", ".join(AUTO_STAGES)}
    if repo.run("diff", "--cached", "--quiet", check=False).returncode == 0:
        return {"status": "NOTHING_STAGED"}
    patterns = (state.get("config") or {}).get("test_patterns")
    staged = [p for p in repo.out("diff", "--cached", "--no-renames", "--name-only").splitlines() if p]
    outside = [p for p in staged if classify_path(p, patterns) != BOUNDARY[agent]]
    if outside:
        return {"status": "BOUNDARY_VIOLATION", "paths": outside,
                "detail": f"{agent} may change {BOUNDARY[agent]} paths only"}
    trailers = {"Forge-Agent": agent, "Forge-Stage": stage or invoke.get("stage"), "Forge-Mode": invoke.get("mode"),
                "Forge-Input-SHA": invoke.get("base_sha")}
    args = [arg for key, value in trailers.items() for arg in ("--trailer", f"{key}: {value}")]
    repo.run("commit", "-q", "-m", message, *args)
    return {"status": "COMMITTED", "sha": repo.out("rev-parse", "HEAD").strip()}


# @spec SPOKE-PROTO-FIN-005, SPOKE-PROTO-FIN-006, SPOKE-PROTO-FIN-007, SPOKE-PROTO-FIN-008, SPOKE-PROTO-FIN-009
# @spec SPOKE-PROTO-FIN-010, SPOKE-PROTO-FIN-011, SPOKE-PROTO-FIN-012, SPOKE-PROTO-FIN-013, SPOKE-PROTO-FIN-014
# @spec SPOKE-PROTO-FIN-015, SPOKE-PROTO-FIN-016, SPOKE-PROTO-FIN-017, SPOKE-PROTO-FIN-018
def envelope_problems(env, state, repo, council):
    lc = state["lifecycle"]
    d, phase = lc["dispatched"], lc.get("current_phase")
    agent = d.get("agent")
    problems = []
    if not (isinstance(env, dict) and isinstance(env.get("status"), str) and isinstance(env.get("summary"), str)):
        problems.append("the envelope must be a JSON object with a string status and a string summary")
    env = env if isinstance(env, dict) else {}
    status = env["status"] if isinstance(env.get("status"), str) else None

    if status is not None and status != "ERROR" and status not in next_step.accepted_statuses(phase, agent, d.get("mode")):
        problems.append(f"status {status} is not allowed for {agent} in {phase} mode {d.get('mode')}")
    if status != "ERROR":
        if agent in BUILDERS:
            dirty = repo.out("status", "--porcelain", "--untracked-files=normal").strip()
            if dirty:
                problems.append("the worktree has uncommitted changes or untracked files: " + dirty)
        else:
            log = repo.out("log", "--format=%(trailers:key=Forge-Agent,valueonly,separator=%x0a)",
                           f"{d['base_sha']}..HEAD")
            if agent in [a.strip() for a in log.splitlines()]:
                problems.append(f"a commit since {d['base_sha']} carries Forge-Agent {agent}, which does not commit")

    esc = env.get("escalation") if isinstance(env.get("escalation"), dict) else {}
    if status in ESCALATING and not text(esc.get("details")):
        problems.append(f"{status} needs escalation.details, a non-empty string")
    if status == "HUMAN_CODE_CHANGE_REQUESTED":
        paths, ranges = esc.get("paths"), esc.get("ranges")
        if not str_list(paths):
            problems.append("escalation.paths must be a list of strings")
        elif not (isinstance(ranges, dict) and all(line_pairs(ranges.get(p)) for p in paths)):
            problems.append("escalation.ranges must map each listed path to a list of [start, end] integer pairs")
    if status == "CHANGES_REQUESTED":
        findings = esc.get("findings")
        if not (isinstance(findings, list) and findings and all(
                isinstance(f, dict) and f.get("route_to") in BUILDERS and text(f.get("detail")) for f in findings)):
            problems.append("escalation.findings must be a non-empty list of {route_to, detail} with route_to one "
                            "of forge-planner, forge-qa, forge-dev and a non-empty string detail")

    scope = env.get("scope") if isinstance(env.get("scope"), dict) else {}
    if (status == "READY_FOR_QA" or (status == "REPAIR_COMMITTED" and agent == "forge-planner"
                                     and "assigned" in scope)) and not str_list(scope.get("assigned")):
        problems.append("scope.assigned must be a list of strings")
    if status in ("PARTIAL_REMAINING", "NOTHING_REMAINING") or (status == "PASS" and phase == "RECONCILE"):
        assigned = (state.get("scope_manifest") or {}).get("assigned") or []
        done, left = scope.get("completed"), scope.get("remaining")
        if not (str_list(done) and str_list(left) and sorted(done + left) == sorted(set(assigned))):
            problems.append("scope.completed and scope.remaining must be lists of strings that together hold each "
                            "pattern of scope_manifest.assigned exactly once")

    if status == "COUNCIL_RULING":
        problems += ruling_problems(env.get("ruling"), council)
    if status in PUBLISHING and env.get("publish_target") != lc["invoke"].get("mode"):
        problems.append(f"publish_target must be {lc['invoke'].get('mode')}")
    if status == "PR_REVIEW_READY" and not review_ok(env.get("review")):
        problems.append("review must be {verdict, body, comments} with verdict approve, request_changes, or "
                        "comment, a string body, and comments a list of {path, line, body}")
    if status == "REPORT_DRAFTED" and not report_ok(env.get("report")):
        problems.append("report must be a well-formed pr_create, pr_update, or pr_review draft")
    report = env.get("report") if isinstance(env.get("report"), dict) else {}
    if status == "REPORT_SENT" and not text(report.get("url")):
        problems.append("report.url must be a non-empty string")
    return problems


# @spec SPOKE-PROTO-FIN-001, SPOKE-PROTO-FIN-002, SPOKE-PROTO-FIN-003, SPOKE-PROTO-FIN-004
# @spec SPOKE-PROTO-FIN-019, SPOKE-PROTO-FIN-020
def finish(state, repo, sd):
    d = state["lifecycle"]["dispatched"]
    path = sd / "envelopes" / f"{d['dispatch_id']}.json"
    if not path.is_file():
        return {"status": "ENVELOPE_MISSING", "path": str(path)}
    try:
        env = json.loads(path.read_text())
    except ValueError:
        env = None
    problems = envelope_problems(env, state, repo, sd / "council")
    if problems:
        return {"status": "INVALID", "problems": problems}
    agent, base = d["agent"], d["base_sha"]
    if agent in BUILDERS:
        head_sha = repo.out("rev-parse", "HEAD").strip()
        touched = sorted({p for _, a, paths in first_parent_commits(repo, f"{base}..{head_sha}") if a == agent
                          for p in paths})
    else:
        head_sha, touched = base, []
    env.update(dispatch_id=d["dispatch_id"], agent=agent, head_sha=head_sha, files_touched=touched)
    path.write_text(json.dumps(env, indent=2) + "\n")
    return {"status": "FINISHED"}


# @spec SPOKE-PROTO-SCOPE-001, SPOKE-PROTO-SCOPE-002, SPOKE-PROTO-SCOPE-003, SPOKE-PROTO-SCOPE-004
# @spec SPOKE-PROTO-SCOPE-005
def scope(state, repo):
    manifest = state.get("scope_manifest") or {}
    patterns = (state.get("config") or {}).get("test_patterns")
    plan_base = (state.get("anchors") or {}).get("plan_base_sha")
    tracked = repo.out("ls-tree", "-r", "--name-only", "HEAD").splitlines()
    commits = first_parent_commits(repo, f"{plan_base}..HEAD") if plan_base else []
    entries = []
    for pattern in manifest.get("assigned") or []:
        hits = []
        for sha, agent, paths in commits:
            matching = [p for p in paths if matches(p, pattern)]
            if matching:
                hits.append({"sha": sha, "kind": "forge" if agent else "human", "agent": agent, "paths": matching})
        entries.append({"pattern": pattern,
                        "files": [{"path": p, "class": classify_path(p, patterns)} for p in tracked
                                  if matches(p, pattern)],
                        "commits": hits})
    return {"status": "OK", "patterns": entries, "completed": manifest.get("completed"),
            "remaining": manifest.get("remaining")}


# --------------------------------------------------------------------------- CLI


# @spec SPOKE-PROTO-CLI-001, SPOKE-PROTO-CLI-002, SPOKE-PROTO-CLI-003, SPOKE-PROTO-CLI-004, SPOKE-PROTO-CLI-005
# @spec SPOKE-PROTO-COMMIT-006
def main(argv=None):
    parser = argparse.ArgumentParser(prog="forge_spoke.py")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("invocation", "commit", "finish", "scope"):
        p = sub.add_parser(name)
        p.add_argument("--state", required=True)
        if name != "scope":
            p.add_argument("--dispatch", required=True)
        if name == "commit":
            p.add_argument("--message-file", required=True)
            p.add_argument("--stage")
    args = parser.parse_args(argv)  # exits 2 on usage errors
    message = None
    if args.command == "commit":
        try:
            message = Path(args.message_file).read_text()
        except (OSError, ValueError) as e:
            parser.error(f"cannot read --message-file {args.message_file}: {e}")
        if not message.strip():
            parser.error(f"--message-file {args.message_file} is empty")

    state, detail = load_state(args.state)
    if state is None:
        result = {"status": "STATE_INVALID", "detail": detail}
    elif args.command != "scope" and (((state.get("lifecycle") or {}).get("dispatched") or {})
                                      .get("dispatch_id") != args.dispatch):
        result = {"status": "NOT_DISPATCHED", "detail": f"dispatch {args.dispatch} is not outstanding"}
    elif args.command == "invocation":
        result = invocation(state)
    else:
        try:
            repo = verify_gate.Repo(state["identity"]["worktree_path"])
            if args.command == "commit":
                result = commit(state, repo, message, args.stage)
            elif args.command == "finish":
                result = finish(state, repo, Path(args.state).resolve().parent)
            else:
                result = scope(state, repo)
        except verify_gate.GitError as e:
            print(f"forge_spoke.py: {e}", file=sys.stderr)
            return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
