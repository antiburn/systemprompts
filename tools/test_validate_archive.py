import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

import yaml

from validate_archive import ArchiveError, validate_metadata


class ArchiveValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.version = Path(self.temp.name) / "1.2.3"
        self.prompt_dir = self.version / "test-model"
        self.prompt_dir.mkdir(parents=True)
        self.raw = "Instructions with Unicode: café 🌍\n".encode()
        (self.prompt_dir / "systemprompt.txt").write_bytes(self.raw)
        (self.prompt_dir / "systemprompt.md").write_text("Rendered prompt")
        self.entry = {
            "model": "test-model", "directory": "test-model",
            "character_count": len(self.raw.decode()), "token_count": 12,
            "content_sha256": hashlib.sha256(self.raw).hexdigest(),
            "token_measurement": {
                "provider": "openai", "model": "test-model", "status": "measured",
                "method": "native marginal token measurement",
                "measured_at": "2026-09-06T01:00:00Z",
            },
            "tools": [{
                "canonical_name": "shell", "observed_raw_aliases": ["shell"],
                "definition_bytes": 120, "definition_token_count": 25,
            }],
        }
        self.path = self.version / "metadata.yml"

    def write(self, entry=None):
        self.path.write_text(yaml.safe_dump({
            "version": "1.2.3", "system_prompts": [entry or self.entry],
        }))

    def test_valid_native_and_explicit_partial_measurements(self):
        self.write()
        self.assertEqual(validate_metadata(self.path, "openai"), 1)
        self.entry["token_measurement"]["status"] = "partial"
        self.entry["tools"][0]["definition_token_count"] = None
        self.entry["tools"][0]["definition_token_count_unavailable_reason"] = "provider_tool_surface_unsupported"
        self.write()
        self.assertEqual(validate_metadata(self.path, "openai"), 1)

    def test_rejects_corrupt_bytes_and_missing_rendered_prompt(self):
        self.write()
        (self.prompt_dir / "systemprompt.txt").write_bytes(self.raw.replace(b'caf', b'bad'))
        with self.assertRaisesRegex(ArchiveError, "SHA-256 mismatch"):
            validate_metadata(self.path, "openai")
        (self.prompt_dir / "systemprompt.txt").write_bytes(self.raw)
        (self.prompt_dir / "systemprompt.md").unlink()
        with self.assertRaisesRegex(ArchiveError, "missing systemprompt.md"):
            validate_metadata(self.path, "openai")

    def test_rejects_mismatched_provenance_and_unexplained_counts(self):
        for field, value, expected in [
            ("model", "different", "model mismatch"),
            ("provider", "anthropic", "provider mismatch"),
            ("status", "partial", "partial measurement status"),
            ("measured_at", "2026-09-06", "no timezone"),
        ]:
            with self.subTest(field=field):
                entry = copy.deepcopy(self.entry)
                entry["token_measurement"][field] = value
                self.write(entry)
                with self.assertRaisesRegex(ArchiveError, expected):
                    validate_metadata(self.path, "openai")
        self.entry["tools"][0]["definition_token_count"] = None
        self.write()
        with self.assertRaisesRegex(ArchiveError, "without an unavailable reason"):
            validate_metadata(self.path, "openai")
        self.entry["tools"][0]["definition_token_count"] = 0
        self.write()
        with self.assertRaisesRegex(ArchiveError, "invalid or conflicting tool token count"):
            validate_metadata(self.path, "openai")

    def test_preserves_explicit_unretained_records(self):
        self.write({
            "model": "unrecorded", "directory": None, "content": "unretained",
            "token_count": None, "token_measurement": {
                "provider": "openai", "model": "unrecorded", "status": "unavailable",
                "failure_code": "captured_model_unrecorded",
            },
        })
        self.assertEqual(validate_metadata(self.path, "openai"), 1)

    def test_rejects_directory_escape_and_ambiguous_yaml(self):
        self.entry["directory"] = "../test-model"
        self.write()
        with self.assertRaisesRegex(ArchiveError, "invalid prompt directory"):
            validate_metadata(self.path, "openai")
        self.entry["directory"] = "test-model"
        self.write()
        self.path.write_text(self.path.read_text() + 'version: "1.2.3"\n')
        with self.assertRaisesRegex(ArchiveError, "duplicate YAML key"):
            validate_metadata(self.path, "openai")


if __name__ == "__main__":
    unittest.main()
