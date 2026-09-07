import os
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chart  # noqa: E402
import generate_readme  # noqa: E402


def dt(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def measured(system, tools=0):
    return {
        "status": "measured",
        "has_chart_data": True,
        "system_tokens": system,
        "tools_tokens": tools,
        "combined": system + tools,
    }


def unavailable(status="unavailable"):
    return {
        "status": status,
        "has_chart_data": False,
        "system_tokens": None,
        "tools_tokens": None,
        "combined": None,
    }


def catalog_model(
    model_id,
    released,
    *,
    harness="claude-code",
    lineage="test-lineage",
    position=1,
    retired=None,
    label=None,
    order=1,
):
    return {
        "harness": harness,
        "id": model_id,
        "family": "opus" if harness == "claude-code" else "gpt",
        "lineage": lineage,
        "lineage_position": position,
        "display_name": label or model_id,
        "released_at": dt(released),
        "retired_at": dt(retired) if retired else None,
        "lineage_label": lineage.replace("-", " ").title(),
        "lineage_order": order,
    }


class ModelFamilyChartTests(unittest.TestCase):
    def test_catalog_rejects_duplicate_model_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "catalog.yml")
            with open(path, "w", encoding="utf-8") as file:
                file.write(
                    textwrap.dedent(
                        """\
                        lineages:
                        - harness: codex
                          id: standard
                          display_name: Standard
                          order: 1
                        models:
                        - harness: codex
                          id: gpt-test
                          family: gpt
                          lineage: standard
                          lineage_position: 1
                          display_name: GPT Test
                          released: '2026-01-01'
                          sources:
                          - "https://example.com/release"
                        - harness: codex
                          id: gpt-test
                          family: gpt
                          lineage: standard
                          lineage_position: 2
                          display_name: GPT Test duplicate
                          released: '2026-01-02'
                          sources:
                          - "https://example.com/release"
                        """
                    )
                )
            with self.assertRaisesRegex(ValueError, "duplicate model codex/gpt-test"):
                generate_readme.load_model_catalog(path)

    def test_catalog_rejects_successor_order_that_disagrees_with_release_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "catalog.yml")
            with open(path, "w", encoding="utf-8") as file:
                file.write(
                    textwrap.dedent(
                        """\
                        lineages:
                        - harness: codex
                          id: standard
                          display_name: Standard
                          order: 1
                        models:
                        - harness: codex
                          id: gpt-newer-name
                          family: gpt
                          lineage: standard
                          lineage_position: 1
                          display_name: First
                          released: '2026-02-01'
                          sources:
                          - "https://example.com/one"
                        - harness: codex
                          id: gpt-older-name
                          family: gpt
                          lineage: standard
                          lineage_position: 2
                          display_name: Second
                          released: '2026-01-01'
                          sources:
                          - "https://example.com/two"
                        """
                    )
                )
            with self.assertRaisesRegex(ValueError, "must be released after"):
                generate_readme.load_model_catalog(path)

    def test_cli_release_date_controls_family_chart_coordinates(self):
        catalog = [
            catalog_model(
                "gpt-test",
                "2025-01-01",
                harness="codex",
                lineage="gpt-standard",
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            self._write_metadata(directory, "1.0.0", "2025-02-01", "2030-01-01")
            self._write_metadata(directory, "1.1.0", "2025-03-01", "2030-02-01")
            before = self._family_svg(directory, catalog)
            self._write_metadata(directory, "1.1.0", "2025-03-01", "2040-12-31")
            after = self._family_svg(directory, catalog)

        self.assertEqual(before, after)
        self.assertIn("Feb 2025", before)
        self.assertIn("Mar 2025", before)
        self.assertNotIn("2040", before)

    def test_successor_immediately_replaces_predecessor_without_fallback(self):
        old = catalog_model("model-z", "2026-01-01", position=1)
        new = catalog_model("model-a", "2026-01-15", position=2)
        records = [
            self._record("1", "2026-01-01", {"model-z": measured(100, 20)}),
            self._record("2", "2026-01-10", {"model-z": measured(110, 20)}),
            self._record(
                "3",
                "2026-01-20",
                {"model-z": measured(120, 20), "model-a": unavailable("partial")},
            ),
        ]

        panel = generate_readme.build_family_panels(
            "claude-code", records, [old, new]
        )[0]

        self.assertEqual(1, len(panel["segments"]))
        self.assertEqual(
            [dt("2026-01-01"), dt("2026-01-10"), dt("2026-01-15")],
            [point["date"] for point in panel["segments"][0]],
        )
        self.assertTrue(panel["segments"][0][-1]["boundary"])
        self.assertEqual("model-z", panel["segments"][0][-1]["model"])
        self.assertEqual(dt("2026-01-20"), panel["timeline_end"])

    def test_model_release_boundary_uses_exact_then_current_cli_capture(self):
        old = catalog_model("old", "2026-01-01", position=1)
        new = catalog_model("new", "2026-01-15", position=2)
        records = [
            self._record("1.0", "2026-01-01", {"old": measured(100, 20)}),
            self._record(
                "1.1",
                "2026-01-10",
                {"old": measured(110, 25), "new": measured(210, 35)},
            ),
        ]

        panel = generate_readme.build_family_panels(
            "claude-code", records, [old, new]
        )[0]

        self.assertEqual(2, len(panel["segments"]))
        point = panel["segments"][1][0]
        self.assertEqual(dt("2026-01-15"), point["date"])
        self.assertEqual("1.1", point["version"])
        self.assertEqual("new", point["model"])
        self.assertEqual((210, 35), (point["system"], point["tools"]))
        self.assertTrue(point["model_release_boundary"])
        self.assertFalse(
            any(
                point["model"] == "new" and point["date"] < dt("2026-01-15")
                for segment in panel["segments"]
                for point in segment
            )
        )

    def test_same_date_cli_records_use_latest_deterministic_version(self):
        model = catalog_model("model", "2026-01-01")
        records = [
            self._record("1.0.0", "2026-01-02", {"model": measured(100, 10)}),
            self._record("1.0.1", "2026-01-02", {"model": measured(200, 20)}),
        ]
        panel = generate_readme.build_family_panels(
            "claude-code", list(reversed(records)), [model]
        )[0]
        points = panel["segments"][0]
        self.assertEqual(1, len(points))
        self.assertEqual("1.0.1", points[0]["version"])
        self.assertEqual((200, 20), (points[0]["system"], points[0]["tools"]))

    def test_retirement_clips_line_and_does_not_restore_retired_model(self):
        model = catalog_model("model", "2026-01-01", retired="2026-01-15")
        records = [
            self._record("1", "2026-01-01", {"model": measured(100, 20)}),
            self._record("2", "2026-01-20", {"model": measured(200, 30)}),
        ]
        panel = generate_readme.build_family_panels(
            "claude-code", records, [model]
        )[0]
        self.assertEqual(
            [dt("2026-01-01"), dt("2026-01-15")],
            [point["date"] for point in panel["segments"][0]],
        )
        self.assertEqual(dt("2026-01-20"), panel["timeline_end"])

    def test_partial_measurement_breaks_series_and_preserves_archive_horizon(self):
        model = catalog_model("model", "2026-01-01")
        records = [
            self._record("1", "2026-01-01", {"model": measured(100, 10)}),
            self._record("2", "2026-01-02", {"model": unavailable("partial")}),
            self._record("3", "2026-01-03", {"model": measured(120, 12)}),
            self._record("4", "2026-01-10", {"model": unavailable()}),
        ]
        panel = generate_readme.build_family_panels(
            "claude-code", records, [model]
        )[0]
        self.assertEqual(
            [[100, 100], [120, 120]],
            [[point["system"] for point in segment] for segment in panel["segments"]],
        )
        self.assertEqual(dt("2026-01-10"), panel["timeline_end"])

    def test_only_complete_exact_model_native_measurements_are_chartable(self):
        with tempfile.TemporaryDirectory() as directory:
            version_dir = os.path.join(directory, "1.0.0")
            os.makedirs(version_dir)
            with open(os.path.join(version_dir, "metadata.yml"), "w", encoding="utf-8") as file:
                file.write(
                    textwrap.dedent(
                        """\
                        version: '1.0.0'
                        released: '2026-01-01T00:00:00Z'
                        capture:
                          captured_at: '2026-09-01T00:00:00Z'
                        system_prompts:
                        - model: good-model
                          token_count: 100
                          token_measurement:
                            status: measured
                            model: good-model
                          tools:
                          - canonical_name: tool
                            definition_token_count: 50
                        - model: partial-model
                          token_count: 100
                          token_measurement:
                            status: partial
                            model: partial-model
                          tools:
                          - canonical_name: tool
                            definition_token_count: 50
                        - model: mismatched-model
                          token_count: 100
                          token_measurement:
                            status: measured
                            model: different-model
                          tools:
                          - canonical_name: tool
                            definition_token_count: 50
                        - model: incomplete-tools-model
                          token_count: 100
                          token_measurement:
                            status: measured
                            model: incomplete-tools-model
                          tools:
                          - canonical_name: tool
                            definition_token_count: null
                        """
                    )
                )
            variants = generate_readme.load_versions(directory)[0]["variants"]

        self.assertEqual(
            (100, 50),
            (
                variants["good-model"]["system_tokens"],
                variants["good-model"]["tools_tokens"],
            ),
        )
        self.assertTrue(variants["good-model"]["has_chart_data"])
        self.assertFalse(variants["partial-model"]["has_chart_data"])
        self.assertFalse(variants["mismatched-model"]["has_chart_data"])
        self.assertFalse(variants["incomplete-tools-model"]["has_chart_data"])

    def test_parallel_same_day_releases_get_separate_lineage_panels(self):
        catalog = [
            catalog_model(
                "gpt-standard",
                "2026-07-09",
                harness="codex",
                lineage="standard",
                order=1,
            ),
            catalog_model(
                "gpt-mini",
                "2026-07-09",
                harness="codex",
                lineage="mini",
                order=2,
            ),
        ]
        panels = generate_readme.build_family_panels("codex", [], catalog)
        self.assertEqual(["standard", "mini"], [panel["lineage"] for panel in panels])

    def test_uncataloged_partial_model_is_visible_without_fake_date(self):
        model = "gpt-unknown"
        records = [self._record("1", "2026-01-01", {model: unavailable("partial")})]
        panel = generate_readme.build_family_panels("codex", records, [])[0]
        svg = chart.render_family_chart_svg(
            "Codex",
            panel["label"],
            panel["segments"],
            panel["releases"],
            panel["unknown_releases"],
            timeline_start=panel["timeline_start"],
            timeline_end=panel["timeline_end"],
        )
        self.assertEqual([], panel["segments"])
        self.assertIn("Release date not cataloged · gpt-unknown", svg)
        self.assertIn("No complete native measurements", svg)
        self.assertNotIn("1970", svg)

    def test_family_svg_is_stacked_and_model_markers_are_dotted(self):
        release = dt("2026-01-01")
        point = {
            "date": release,
            "version": "1.0.0",
            "model": "model",
            "system": 100,
            "tools": 50,
            "model_release_boundary": True,
        }
        svg = chart.render_family_chart_svg(
            "Claude Code",
            "Opus",
            [[point]],
            [
                {
                    "date": release,
                    "models": [
                        {"model": "model", "label": "Model", "has_data": True}
                    ],
                }
            ],
            [],
            timeline_start=release,
            timeline_end=dt("2026-02-01"),
        )
        self.assertIn("System message", svg)
        self.assertIn("Built-in tools (aggregate)", svg)
        self.assertIn('stroke-dasharray="2,5"', svg)
        self.assertIn('data-boundary-capture="true"', svg)
        self.assertIn('data-source-version="1.0.0"', svg)
        self.assertIn('text-anchor="start">Jan 2026</text>', svg)
        self.assertIn('text-anchor="end">Feb 2026</text>', svg)

    def test_readme_places_both_overviews_before_family_sections(self):
        with tempfile.TemporaryDirectory() as root:
            assets = os.path.join(root, "assets")
            for harness in ("claude-code", "codex"):
                harness_dir = os.path.join(root, harness)
                os.makedirs(harness_dir)
                with open(
                    os.path.join(harness_dir, "annotations.yml"),
                    "w",
                    encoding="utf-8",
                ) as file:
                    file.write("callouts: []\n")
            readme = generate_readme.build_readme(root, assets, [])

        family_index = readme.index("## Model-family histories")
        self.assertLess(readme.index("## Claude Code"), family_index)
        self.assertLess(readme.index("## Codex"), family_index)
        self.assertIn("same stacked system-message", readme)

    def _family_svg(self, harness_dir, catalog):
        records = generate_readme.load_versions(harness_dir)
        panel = generate_readme.build_family_panels("codex", records, catalog)[0]
        return chart.render_family_chart_svg(
            "Codex",
            panel["label"],
            panel["segments"],
            panel["releases"],
            panel["unknown_releases"],
            timeline_start=panel["timeline_start"],
            timeline_end=panel["timeline_end"],
        )

    def _write_metadata(self, root, version, released, captured_at):
        directory = os.path.join(root, version)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "metadata.yml"), "w", encoding="utf-8") as file:
            file.write(
                textwrap.dedent(
                    f"""\
                    version: '{version}'
                    released: '{released}T00:00:00Z'
                    capture:
                      captured_at: '{captured_at}T00:00:00Z'
                    system_prompts:
                    - model: gpt-test
                      token_count: 100
                      token_measurement:
                        status: measured
                        model: gpt-test
                      tools:
                      - canonical_name: tool
                        definition_token_count: 50
                    """
                )
            )

    def _record(self, version, date, variants):
        return {
            "version": version,
            "dirname": version,
            "date": dt(date),
            "captured_at": dt(date),
            "variants": variants,
        }


if __name__ == "__main__":
    unittest.main()
