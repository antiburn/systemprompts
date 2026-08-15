#!/usr/bin/env python3
"""Regenerate README.md and the per-harness token-history charts from the
checked-in metadata tree.

Layout this reads (see the repo's design doc / AGENTS notes):

    <harness>/annotations.yml               {"callouts": [{version, label, detail}, ...]}
    <harness>/<version>/metadata.yml        {version, capture: {captured_at, ...},
                                              system_prompts: [{model, directory,
                                                       character_count, token_count,
                                                       content_sha256?, capture,
                                                       token_measurement: {...},
                                                       tools: [{canonical_name,
                                                                definition_bytes,
                                                                definition_token_count}, ...]},
                                                       ...]}

A harness directory is any top-level directory containing its own
annotations.yml. Known harnesses get a friendly display name; unknown ones
fall back to a title-cased version of the directory name.

Usage:
    python3 generate_readme.py [--root DIR] [--readme PATH] [--assets DIR]

Defaults: --root is the repo root (parent of this script's tools/ dir),
--readme is <root>/README.md, --assets is <root>/assets.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from yamlmini import load_yaml_file  # noqa: E402
import chart  # noqa: E402

DISPLAY_NAMES = {
    "claude-code": "Claude Code",
    "codex": "Codex",
}

SKIP_DIRS = {"tools", ".github", "assets", ".git", "node_modules", ".venv", "__pycache__"}


def eprint(*a, **kw):
    print(*a, file=sys.stderr, **kw)


_FRACTIONAL_SECONDS_RE = re.compile(r"(\.\d+)(?=[+-]\d{2}:\d{2}$|$)")


def parse_iso(ts: str) -> datetime:
    # Accept trailing 'Z' regardless of Python's fromisoformat version quirks.
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Pre-3.11 fromisoformat only accepts 0 or 6 fractional-second
        # digits; real captures carry millisecond timestamps like
        # ".163". Pad/truncate to exactly 6 digits and retry once.
        def _pad(m: "re.Match") -> str:
            digits = m.group(1)[1:]
            return "." + (digits + "000000")[:6]

        dt = datetime.fromisoformat(_FRACTIONAL_SECONDS_RE.sub(_pad, s))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def display_name(dirname: str) -> str:
    if dirname in DISPLAY_NAMES:
        return DISPLAY_NAMES[dirname]
    return " ".join(w.capitalize() for w in re.split(r"[-_]", dirname) if w)


def discover_harnesses(root: str):
    found = []
    for entry in sorted(os.listdir(root)):
        if entry in SKIP_DIRS or entry.startswith("."):
            continue
        path = os.path.join(root, entry)
        if not os.path.isdir(path):
            continue
        if os.path.isfile(os.path.join(path, "annotations.yml")):
            found.append(entry)
    order = {"claude-code": 0, "codex": 1}
    found.sort(key=lambda d: (order.get(d, 99), d))
    return found


def load_versions(harness_dir: str):
    """Return a list of version records, sorted by captured_at ascending."""
    records = []
    for entry in sorted(os.listdir(harness_dir)):
        vpath = os.path.join(harness_dir, entry)
        if not os.path.isdir(vpath):
            continue
        meta_path = os.path.join(vpath, "metadata.yml")
        if not os.path.isfile(meta_path):
            continue
        try:
            meta = load_yaml_file(meta_path) or {}
        except Exception as e:  # noqa: BLE001
            eprint(f"warning: failed to parse {meta_path}: {e}; skipping this version")
            continue

        version = meta.get("version") or entry
        capture = meta.get("capture") or {}
        captured_at_raw = capture.get("captured_at")
        if not captured_at_raw:
            eprint(f"warning: {meta_path} has no capture.captured_at; skipping this version")
            continue
        try:
            captured_at = parse_iso(captured_at_raw)
        except Exception as e:  # noqa: BLE001
            eprint(f"warning: {meta_path} has unparseable captured_at {captured_at_raw!r}: {e}; skipping")
            continue

        # `released` (the package's actual publish date) is the date we want
        # for the x-axis and date-range stats. `captured_at` is when a
        # historical backfill run happened to execute, which for a batch
        # backfill is the same day for every version — not useful as a time
        # axis. Fall back to captured_at when released isn't recorded yet.
        released_raw = meta.get("released")
        effective_date = captured_at
        if released_raw:
            try:
                effective_date = parse_iso(released_raw)
            except Exception as e:  # noqa: BLE001
                eprint(
                    f"warning: {meta_path} has unparseable released {released_raw!r}: {e}; "
                    "falling back to captured_at"
                )

        system_prompts = meta.get("system_prompts") or []
        primary = None
        for sp in system_prompts:
            measurement = sp.get("token_measurement") or {}
            if (
                measurement.get("status") == "measured"
                and measurement.get("model") == sp.get("model")
                and isinstance(sp.get("token_count"), (int, float))
            ):
                primary = sp
                break

        has_native_schema = any(sp.get("token_measurement") for sp in system_prompts)

        # Compatibility only for metadata that has not yet migrated from the
        # old version-level fixed-tokenizer layout. A native `partial` entry
        # deliberately is not charted: its system count is valid, but stacking
        # only the supported tools would understate the combined total.
        if primary is None and not has_native_schema:
            for sp in system_prompts:
                if isinstance(sp.get("token_count"), (int, float)):
                    primary = sp
                    break
            tools = meta.get("tools") or []
        elif primary is not None:
            tools = primary.get("tools") or []
        else:
            tools = []

        tools_token_sum = 0
        for t in tools:
            v = t.get("definition_token_count")
            if isinstance(v, (int, float)):
                tools_token_sum += v
            # else: missing/None definition_token_count contributes 0 (documented assumption)

        has_chart_data = primary is not None
        system_tokens = primary["token_count"] if has_chart_data else None

        records.append(
            {
                "version": str(version),
                "dirname": entry,
                "captured_at": captured_at,
                "date": effective_date,
                "tools_tokens": tools_token_sum,
                "system_tokens": system_tokens,
                "has_chart_data": has_chart_data,
                "combined": (system_tokens + tools_token_sum) if has_chart_data else None,
            }
        )

    records.sort(key=lambda r: (r["date"], version_sort_key(r["version"]), r["dirname"]))
    return records


def version_sort_key(version: str):
    """Numeric tuple for a dotted version string, so "2.1.98" < "2.1.228"

    (a plain string/dirname comparison gets this backwards: "9" > "2" as the
    first differing character). Falls back to the raw string for anything
    with no digits at all.
    """
    parts = re.findall(r"\d+", version)
    if not parts:
        return (version,)
    return tuple(int(p) for p in parts)


def load_annotations(harness_dir: str):
    path = os.path.join(harness_dir, "annotations.yml")
    if not os.path.isfile(path):
        return []
    try:
        data = load_yaml_file(path) or {}
    except Exception as e:  # noqa: BLE001
        eprint(f"warning: failed to parse {path}: {e}; treating as no callouts")
        return []
    return data.get("callouts") or []


def build_harness_section(root: str, harness_dirname: str, assets_dir: str):
    harness_dir = os.path.join(root, harness_dirname)
    records = load_versions(harness_dir)
    callouts_raw = load_annotations(harness_dir)

    label = display_name(harness_dirname)

    if not records:
        eprint(f"warning: no captured versions found for {harness_dirname}")

    n_versions = len(records)
    first_date = min((r["date"] for r in records), default=None)
    last_date = max((r["date"] for r in records), default=None)

    latest_combined = None
    for r in sorted(records, key=lambda r: (r["date"], version_sort_key(r["version"])), reverse=True):
        if r["has_chart_data"]:
            latest_combined = r["combined"]
            break

    chart_points = [
        {
            "date": r["date"],
            "version": r["version"],
            "system": r["system_tokens"],
            "tools": r["tools_tokens"],
        }
        for r in records
        if r["has_chart_data"]
    ]

    by_version = {r["version"]: r for r in records if r["has_chart_data"]}
    callouts = []
    for co in callouts_raw:
        v = co.get("version")
        rec = by_version.get(str(v))
        if rec is None:
            eprint(
                f"warning: {harness_dirname}/annotations.yml references version "
                f"{v!r} with no plotted token data; skipping this callout"
            )
            continue
        callouts.append(
            {
                "date": rec["date"],
                "value": rec["combined"],
                "label": co.get("label", ""),
                "detail": co.get("detail", ""),
            }
        )

    os.makedirs(assets_dir, exist_ok=True)
    light_svg = chart.render_chart_svg(label, chart_points, callouts, mode="light")
    dark_svg = chart.render_chart_svg(label, chart_points, callouts, mode="dark")
    light_path = os.path.join(assets_dir, f"{harness_dirname}-tokens.svg")
    dark_path = os.path.join(assets_dir, f"{harness_dirname}-tokens-dark.svg")
    with open(light_path, "w", encoding="utf-8") as f:
        f.write(light_svg + "\n")
    with open(dark_path, "w", encoding="utf-8") as f:
        f.write(dark_svg + "\n")

    if n_versions == 0 or first_date is None:
        stats_line = "_No versions captured yet._"
    else:
        date_range = f"{first_date.strftime('%b %Y')} – {last_date.strftime('%b %Y')}"
        combined_str = f"{latest_combined:,} combined tokens (latest)" if latest_combined is not None else "combined tokens: n/a"
        version_word = "version" if n_versions == 1 else "versions"
        stats_line = f"{n_versions} {version_word} · {date_range} · {combined_str}"

    asset_rel_light = f"assets/{harness_dirname}-tokens.svg"
    asset_rel_dark = f"assets/{harness_dirname}-tokens-dark.svg"

    section = []
    section.append(f"## {label}")
    section.append("")
    section.append(stats_line)
    section.append("")
    section.append("<picture>")
    section.append(f'  <source media="(prefers-color-scheme: dark)" srcset="{asset_rel_dark}">')
    section.append(f'  <img alt="{label} token history: system message and built-in tool token counts by capture date" src="{asset_rel_light}">')
    section.append("</picture>")
    section.append("")
    section.append(
        f"Each `{harness_dirname}/<version>/` directory holds `metadata.yml` "
        "(capture provenance, token measurement, and the built-in tool "
        "surface) plus one subdirectory per captured model variant, each "
        "with `systemprompt.txt` (the raw captured payload) and "
        "`systemprompt.md` (a rendered, browsable view)."
    )
    section.append("")
    return "\n".join(section)


def build_readme(root: str, assets_dir: str) -> str:
    harnesses = discover_harnesses(root)
    lines = []
    lines.append("# AI Coding Harness System Prompts")
    lines.append("")
    lines.append(
        "An automatically updated, versioned archive of the system prompts "
        "and built-in tool surfaces of AI coding harnesses, with measured "
        "token counts and capture provenance for every release."
    )
    lines.append("")
    lines.append(
        "> Captured artifacts are provided for research and reference. "
        "The prompt content belongs to the respective vendors; no license "
        "is granted over it by this repository."
    )
    lines.append("")

    if not harnesses:
        lines.append("_No harness data has landed in this repository yet._")
        lines.append("")
    else:
        for h in harnesses:
            lines.append(build_harness_section(root, h, assets_dir))

    lines.append("---")
    lines.append("")
    lines.append(
        "README and charts are regenerated automatically from the checked-in "
        "`metadata.yml` and `annotations.yml` files by "
        "`.github/workflows/generate-readme.yml`; edit those, not this file."
    )
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--root", default=default_root, help="repo/data root (default: repo root)")
    ap.add_argument("--readme", default=None, help="output README path (default: <root>/README.md)")
    ap.add_argument("--assets", default=None, help="output assets dir (default: <root>/assets)")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    readme_path = args.readme or os.path.join(root, "README.md")
    assets_dir = args.assets or os.path.join(root, "assets")

    readme = build_readme(root, assets_dir)
    os.makedirs(os.path.dirname(os.path.abspath(readme_path)) or ".", exist_ok=True)
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)

    print(f"wrote {readme_path}")
    print(f"wrote assets to {assets_dir}")


if __name__ == "__main__":
    main()
