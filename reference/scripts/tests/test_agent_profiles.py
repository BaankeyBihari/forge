"""Tests for the spoke agent profiles claude/agents/forge-*.md
(docs/intent/spokes/protocol/protocol-specs.md, *Agent profiles*).

Frontmatter is read with a tiny parser: `key: value` lines, where a value may be a
comma-separated string, an inline `[a, b]` list, or a block of `- item` lines.
"""

from pathlib import Path

import pytest

AGENTS = Path(__file__).resolve().parents[3] / "claude" / "agents"
EDIT_TOOLS = {"Read", "Grep", "Glob", "Bash", "Write", "Edit"}
READ_TOOLS = {"Read", "Grep", "Glob", "Bash", "Write"}
PERSONAS = ["pm", "architect", "sec-ops", "debugger", "senior-dev", "critic"]
SPOKES = ["forge-planner", "forge-qa", "forge-dev", "forge-verifier", "forge-reviewer", "forge-scout",
          "forge-publisher", "forge-reporter", "forge-council"]


def parse_profile(text):
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "profile has no frontmatter"
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    meta, key = {}, None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and key:
            meta[key].append(stripped[2:].strip().strip("'\""))
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not value:
            meta[key] = []
        elif value.startswith("[") and value.endswith("]"):
            meta[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key] = value.strip("'\"")
    return meta, "\n".join(lines[end + 1:])


def as_set(value):
    if value is None:
        return set()
    if isinstance(value, str):
        return {v.strip() for v in value.split(",") if v.strip()}
    return set(value)


def profile(name):
    path = AGENTS / f"{name}.md"
    assert path.is_file(), f"claude/agents/{name}.md is not implemented yet"
    return parse_profile(path.read_text())


def assert_profile(name, model, tools, skills):
    meta, _ = profile(name)
    assert meta.get("model") == model
    assert as_set(meta.get("tools")) == tools
    assert as_set(meta.get("skills")) == skills


# @spec SPOKE-PROTO-PROF-001
def test_planner_profile():
    assert_profile("forge-planner", "opus", EDIT_TOOLS,
                   {"forge-spoke", "forge-memory", "forge-planner", "linked-intent-dev"})


# @spec SPOKE-PROTO-PROF-002
@pytest.mark.parametrize("name", ["forge-qa", "forge-dev"])
def test_qa_and_dev_profiles(name):
    assert_profile(name, "sonnet", EDIT_TOOLS, {"forge-spoke", "forge-memory", name})


# @spec SPOKE-PROTO-PROF-003
def test_verifier_profile():
    assert_profile("forge-verifier", "sonnet", READ_TOOLS, {"forge-spoke", "forge-memory", "forge-verifier"})


# @spec SPOKE-PROTO-PROF-004
def test_reviewer_profile():
    assert_profile("forge-reviewer", "opus", READ_TOOLS, {"forge-spoke", "forge-memory", "forge-reviewer"})


# @spec SPOKE-PROTO-PROF-005
@pytest.mark.parametrize("name", ["forge-scout", "forge-reporter"])
def test_scout_and_reporter_profiles(name):
    assert_profile(name, "haiku", READ_TOOLS, {"forge-spoke", "forge-memory", name})


# @spec SPOKE-PROTO-PROF-006
def test_publisher_profile():
    assert_profile("forge-publisher", "haiku", {"Read", "Bash", "Write"},
                   {"forge-spoke", "forge-memory", "forge-publisher"})


# @spec SPOKE-PROTO-PROF-007
def test_council_profile():
    assert_profile("forge-council", "sonnet", READ_TOOLS | {"Agent"},
                   {"forge-spoke", "forge-memory", "forge-council"})


# @spec SPOKE-PROTO-PROF-008
@pytest.mark.parametrize("persona", PERSONAS)
def test_council_persona_profiles(persona):
    assert_profile(f"forge-council-{persona}", "opus", {"Read", "Grep", "Glob"}, set())


# @spec SPOKE-PROTO-PROF-009
@pytest.mark.parametrize("name", SPOKES)
def test_spoke_body_names_agent_and_forge_spoke(name):
    _, body = profile(name)
    assert name in body
    assert "forge-spoke" in body
