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


def measured(combined):
    return {"status": "measured", "has_chart_data": True, "combined": combined}


class ModelFamilyChartTests(unittest.TestCase):
    def test_catalog_rejects_duplicate_model_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "catalog.yml")
            with open(path, "w", encoding="utf-8") as file:
                file.write(
                    textwrap.dedent(
                        """\
                        models:
                        - harness: codex
                          id: gpt-test
                          family: gpt
                          display_name: GPT Test
                          released: '2026-01-01'
                          sources:
                          - "https://example.com/release"
                        - harness: codex
                          id: gpt-test
                          family: gpt
                          display_name: GPT Test duplicate
                          released: '2026-01-02'
                          sources:
                          - "https://example.com/release"
                        """
                    )
                )
            with self.assertRaisesRegex(ValueError, "duplicate model codex/gpt-test"):
                generate_readme.load_model_catalog(path)

    def test_cli_release_date_controls_family_chart_coordinates(self):
        catalog = [
            {
                "harness": "codex",
                "id": "gpt-test",
                "family": "gpt",
                "display_name": "GPT Test",
                "released_at": dt("2025-01-01"),
                "retired_at": None,
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            self._write_metadata(directory, "1.0.0", "2025-02-01", "2030-01-01")
            self._write_metadata(directory, "1.1.0", "2025-03-01", "2030-02-01")
            before = self._family_svg(directory, catalog)

            # A historical backfill may run years later. Changing only that
            # capture timestamp must not move either chart point.
            self._write_metadata(directory, "1.1.0", "2025-03-01", "2040-12-31")
            after = self._family_svg(directory, catalog)

        self.assertEqual(before, after)
        self.assertIn("Feb 2025", before)
        self.assertIn("Mar 2025", before)
        self.assertNotIn("2040", before)

    def test_partial_and_missing_measurements_break_series(self):
        model = "claude-opus-test"
        records = [
            self._record("1", "2026-01-01", {model: measured(100)}),
            self._record(
                "2",
                "2026-01-02",
                {model: {"status": "partial", "has_chart_data": False, "combined": None}},
            ),
            self._record("3", "2026-01-03", {model: measured(120)}),
            self._record("4", "2026-01-04", {}),
            self._record("5", "2026-01-05", {model: measured(140)}),
        ]
        catalog = [
            {
                "harness": "claude-code",
                "id": model,
                "family": "opus",
                "display_name": "Claude Opus Test",
                "released_at": dt("2026-01-01"),
                "retired_at": None,
            }
        ]
        panel = generate_readme.build_family_panels("claude-code", records, catalog)[0]
        self.assertEqual(
            [[100], [120], [140]],
            [
                [point["value"] for point in segment]
                for segment in panel["series"][0]["segments"]
            ],
        )

    def test_recorded_measurement_before_model_release_is_retained(self):
        model = "claude-opus-test"
        records = [self._record("1", "2025-12-01", {model: measured(100)})]
        catalog = [
            {
                "harness": "claude-code",
                "id": model,
                "family": "opus",
                "display_name": "Claude Opus Test",
                "released_at": dt("2026-01-01"),
                "retired_at": None,
            }
        ]
        panel = generate_readme.build_family_panels("claude-code", records, catalog)[0]
        point = panel["series"][0]["segments"][0][0]
        self.assertEqual(dt("2025-12-01"), point["date"])
        self.assertEqual(100, point["value"])

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

        self.assertTrue(variants["good-model"]["has_chart_data"])
        self.assertEqual(150, variants["good-model"]["combined"])
        self.assertFalse(variants["partial-model"]["has_chart_data"])
        self.assertFalse(variants["mismatched-model"]["has_chart_data"])
        self.assertFalse(variants["incomplete-tools-model"]["has_chart_data"])

    def test_uncataloged_model_is_visible_without_guessed_release(self):
        model = "claude-opus-unknown"
        records = [self._record("1", "2026-01-01", {model: measured(100)})]
        panel = generate_readme.build_family_panels("claude-code", records, [])[0]
        self.assertEqual("opus", panel["family"])
        self.assertEqual([model], [series["model"] for series in panel["series"]])
        self.assertEqual([model], panel["unknown_releases"])
        self.assertEqual([], panel["releases"])

    def test_same_day_releases_share_marker_and_key(self):
        released = dt("2026-07-09")
        svg = chart.render_family_chart_svg(
            "Codex",
            "GPT 5.4–6",
            [],
            [
                {
                    "date": released,
                    "models": [{"model": "gpt-a", "label": "GPT A", "has_data": False}],
                },
                {
                    "date": released,
                    "models": [{"model": "gpt-b", "label": "GPT B", "has_data": False}],
                },
            ],
            [],
        )
        self.assertEqual(1, svg.count('data-release-date="2026-07-09"'))
        self.assertIn("R1  09 Jul 2026 · GPT A† / GPT B†", svg)
        self.assertNotIn(">R2<", svg)
        self.assertNotIn('data-series-model="gpt-a"', svg)

    def test_unknown_release_only_chart_has_no_invented_axis_date(self):
        svg = chart.render_family_chart_svg(
            "Codex",
            "Other models",
            [],
            [],
            ["gpt-unknown"],
        )
        self.assertIn("Release date not cataloged · gpt-unknown", svg)
        self.assertNotIn("1970", svg)
        self.assertNotIn("Jan 1970", svg)

    def _family_svg(self, harness_dir, catalog):
        records = generate_readme.load_versions(harness_dir)
        panel = generate_readme.build_family_panels("codex", records, catalog)[0]
        return chart.render_family_chart_svg(
            "Codex",
            panel["label"],
            panel["series"],
            panel["releases"],
            panel["unknown_releases"],
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
            "date": dt(date),
            "captured_at": dt(date),
            "variants": variants,
        }


if __name__ == "__main__":
    unittest.main()
