"""Tests for the forge-github-gh skill layout (GH-LAYOUT-*) and the scout skill's
fenced code blocks (SPOKE-SCOUT-GH-002)."""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "reference" / "skills" / "forge-github-gh"
GH_CMD = re.compile(r"(^|[ (|;&])gh ")


# @spec GH-LAYOUT-001
def test_skill_holds_index_and_reference_files():
    for rel in ["SKILL.md", "references/issues.md", "references/pulls.md", "references/ci.md"]:
        assert (SKILL / rel).is_file(), f"{rel} missing"


# @spec GH-LAYOUT-002
def test_claude_skill_links_to_reference_skill():
    link = ROOT / "claude" / "skills" / "forge-github-gh"
    assert link.is_symlink()
    assert os.readlink(link) == "../../reference/skills/forge-github-gh"


# @spec SPOKE-SCOUT-GH-002
def test_scout_skill_has_no_gh_command_in_code_blocks():
    text = (ROOT / "reference" / "skills" / "forge-scout" / "SKILL.md").read_text()
    inside, bad = False, []
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            inside = not inside
        elif inside and GH_CMD.search(line):
            bad.append(line)
    assert not bad, bad
