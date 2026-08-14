#!/usr/bin/env python3
"""Validate and package the skill for Claude Code, Cowork and claude.ai.

Validation follows the Agent Skills spec as enforced by the Skills API and
claude.ai uploads (which is what Cowork and cloud sessions install from):
only six frontmatter fields are allowed, the directory name must match `name`,
and the bundle must stay under 30 MB uncompressed with SKILL.md at the root of
a single top-level directory.

Usage:
    python3 tools/bundle_skill.py --check            # validate only
    python3 tools/bundle_skill.py -o dist/skill.zip  # validate and package
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "remove-ai-marks"

# The Agent Skills spec fields accepted by claude.ai uploads, the Skills API and
# `package_skill.py`. Claude Code accepts more, but anything else here is a hard
# error on the upload path used by Cowork.
SPEC_FIELDS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}

NAME_RE = re.compile(r"^[a-z0-9-]+$")
RESERVED_NAME_WORDS = ("anthropic", "claude")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_COMPATIBILITY = 500
MAX_BUNDLE_BYTES = 30 * 1024 * 1024

EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".git", ".venv"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


class SkillError(Exception):
    """Raised when the skill does not satisfy the upload requirements."""


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            return inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return value


def parse_frontmatter(text: str) -> dict[str, object]:
    """Parse the subset of YAML this skill's frontmatter is allowed to use.

    Supports `key: scalar` and one level of nested maps. Anything else (folded
    or literal blocks, sequences) raises, because those shapes are exactly what
    tends to differ between YAML parsers on the upload path.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("SKILL.md must start with a '---' frontmatter fence")
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        raise SkillError("SKILL.md frontmatter is not closed with '---'") from None

    data: dict[str, object] = {}
    current_key: str | None = None
    for lineno, raw in enumerate(lines[1:end], start=2):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw[:1].isspace()
        if ":" not in raw:
            raise SkillError(f"SKILL.md line {lineno}: expected 'key: value', got {raw!r}")
        key, _, value = raw.partition(":")
        key, value = key.strip(), value.strip()
        if value in (">", "|", ">-", "|-"):
            raise SkillError(
                f"SKILL.md line {lineno}: block scalars are not portable, "
                "use a single quoted line"
            )
        if raw.lstrip().startswith("- "):
            raise SkillError(f"SKILL.md line {lineno}: sequences are not supported")
        if indented:
            if current_key is None or not isinstance(data.get(current_key), dict):
                raise SkillError(f"SKILL.md line {lineno}: unexpected indented key {key!r}")
            data[current_key][key] = _unquote(value)  # type: ignore[index]
            continue
        current_key = key
        data[key] = {} if value == "" else _unquote(value)
    return data


def validate(skill_dir: Path = SKILL_DIR) -> dict[str, object]:
    """Validate the skill directory; return its parsed frontmatter."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SkillError(f"missing {skill_md}")

    meta = parse_frontmatter(skill_md.read_text(encoding="utf-8"))

    unexpected = sorted(set(meta) - SPEC_FIELDS)
    if unexpected:
        raise SkillError(
            "Unexpected key(s) in SKILL.md frontmatter: "
            + ", ".join(unexpected)
            + ". Allowed properties are: "
            + ", ".join(sorted(SPEC_FIELDS))
        )

    name = meta.get("name")
    if not isinstance(name, str) or not name:
        raise SkillError("frontmatter 'name' is required")
    if len(name) > MAX_NAME:
        raise SkillError(f"frontmatter 'name' exceeds {MAX_NAME} characters")
    if not NAME_RE.match(name):
        raise SkillError("frontmatter 'name' must be lowercase letters, digits and hyphens")
    if any(word in name for word in RESERVED_NAME_WORDS):
        raise SkillError("frontmatter 'name' must not contain a reserved word")
    if name != skill_dir.name:
        raise SkillError(f"frontmatter 'name' ({name}) must match the directory ({skill_dir.name})")

    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SkillError("frontmatter 'description' is required")
    if len(description) > MAX_DESCRIPTION:
        raise SkillError(f"frontmatter 'description' exceeds {MAX_DESCRIPTION} characters")

    compatibility = meta.get("compatibility", "")
    if isinstance(compatibility, str) and len(compatibility) > MAX_COMPATIBILITY:
        raise SkillError(f"frontmatter 'compatibility' exceeds {MAX_COMPATIBILITY} characters")

    for field in ("description", "compatibility"):
        value = meta.get(field, "")
        if isinstance(value, str) and re.search(r"<[a-zA-Z/][^>]*>", value):
            raise SkillError(f"frontmatter '{field}' must not contain XML tags")

    total = sum(path.stat().st_size for path in bundle_files(skill_dir))
    if total >= MAX_BUNDLE_BYTES:
        raise SkillError(f"skill bundle is {total} bytes, the upload limit is {MAX_BUNDLE_BYTES}")

    return meta


def bundle_files(skill_dir: Path = SKILL_DIR) -> list[Path]:
    """Files that belong in the uploadable bundle, sorted for reproducibility."""
    files = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        if EXCLUDED_DIRS.intersection(path.relative_to(skill_dir).parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES:
            continue
        files.append(path)
    return files


def bundle(output: Path, skill_dir: Path = SKILL_DIR) -> Path:
    """Write a zip whose single top-level entry is the skill directory."""
    validate(skill_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in bundle_files(skill_dir):
            arcname = Path(skill_dir.name) / path.relative_to(skill_dir)
            archive.write(path, arcname.as_posix())
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-dir", type=Path, default=SKILL_DIR)
    parser.add_argument("-o", "--output", type=Path, help="zip path to write")
    parser.add_argument("--check", action="store_true", help="validate only")
    args = parser.parse_args(argv)

    try:
        meta = validate(args.skill_dir)
        if args.check or not args.output:
            print(f"ok: {args.skill_dir.name} ({len(str(meta['description']))} char description)")
            return 0
        path = bundle(args.output, args.skill_dir)
    except SkillError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
