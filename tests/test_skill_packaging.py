"""Skill and plugin manifests stay installable in Claude Code / Cowork."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import bundle_skill  # noqa: E402

SKILL_DIR = REPO_ROOT / "skills" / "remove-ai-marks"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"


def test_skill_passes_upload_validation():
    meta = bundle_skill.validate(SKILL_DIR)
    assert meta["name"] == "remove-ai-marks"


def test_frontmatter_uses_only_spec_fields():
    meta = bundle_skill.parse_frontmatter((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"))
    # Anything outside the Agent Skills spec is a hard error on claude.ai
    # uploads, which is how Cowork and cloud sessions get the skill.
    assert set(meta) <= bundle_skill.SPEC_FIELDS


def test_skill_ships_no_code():
    # The skill is a thin HTTP client: markdown only, so it stays installable on
    # hosts with no Python and small enough to upload.
    payload = [path.suffix for path in bundle_skill.bundle_files(SKILL_DIR)]
    assert set(payload) == {".md"}


def test_referenced_references_exist():
    body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for name in ("mark-classes.md", "vendor-notes.md", "removal-matrix.md", "ethics.md"):
        assert (SKILL_DIR / "references" / name).is_file(), name
        assert name in body


def test_skill_documents_the_sandbox_service_url():
    body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "WATERMARKS_SERVICE_URL" in body
    assert "<skill_dir>" not in body


def test_bundle_has_single_top_level_directory(tmp_path):
    archive_path = bundle_skill.bundle(tmp_path / "skill.zip", SKILL_DIR)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
    assert {name.split("/")[0] for name in names} == {"remove-ai-marks"}
    assert "remove-ai-marks/SKILL.md" in names
    assert not [name for name in names if "__pycache__" in name or name.endswith(".pyc")]


def test_bundle_stays_under_upload_limit():
    total = sum(path.stat().st_size for path in bundle_skill.bundle_files(SKILL_DIR))
    assert total < bundle_skill.MAX_BUNDLE_BYTES


@pytest.mark.parametrize(
    "frontmatter, expected",
    [
        ("---\nname: remove-ai-marks\ndescription: x\nargument-hint: y\n---\n", "Unexpected key"),
        ("---\nname: Remove_AI\ndescription: x\n---\n", "lowercase"),
        ("---\nname: remove-ai-marks\ndescription: >\n  folded\n---\n", "block scalars"),
        ("---\nname: remove-ai-marks\n---\n", "'description' is required"),
    ],
)
def test_validation_rejects_unportable_frontmatter(tmp_path, frontmatter, expected):
    skill_dir = tmp_path / "remove-ai-marks"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(frontmatter, encoding="utf-8")
    with pytest.raises(bundle_skill.SkillError, match=expected):
        bundle_skill.validate(skill_dir)


def test_plugin_manifest_is_valid():
    plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    assert plugin["name"] == "watermarks-remover"
    # Claude Code discovers plugin skills at <plugin root>/skills/<name>/SKILL.md.
    assert (REPO_ROOT / "skills" / "remove-ai-marks" / "SKILL.md").is_file()


def test_marketplace_manifest_is_valid():
    marketplace = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
    assert marketplace["name"]
    assert marketplace["owner"]["name"]
    entries = marketplace["plugins"]
    assert [entry["name"] for entry in entries] == ["watermarks-remover"]
    for entry in entries:
        source = entry["source"]
        assert isinstance(source, str) and source.startswith("./")
        assert (REPO_ROOT / source / ".claude-plugin" / "plugin.json").is_file()


def test_marketplace_does_not_duplicate_the_plugin_version():
    # Claude Code always takes plugin.json's version, so a second one here
    # would silently go stale.
    marketplace = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
    assert all("version" not in entry for entry in marketplace["plugins"])
