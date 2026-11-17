#!/usr/bin/env python3
"""Mechanical steps of the Forge Commander (Hub), shared by every CLI's Commander.

Design: docs/intent/commander/commander-design.md.
Specs: docs/intent/commander/commander-specs.md.

    forge_hub.py classify [WORD ...]
    forge_hub.py brief    --holder-pid PID --holder-session ID [--workstream SLUG]
    forge_hub.py envelope --state PATH [--discard]
    forge_hub.py answer   --state PATH --decision DECISION [--option OPTION] [--notes-file FILE]
    forge_hub.py mode     --state PATH --mode MODE
    forge_hub.py stop | unstop

Every status prints one JSON object and exits 0. Usage errors exit 2; classify or
brief outside a git repository exits 1; each with a message on stderr.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import forge_memory  # noqa: E402
import next_step  # noqa: E402
import resolve_state  # noqa: E402

PR_URL = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+/pull/[1-9]\d*/?$")
ISSUE = re.compile(r"^(https://github\.com/[^/\s]+/[^/\s]+/issues/[1-9]\d*/?|#?[1-9]\d*)$")


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ------------------------------------------------------------------ classify


MODES = resolve_state.MODES
START_KINDS = {"issue", "pr", "adopt", "unknown", "describe"}


# @spec CMD-ARG-001, CMD-ARG-002, CMD-ARG-003, CMD-ARG-004, CMD-ARG-005, CMD-ARG-006, CMD-ARG-007
# @spec CMD-ARG-008, CMD-ARG-009, CMD-ARG-010, CMD-ARG-011, CMD-ARG-012, CMD-ARG-013, CMD-ARG-014
# @spec CMD-ARG-015, CMD-ARG-016, CMD-ARG-017
def classify(cwd, words):
    checkout = resolve_state.Checkout(cwd)
    if not words:
        return {"kind": "current"}
    if words == ["list"]:
        return {"kind": "list"}
    if words == ["stop"]:
        return {"kind": "stop"}
    if words == ["resume"]:
        return {"kind": "continue"}
    if len(words) == 2 and words[0] == "mode" and words[1] in MODES:
        return {"kind": "mode", "mode": words[1]}
    if (len(words) >= 3 and words[0] == "drop" and resolve_state.valid_slug(words[1])
            and words[1] not in (".", "..") and (checkout.repo_dir() / words[1]).is_dir()):
        return {"kind": "drop", "workstream": words[1], "reason": " ".join(words[2:])}
    if words[0] == "--mode":
        if len(words) >= 3 and words[1] in MODES and words[2] != "--mode":
            rest = classify(cwd, words[2:])
            if rest["kind"] in START_KINDS:
                return {**rest, "mode": words[1]}
        return {"kind": "invalid", "detail": "expected /forge --mode <mode> <issue, pr, adopt, or description>, "
                "with <mode> one of " + ", ".join(MODES)}
    if len(words) == 2 and words[0] == "mode":
        return {"kind": "invalid", "detail": "expected /forge mode <mode>, with <mode> one of " + ", ".join(MODES)}
    if words[0] in ("stop", "resume"):
        return {"kind": "invalid", "detail": f"expected /forge {words[0]} with nothing after it"}
    if (len(words) == 2 and words[0] == "pr") or (len(words) == 1 and PR_URL.match(words[0])):
        return {"kind": "pr", "ref": words[-1]}
    if len(words) == 2 and words[0] == "adopt":
        return {"kind": "adopt", "branch": words[1]}
    if words[0] in ("pr", "adopt"):
        form = "pr <number or URL>" if words[0] == "pr" else "adopt <branch>"
        return {"kind": "invalid", "detail": f"expected /forge {form}"}
    if len(words) == 1 and ISSUE.match(words[0]):
        return {"kind": "issue", "ref": words[0]}
    if len(words) == 1 and resolve_state.valid_slug(words[0]):
        known = (checkout.repo_dir() / words[0]).is_dir()
        return {"kind": "resume" if known else "unknown", "workstream": words[0]}
    return {"kind": "describe", "text": " ".join(words)}


# --------------------------------------------------------------------- brief


def read_object(path):
    """Return (object, None) or (None, reason) for a JSON-object file."""
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None, f"{path} is missing"
    except (OSError, ValueError) as e:
        return None, f"{path} does not parse: {e}"
    if not isinstance(data, dict):
        return None, f"{path} is not a JSON object"
    return data, None


def hub_context(state_path):
    out = forge_memory.load(state_path, "hub", 20)
    for e in out.get("entries", []):
        if e["partition"] == "hub" and not e["historical"] and e["decisions"]:
            return e["decisions"][0]
    return "none recorded"


# @spec CMD-BRIEF-001, CMD-BRIEF-002, CMD-BRIEF-003, CMD-BRIEF-004, CMD-BRIEF-005, CMD-BRIEF-006
# @spec CMD-BRIEF-007, CMD-BRIEF-008, CMD-BRIEF-009, CMD-BRIEF-010, CMD-BRIEF-011
def brief(cwd, workstream, pid, session):
    report = resolve_state.wake(cwd, workstream, pid, session)
    if report["status"] != "OK":
        return report
    sp = Path(report["state_path"])
    state, _ = resolve_state.load_state(sp)
    ident, config, lc = state["identity"], state.get("config") or {}, state["lifecycle"]
    scope, blockers = state.get("scope_manifest") or {}, state.get("blockers") or []

    line1 = (f"Workstream: {ident['org']}/{ident['repo']}/{ident['workstream']} "
             f"({config.get('repo_mode')}, mode {config.get('mode', 'collab')})")
    last = f"{lc['last_agent']} → {lc.get('last_status')}" if lc.get("last_agent") else "nothing run yet"
    line2 = f"Phase: {lc['current_phase']} ({last})"
    assigned = scope.get("assigned") or []
    if assigned:
        remaining = ", ".join(scope.get("remaining") or []) or "none"
        line3 = (f"Progress: {len(scope.get('completed') or [])} of {len(assigned)} scope patterns complete; "
                 f"remaining: {remaining}")
    else:
        line3 = "Progress: no plan yet"
    if blockers:
        line3 += f"; open blockers: {len(blockers)}"
    line4 = f"Hub context: {hub_context(sp)}"

    pending = False
    d = lc.get("dispatched")
    env_path = sp.parent / "envelopes" / f"{d['dispatch_id']}.json" if d else None
    if env_path and env_path.is_file():
        pending = True
        env, _ = read_object(env_path)
        status = env.get("status") if env else None
        line5 = f"Next step: Apply the result {d['agent']} left ({status if status else 'unreadable'})"
    elif lc["current_phase"] == "INIT":
        line5 = "Next step: Start routing (nothing has run yet)"
    else:
        decision = next_step.route(cwd, workstream, pid, session, None, dry_run=True)
        if decision["action"] == "pause":
            line5 = f"Next step: Waiting for you: {decision['question']}"
        elif decision["action"] == "stop":
            line5 = f"Next step: Stopped: {decision['status']}: {decision['detail']}"
        else:
            line5 = f"Next step: {decision['summary']}"
    return {"status": "OK", "lines": [line1, line2, line3, line4, line5], "pending_envelope": pending}


# ------------------------------------------------------------------ envelope


def load_lifecycle(state_path):
    state, err = read_object(state_path)
    if err:
        return None, {"status": "STATE_INVALID", "detail": err}
    return state.get("lifecycle") or {}, None


# @spec CMD-ENV-001, CMD-ENV-002, CMD-ENV-003, CMD-ENV-004, CMD-ENV-005, CMD-ENV-006, CMD-ENV-007
def envelope(state_path, discard=False):
    state_path = Path(state_path).absolute()
    lc, err = load_lifecycle(state_path)
    if err:
        return err
    envelopes = state_path.parent / "envelopes"
    envelopes.mkdir(exist_ok=True)
    d = lc.get("dispatched")
    if not d:
        return {"status": "NO_DISPATCH"}
    path = envelopes / f"{d['dispatch_id']}.json"
    if not path.is_file():
        return {"status": "ENVELOPE_MISSING", "path": str(path)}
    if discard:
        moved = envelopes / f"{d['dispatch_id']}.rejected-{stamp()}.json"
        path.rename(moved)
        return {"status": "DISCARDED", "path": str(moved)}
    body, err = read_object(path)
    if err:
        return {"status": "ENVELOPE_UNREADABLE", "path": str(path), "detail": err}
    event_path = envelopes / f"{d['dispatch_id']}.event.json"
    event_path.write_text(json.dumps({"type": "result", "envelope": body}))
    return {"status": "OK", "event_path": str(event_path)}


# -------------------------------------------------------------------- answer


# @spec CMD-ANS-001, CMD-ANS-002, CMD-ENV-006
def answer(state_path, decision, option=None, notes=None):
    state_path = Path(state_path).absolute()
    lc, err = load_lifecycle(state_path)
    if err:
        return err
    awaiting = lc.get("awaiting")
    if not awaiting:
        return {"status": "NOT_PAUSED"}
    event = {"type": "user", "awaiting": awaiting["kind"], "decision": decision}
    if option is not None:
        event["option"] = option
    if notes is not None:
        event["notes"] = notes
    envelopes = state_path.parent / "envelopes"
    envelopes.mkdir(exist_ok=True)
    # ponytail: second-resolution name; two answers in one second overwrite (accepted in design).
    event_path = envelopes / f"answer-{stamp()}.event.json"
    event_path.write_text(json.dumps(event))
    return {"status": "OK", "event_path": str(event_path)}


# ---------------------------------------------------------- mode, stop, unstop


# @spec CMD-MODE-001, CMD-ENV-006
def mode_event(state_path, mode):
    state_path = Path(state_path).absolute()
    _, err = load_lifecycle(state_path)
    if err:
        return err
    envelopes = state_path.parent / "envelopes"
    envelopes.mkdir(exist_ok=True)
    event_path = envelopes / f"mode-{stamp()}.event.json"
    event_path.write_text(json.dumps({"type": "mode", "mode": mode}))  # the router checks the name
    return {"status": "OK", "event_path": str(event_path)}


def worktree_roots(cwd):
    lines = resolve_state.git(cwd, "worktree", "list", "--porcelain").stdout.splitlines()
    return [line.split(" ", 1)[1] for line in lines if line.startswith("worktree ")]


# @spec CMD-KILL-001, CMD-KILL-003, CMD-KILL-004
def stop(cwd):
    path = Path(worktree_roots(cwd)[0]) / ".STOP_FORGE"
    path.touch(exist_ok=True)
    return {"status": "STOPPED", "path": str(path)}


# @spec CMD-KILL-002, CMD-KILL-004
def unstop(cwd):
    removed = []
    for root in worktree_roots(cwd):
        path = Path(root) / ".STOP_FORGE"
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    return {"status": "CLEARED", "removed": removed}


# ----------------------------------------------------------------------- CLI


# @spec CMD-CLI-001, CMD-CLI-002, CMD-CLI-003, CMD-ANS-003, CMD-KILL-004
def main(argv=None):
    parser = argparse.ArgumentParser(prog="forge_hub.py")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("classify")
    p.add_argument("words", nargs="*")
    p = sub.add_parser("brief")
    p.add_argument("--holder-pid", required=True, type=int)
    p.add_argument("--holder-session", required=True)
    p.add_argument("--workstream")
    p = sub.add_parser("envelope")
    p.add_argument("--state", required=True)
    p.add_argument("--discard", action="store_true")
    p = sub.add_parser("answer")
    p.add_argument("--state", required=True)
    p.add_argument("--decision", required=True)
    p.add_argument("--option")
    p.add_argument("--notes-file")
    p = sub.add_parser("mode")
    p.add_argument("--state", required=True)
    p.add_argument("--mode", required=True)
    sub.add_parser("stop")
    sub.add_parser("unstop")
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["classify"]:  # words may look like options (--mode)
        args = argparse.Namespace(command="classify", words=argv[1:])
    else:
        args = parser.parse_args(argv)
    cwd = os.getcwd()
    try:
        if args.command == "classify":
            result = classify(cwd, args.words)
        elif args.command == "mode":
            result = mode_event(args.state, args.mode)
        elif args.command == "stop":
            result = stop(cwd)
        elif args.command == "unstop":
            result = unstop(cwd)
        elif args.command == "brief":
            result = brief(cwd, args.workstream, args.holder_pid, args.holder_session)
        elif args.command == "envelope":
            result = envelope(args.state, args.discard)
        else:
            notes = None
            if args.notes_file is not None:
                try:
                    notes = Path(args.notes_file).read_text()
                except OSError as e:
                    parser.error(f"cannot read --notes-file: {e}")
            result = answer(args.state, args.decision, args.option, notes)
    except resolve_state.UsageError as e:
        print(f"forge_hub.py: {e}", file=sys.stderr)
        return 2
    except resolve_state.GitError as e:
        print(f"forge_hub.py: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
