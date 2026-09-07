"""Validate retained prompt bytes and native measurement metadata before publishing."""

from __future__ import annotations

import argparse
import hashlib
import re
from datetime import datetime
from pathlib import Path

import yaml


class ArchiveError(ValueError):
    pass


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    pairs = loader.construct_pairs(node)
    mapping = {}
    for key, value in pairs:
        if key in mapping:
            raise ArchiveError("duplicate YAML key")
        mapping[key] = value
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def require(condition, message):
    if not condition:
        raise ArchiveError(message)


def natural(value):
    return type(value) is int and value >= 0


def timestamp(value):
    require(isinstance(value, str), "missing measurement timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArchiveError("invalid measurement timestamp") from exc
    require(parsed.tzinfo is not None, "measurement timestamp has no timezone")


def validate_tools(tools, partial):
    require(isinstance(tools, list) and bool(tools), "missing native tool measurements")
    names = set()
    unavailable = False
    for tool in tools:
        require(isinstance(tool, dict), "invalid tool measurement")
        name = tool.get("canonical_name")
        require(isinstance(name, str) and bool(name) and name not in names,
                "missing or duplicate tool name")
        names.add(name)
        aliases = tool.get("observed_raw_aliases")
        require(isinstance(aliases, list) and bool(aliases)
                and all(isinstance(alias, str) and alias for alias in aliases),
                "missing tool aliases")
        size = tool.get("definition_bytes")
        require(natural(size) and size > 0, "invalid tool definition size")
        count = tool.get("definition_token_count")
        reason = tool.get("definition_token_count_unavailable_reason")
        if count is None:
            require(partial and isinstance(reason, str) and bool(reason),
                    "tool token count missing without an unavailable reason")
            unavailable = True
        else:
            require(natural(count) and count > 0 and not reason,
                    "invalid or conflicting tool token count")
    require(partial == unavailable, "partial measurement status disagrees with tool counts")


def validate_entry(entry, version_dir, provider):
    require(isinstance(entry, dict), "invalid prompt entry")
    model = entry.get("model")
    require(isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", model),
            "invalid model ID")
    measurement = entry.get("token_measurement")
    require(isinstance(measurement, dict), "missing token measurement provenance")
    status = measurement.get("status")
    require(status in {"measured", "partial", "unavailable"}, "invalid measurement status")
    require(measurement.get("provider") == provider, "measurement provider mismatch")
    require(measurement.get("model") == model, "measurement model mismatch")
    count = entry.get("token_count")
    if status == "unavailable":
        require(count is None and bool(measurement.get("failure_code")),
                "unavailable measurement must have a reason and no count")
    else:
        require(natural(count) and count > 0, "invalid native prompt token count")
        require(bool(measurement.get("method")), "missing measurement method")
        timestamp(measurement.get("measured_at"))
        validate_tools(entry.get("tools"), status == "partial")

    directory = entry.get("directory")
    if directory is None:
        require(status == "unavailable" and entry.get("content") == "unretained",
                "missing prompt directory without explicit unretained provenance")
        return
    require(isinstance(directory, str) and directory not in {".", ".."}
            and re.fullmatch(r"[A-Za-z0-9_.-]+", directory), "invalid prompt directory")
    require(directory == model or (directory == "default" and model == "unrecorded"),
            "prompt directory and model disagree")
    prompt_dir = version_dir / directory
    for extension in ("txt", "md"):
        path = prompt_dir / f"systemprompt.{extension}"
        require(path.resolve().is_relative_to(version_dir.resolve()),
                "prompt path escapes version directory")
        require(path.is_file(), f"missing systemprompt.{extension}")
    raw = (prompt_dir / "systemprompt.txt").read_bytes()
    require(len(raw) <= 4 * 1024 * 1024, "prompt exceeds capture size bound")
    try:
        prompt = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArchiveError("prompt is not UTF-8") from exc
    require(entry.get("character_count") == len(prompt), "prompt character count mismatch")
    digest = entry.get("content_sha256")
    require(isinstance(digest, str) and digest == hashlib.sha256(raw).hexdigest(),
            "prompt SHA-256 mismatch")


def validate_metadata(path, provider):
    try:
        metadata = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ArchiveError("invalid YAML metadata") from exc
    require(isinstance(metadata, dict), "invalid version metadata")
    require(metadata.get("version") == path.parent.name, "version directory mismatch")
    entries = metadata.get("system_prompts")
    require(isinstance(entries, list) and bool(entries), "missing prompt entries")
    seen = set()
    for entry in entries:
        validate_entry(entry, path.parent, provider)
        model = entry["model"]
        require(model not in seen, "duplicate model entry")
        seen.add(model)
    return len(entries)


def validate_archive(root):
    versions = prompts = 0
    errors = []
    for harness, provider in (("claude-code", "anthropic"), ("codex", "openai")):
        paths = sorted((root / harness).glob("*/metadata.yml"))
        require(bool(paths), f"no versions found for {harness}")
        for path in paths:
            try:
                prompts += validate_metadata(path, provider)
                versions += 1
            except (ArchiveError, OSError) as exc:
                errors.append(f"{path.relative_to(root)}: {exc}")
    if errors:
        raise ArchiveError("\n".join(errors))
    return versions, prompts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        versions, prompts = validate_archive(args.root)
    except ArchiveError as exc:
        parser.exit(1, f"{exc}\n")
    print(f"Validated {versions} versions and {prompts} prompt entries")


if __name__ == "__main__":
    main()
