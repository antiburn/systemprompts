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

FAMILY_LABELS = {
    "opus": "Opus",
    "sonnet": "Sonnet",
    "haiku": "Haiku",
    "fable": "Fable",
    "gpt": "GPT",
    "codex-tuned": "Codex-tuned",
    "o-series": "o-series",
    "other": "Other models",
}

FAMILY_ORDER = {
    "claude-code": {"opus": 0, "sonnet": 1, "haiku": 2, "fable": 3, "other": 99},
    "codex": {"gpt": 0, "codex-tuned": 1, "o-series": 2, "other": 99},
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
        variants = {}
        for system_prompt in system_prompts:
            model = system_prompt.get("model")
            if not isinstance(model, str) or not model or model == "unrecorded":
                continue
            measurement = system_prompt.get("token_measurement") or {}
            tools_for_model = system_prompt.get("tools") or []
            system_count = system_prompt.get("token_count")
            tool_counts = [tool.get("definition_token_count") for tool in tools_for_model]
            is_complete = (
                measurement.get("status") == "measured"
                and measurement.get("model") == model
                and isinstance(system_count, int)
                and not isinstance(system_count, bool)
                and all(isinstance(count, int) and not isinstance(count, bool) for count in tool_counts)
            )
            variants[model] = {
                "status": measurement.get("status") or "legacy",
                "has_chart_data": is_complete,
                "system_tokens": system_count if is_complete else None,
                "tools_tokens": sum(tool_counts) if is_complete else None,
                "combined": system_count + sum(tool_counts) if is_complete else None,
            }

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
                "variants": variants,
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


def load_model_catalog(path: str):
    """Load and validate the small, reviewed release catalog used by charts."""
    data = load_yaml_file(path) or {}
    lineages = data.get("lineages")
    models = data.get("models")
    if not isinstance(lineages, list):
        raise ValueError(f"{path}: expected a lineages list")
    if not isinstance(models, list):
        raise ValueError(f"{path}: expected a models list")

    lineage_by_id = {}
    lineage_orders = set()
    for index, lineage in enumerate(lineages):
        if not isinstance(lineage, dict):
            raise ValueError(f"{path}: lineages[{index}] must be a mapping")
        required = ("harness", "id", "display_name", "order")
        missing = [key for key in required if lineage.get(key) is None]
        if missing:
            raise ValueError(f"{path}: lineages[{index}] is missing {', '.join(missing)}")
        identity = (lineage["harness"], lineage["id"])
        order_identity = (lineage["harness"], lineage["order"])
        if identity in lineage_by_id:
            raise ValueError(f"{path}: duplicate lineage {identity[0]}/{identity[1]}")
        if order_identity in lineage_orders:
            raise ValueError(
                f"{path}: duplicate lineage order {order_identity[0]}/{order_identity[1]}"
            )
        if (
            not isinstance(lineage["order"], int)
            or isinstance(lineage["order"], bool)
            or lineage["order"] <= 0
        ):
            raise ValueError(f"{path}: {lineage['id']} order must be a positive integer")
        lineage_by_id[identity] = lineage
        lineage_orders.add(order_identity)

    result = []
    seen = set()
    lineage_positions = set()
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            raise ValueError(f"{path}: models[{index}] must be a mapping")
        required = (
            "harness",
            "id",
            "family",
            "lineage",
            "lineage_position",
            "display_name",
            "released",
            "sources",
        )
        missing = [key for key in required if not model.get(key)]
        if missing:
            raise ValueError(f"{path}: models[{index}] is missing {', '.join(missing)}")
        identity = (model["harness"], model["id"])
        if identity in seen:
            raise ValueError(f"{path}: duplicate model {identity[0]}/{identity[1]}")
        seen.add(identity)
        lineage_identity = (model["harness"], model["lineage"])
        if lineage_identity not in lineage_by_id:
            raise ValueError(
                f"{path}: {model['id']} references unknown lineage {model['lineage']}"
            )
        position = model["lineage_position"]
        if not isinstance(position, int) or isinstance(position, bool) or position <= 0:
            raise ValueError(
                f"{path}: {model['id']} lineage_position must be a positive integer"
            )
        position_identity = (*lineage_identity, position)
        if position_identity in lineage_positions:
            raise ValueError(
                f"{path}: duplicate position {position} in "
                f"{lineage_identity[0]}/{lineage_identity[1]}"
            )
        lineage_positions.add(position_identity)
        if not isinstance(model["sources"], list) or not all(
            isinstance(source, str) and source.startswith(("https://", "http://"))
            for source in model["sources"]
        ):
            raise ValueError(f"{path}: {model['id']} sources must be a non-empty URL list")
        try:
            released = parse_iso(str(model["released"]))
            retired = parse_iso(str(model["retired"])) if model.get("retired") else None
        except ValueError as error:
            raise ValueError(f"{path}: invalid lifecycle date for {model['id']}: {error}") from error
        if retired is not None and retired <= released:
            raise ValueError(f"{path}: {model['id']} must retire after it is released")
        lineage = lineage_by_id[lineage_identity]
        result.append(
            {
                **model,
                "released_at": released,
                "retired_at": retired,
                "lineage_label": lineage["display_name"],
                "lineage_order": lineage["order"],
            }
        )

    for lineage_identity in lineage_by_id:
        lineage_models = sorted(
            (model for model in result if (model["harness"], model["lineage"]) == lineage_identity),
            key=lambda model: model["lineage_position"],
        )
        expected_positions = list(range(1, len(lineage_models) + 1))
        actual_positions = [model["lineage_position"] for model in lineage_models]
        if actual_positions != expected_positions:
            raise ValueError(
                f"{path}: {lineage_identity[0]}/{lineage_identity[1]} positions must be consecutive"
            )
        for older, newer in zip(lineage_models, lineage_models[1:]):
            if newer["released_at"] <= older["released_at"]:
                raise ValueError(
                    f"{path}: {newer['id']} must be released after lineage predecessor {older['id']}"
                )
    return result


def infer_family(model: str) -> str:
    """Keep uncataloged archive models visible without guessing a release."""
    lowered = model.lower()
    for family in ("opus", "sonnet", "haiku", "fable"):
        if family in lowered:
            return family
    if lowered.startswith("o") and re.match(r"^o\d", lowered):
        return "o-series"
    if "codex" in lowered:
        return "codex-tuned"
    if lowered.startswith("gpt-"):
        return "gpt"
    return "other"


def build_family_panels(harness: str, records, catalog):
    """Return stacked histories for explicit successor lineages.

    Release, retirement, and CLI package dates form one event timeline. A
    successor immediately replaces its predecessor. The successor can start
    exactly at its API release marker only when the archive has a complete
    capture for the then-current CLI/model pair; otherwise the chart stays
    blank until a later CLI release has a complete selected-model capture.
    """
    records = sorted(
        records,
        key=lambda record: (
            record["date"],
            version_sort_key(record["version"]),
            record.get("dirname", ""),
        ),
    )
    known = {entry["id"]: entry for entry in catalog if entry["harness"] == harness}
    observed = {
        model
        for record in records
        for model in record["variants"]
        if model != "unrecorded"
    }

    grouped = {}
    for model in known.values():
        grouped.setdefault(model["lineage"], []).append(model)
    for model in sorted(observed - set(known)):
        grouped[f"uncataloged:{model}"] = [
            {
                "harness": harness,
                "id": model,
                "family": infer_family(model),
                "lineage": f"uncataloged:{model}",
                "lineage_position": 1,
                "display_name": model,
                "released_at": None,
                "retired_at": None,
                "lineage_label": f"Uncataloged: {model}",
                "lineage_order": 999,
            }
        ]

    timeline_start = min((record["date"] for record in records), default=None)
    timeline_end = max((record["date"] for record in records), default=None)
    panels = []
    for lineage, lineage_models in grouped.items():
        models = sorted(lineage_models, key=lambda model: model["lineage_position"])
        is_cataloged = models[0]["released_at"] is not None
        has_data_by_model = {model["id"]: False for model in models}
        segments = []
        current_segment = []
        selected_model = None

        events = [(record["date"], 2, "cli", record) for record in records]
        if is_cataloged:
            for model in models:
                events.append((model["released_at"], 0, "release", model))
                if model["retired_at"] is not None and (
                    timeline_end is None or model["retired_at"] <= timeline_end
                ):
                    events.append((model["retired_at"], 1, "retire", model))
        events.sort(key=lambda event: (event[0], event[1]))

        for event_date, _, event_kind, payload in events:
            if event_kind == "release":
                if current_segment:
                    _append_boundary(current_segment, event_date)
                    segments.append(current_segment)
                    current_segment = []
                selected_model = payload
                source_record = _then_current_cli(records, event_date)
                point = _measurement_point(
                    source_record,
                    selected_model,
                    date=event_date,
                    model_release_boundary=True,
                )
                if point is not None:
                    _append_measurement(current_segment, point)
                    has_data_by_model[selected_model["id"]] = True
                continue

            if event_kind == "retire":
                if selected_model is not None and selected_model["id"] == payload["id"]:
                    if current_segment:
                        _append_boundary(current_segment, event_date)
                        segments.append(current_segment)
                        current_segment = []
                    selected_model = None
                continue

            record = payload
            selected = _select_lineage_model(models, record["date"]) if is_cataloged else models[0]
            selected_id = selected["id"] if selected is not None else None
            prior_id = selected_model["id"] if selected_model is not None else None
            if selected_id != prior_id:
                if current_segment:
                    boundary = _model_transition_date(models, selected_model, record["date"])
                    _append_boundary(current_segment, boundary)
                    segments.append(current_segment)
                    current_segment = []
                selected_model = selected

            point = _measurement_point(record, selected_model)
            if point is not None:
                _append_measurement(current_segment, point)
                has_data_by_model[selected_model["id"]] = True
            elif current_segment:
                _append_boundary(current_segment, record["date"])
                segments.append(current_segment)
                current_segment = []

        if current_segment:
            segments.append(current_segment)

        releases = []
        unknown_releases = []
        if is_cataloged:
            for model in models:
                releases.append(
                    {
                        "date": model["released_at"],
                        "models": [
                            {
                                "model": model["id"],
                                "label": model["display_name"],
                                "has_data": has_data_by_model[model["id"]],
                            }
                        ],
                    }
                )
        else:
            unknown_releases.append(models[0]["display_name"])

        panels.append(
            {
                "family": models[0]["family"],
                "lineage": lineage,
                "label": models[0]["lineage_label"],
                "slug": _slugify(lineage),
                "order": models[0]["lineage_order"],
                "models": models,
                "selected_models_with_data": sum(has_data_by_model.values()),
                "segments": segments,
                "releases": releases,
                "unknown_releases": unknown_releases,
                "timeline_start": timeline_start,
                "timeline_end": timeline_end,
            }
        )

    panels.sort(key=lambda panel: (panel["order"], panel["label"]))
    return panels


def _select_lineage_model(models, date):
    released = [model for model in models if model["released_at"] <= date]
    if not released:
        return None
    latest = max(released, key=lambda model: model["lineage_position"])
    if latest["retired_at"] is not None and date >= latest["retired_at"]:
        return None
    return latest


def _model_transition_date(models, selected_model, observed_until):
    if selected_model is None:
        return None
    candidates = [
        model["released_at"]
        for model in models
        if model["lineage_position"] > selected_model["lineage_position"]
        and model["released_at"] <= observed_until
    ]
    retired = selected_model["retired_at"]
    if retired is not None and retired <= observed_until:
        candidates.append(retired)
    return min(candidates, default=None)


def _then_current_cli(records, date):
    eligible = [record for record in records if record["date"] <= date]
    return eligible[-1] if eligible else None


def _measurement_point(
    record,
    selected_model,
    *,
    date=None,
    model_release_boundary=False,
):
    if record is None or selected_model is None:
        return None
    observation = record["variants"].get(selected_model["id"])
    if not observation or not observation["has_chart_data"]:
        return None
    return {
        "date": date or record["date"],
        "version": record["version"],
        "model": selected_model["id"],
        "system": observation["system_tokens"],
        "tools": observation["tools_tokens"],
        "model_release_boundary": model_release_boundary,
    }


def _append_boundary(segment, boundary):
    if boundary is None or boundary <= segment[-1]["date"]:
        return
    segment.append({**segment[-1], "date": boundary, "boundary": True})


def _append_measurement(segment, point):
    """Append a point, resolving same-date records by deterministic CLI order."""
    if (
        segment
        and segment[-1]["date"] == point["date"]
        and segment[-1]["model"] == point["model"]
    ):
        previous = segment[-1]
        if previous.get("model_release_boundary") and (
            previous["version"] == point["version"]
            and previous["system"] == point["system"]
            and previous["tools"] == point["tools"]
        ):
            return
        if previous.get("model_release_boundary"):
            point = {**point, "model_release_boundary": True}
        segment[-1] = point
    else:
        segment.append(point)


def _slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def build_harness_overview(root: str, harness_dirname: str, assets_dir: str):
    harness_dir = os.path.join(root, harness_dirname)
    records = load_versions(harness_dir)
    callouts_raw = load_annotations(harness_dir)
    label = display_name(harness_dirname)

    if not records:
        eprint(f"warning: no captured versions found for {harness_dirname}")

    n_versions = len(records)
    first_date = min((record["date"] for record in records), default=None)
    last_date = max((record["date"] for record in records), default=None)
    latest_combined = next(
        (
            record["combined"]
            for record in sorted(
                records,
                key=lambda record: (
                    record["date"],
                    version_sort_key(record["version"]),
                ),
                reverse=True,
            )
            if record["has_chart_data"]
        ),
        None,
    )

    chart_points = [
        {
            "date": record["date"],
            "version": record["version"],
            "system": record["system_tokens"],
            "tools": record["tools_tokens"],
        }
        for record in records
        if record["has_chart_data"]
    ]
    by_version = {record["version"]: record for record in records if record["has_chart_data"]}
    callouts = []
    for callout in callouts_raw:
        version = callout.get("version")
        record = by_version.get(str(version))
        if record is None:
            eprint(
                f"warning: {harness_dirname}/annotations.yml references version "
                f"{version!r} with no plotted token data; skipping this callout"
            )
            continue
        callouts.append(
            {
                "date": record["date"],
                "value": record["combined"],
                "label": callout.get("label", ""),
                "detail": callout.get("detail", ""),
            }
        )

    os.makedirs(assets_dir, exist_ok=True)
    for mode, suffix in (("light", ""), ("dark", "-dark")):
        svg = chart.render_chart_svg(label, chart_points, callouts, mode=mode)
        with open(
            os.path.join(assets_dir, f"{harness_dirname}-tokens{suffix}.svg"),
            "w",
            encoding="utf-8",
        ) as file:
            file.write(svg + "\n")

    if n_versions == 0 or first_date is None:
        stats_line = "_No versions captured yet._"
    else:
        date_range = f"{first_date.strftime('%b %Y')} – {last_date.strftime('%b %Y')}"
        combined = (
            f"{latest_combined:,} combined tokens (latest)"
            if latest_combined is not None
            else "combined tokens: n/a"
        )
        version_word = "version" if n_versions == 1 else "versions"
        stats_line = f"{n_versions} {version_word} · {date_range} · {combined}"

    section = [f"## {label}", "", stats_line, "", "<picture>"]
    section.append(
        f'  <source media="(prefers-color-scheme: dark)" '
        f'srcset="assets/{harness_dirname}-tokens-dark.svg">'
    )
    section.append(
        f'  <img alt="{label} token history: system message and built-in tool '
        f'token counts by CLI release date" src="assets/{harness_dirname}-tokens.svg">'
    )
    section.extend(["</picture>", ""])
    section.append(
        f"Each `{harness_dirname}/<version>/` directory holds `metadata.yml` "
        "(capture provenance, token measurement, and the built-in tool "
        "surface) plus one subdirectory per captured model variant, each "
        "with `systemprompt.txt` (the raw captured payload) and "
        "`systemprompt.md` (a rendered, browsable view)."
    )
    section.append("")
    return "\n".join(section), records


def build_family_sections(harness_dirname, records, assets_dir, catalog):
    label = display_name(harness_dirname)
    section = [f"### {label}", ""]
    if harness_dirname == "codex":
        section.append(
            "Parallel standard, mini, nano, Pro, Codex, and o-series tiers use "
            "separate charts so a same-day sibling release never appears to "
            "supersede another tier."
        )
        section.append("")

    for panel in build_family_panels(harness_dirname, records, catalog):
        asset_base = f"{harness_dirname}-{panel['slug']}-tokens"
        for mode, suffix in (("light", ""), ("dark", "-dark")):
            svg = chart.render_family_chart_svg(
                label,
                panel["label"],
                panel["segments"],
                panel["releases"],
                panel["unknown_releases"],
                timeline_start=panel["timeline_start"],
                timeline_end=panel["timeline_end"],
                mode=mode,
            )
            with open(
                os.path.join(assets_dir, f"{asset_base}{suffix}.svg"),
                "w",
                encoding="utf-8",
            ) as file:
                file.write(svg + "\n")

        release_count = len(panel["models"])
        selected_count = panel["selected_models_with_data"]
        section.extend([f"#### {panel['label']}", ""])
        if panel["unknown_releases"]:
            section.append(
                f"{release_count} uncataloged model{'s' if release_count != 1 else ''} observed · "
                f"{selected_count} with complete measurements in the archive"
            )
        else:
            section.append(
                f"{release_count} model release{'s' if release_count != 1 else ''} · "
                f"{selected_count} with complete measurements during their release period"
            )
        section.extend(["", "<picture>"])
        section.append(
            f'  <source media="(prefers-color-scheme: dark)" '
            f'srcset="assets/{asset_base}-dark.svg">'
        )
        section.append(
            f'  <img alt="{label} {panel["label"]} lineage history: stacked system-message '
            f'and built-in-tool native token counts by CLI and model release date" '
            f'src="assets/{asset_base}.svg">'
        )
        section.extend(["</picture>", ""])
    return "\n".join(section)


def build_readme(root: str, assets_dir: str, catalog=None) -> str:
    harnesses = discover_harnesses(root)
    if catalog is None:
        catalog = load_model_catalog(os.path.join(root, "tools", "model-families.yml"))
    lines = [
        "# AI Coding Harness System Prompts",
        "",
        (
            "An automatically updated, versioned archive of the system prompts "
            "and built-in tool surfaces of AI coding harnesses, with measured "
            "token counts and capture provenance for every release."
        ),
        "",
        (
            "> Captured artifacts are provided for research and reference. "
            "The prompt content belongs to the respective vendors; no license "
            "is granted over it by this repository."
        ),
        "",
    ]

    records_by_harness = {}
    if not harnesses:
        lines.extend(["_No harness data has landed in this repository yet._", ""])
    else:
        for harness in harnesses:
            overview, records = build_harness_overview(root, harness, assets_dir)
            records_by_harness[harness] = records
            lines.append(overview)

        lines.extend(
            [
                "## Model-family histories",
                "",
                (
                    "Each chart uses the same stacked system-message and aggregate built-in-tool "
                    "token format as the overviews. At every CLI package release, it selects the "
                    "most recently API-released model in that successor lineage; a newer release "
                    "immediately replaces its predecessor."
                ),
                "",
                (
                    "The horizontal timeline combines CLI package releases with dotted model API "
                    "release markers. At a marker, an archived complete capture of the new model "
                    "for the then-current CLI can anchor the new stack; otherwise the chart stays "
                    "blank until a later CLI release has that measurement. Historical recaptures "
                    "describe the tested CLI/model pair, not actual model usage when the CLI shipped."
                ),
                "",
                (
                    "Missing, unavailable, and partial selected-model measurements remain blank; "
                    "the older model is never substituted. Those outcomes record a capture result, "
                    "not proof that the model could never be captured, and their reason is preserved "
                    "in that version's `metadata.yml` under `token_measurement`."
                ),
                "",
                (
                    "Model availability dates, explicit successor order, and source links live in "
                    "[`tools/model-families.yml`](tools/model-families.yml). Uncataloged models get "
                    "their own clearly labeled chart without a guessed release date."
                ),
                "",
            ]
        )
        for harness in harnesses:
            lines.append(
                build_family_sections(
                    harness,
                    records_by_harness[harness],
                    assets_dir,
                    catalog,
                )
            )

    lines.extend(
        [
            "---",
            "",
            (
                "README and charts are regenerated automatically from the checked-in "
                "`metadata.yml`, `annotations.yml`, and `tools/model-families.yml` files by "
                "`.github/workflows/generate-readme.yml`; edit those, not this file."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--root", default=default_root, help="repo/data root (default: repo root)")
    ap.add_argument("--readme", default=None, help="output README path (default: <root>/README.md)")
    ap.add_argument("--assets", default=None, help="output assets dir (default: <root>/assets)")
    ap.add_argument(
        "--catalog",
        default=None,
        help="model release catalog (default: <root>/tools/model-families.yml)",
    )
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    readme_path = args.readme or os.path.join(root, "README.md")
    assets_dir = args.assets or os.path.join(root, "assets")
    catalog_path = args.catalog or os.path.join(root, "tools", "model-families.yml")

    catalog = load_model_catalog(catalog_path)
    readme = build_readme(root, assets_dir, catalog)
    os.makedirs(os.path.dirname(os.path.abspath(readme_path)) or ".", exist_ok=True)
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)

    print(f"wrote {readme_path}")
    print(f"wrote assets to {assets_dir}")


if __name__ == "__main__":
    main()
