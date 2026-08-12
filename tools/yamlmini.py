"""Tiny YAML loader for the fixed shapes used in this repo's metadata files.

Uses PyYAML when it's importable (that's what the GitHub Action installs).
Falls back to a hand-rolled parser that understands exactly the subset of
YAML this repo's generators emit: nested mappings, sequences of mappings,
quoted/plain scalars, `null`, and ints. It is not a general YAML parser and
will raise on anything outside that subset rather than silently misparse.
"""
from __future__ import annotations

import re

try:
    import yaml as _pyyaml  # type: ignore
except ImportError:  # pragma: no cover - exercised locally without pyyaml
    _pyyaml = None


def load_yaml_file(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return load_yaml(text)


def load_yaml(text: str):
    if _pyyaml is not None:
        return _pyyaml.safe_load(text)
    return _MiniYamlParser(text).parse()


class YamlError(ValueError):
    pass


def _leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _unquote(s: str):
    s = s.strip()
    if s == "" or s == "~" or s.lower() == "null":
        return None
    if s == "[]":
        return []
    if s == "{}":
        return {}
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = s[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1].replace("''", "'")
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    return s


class _MiniYamlParser:
    def __init__(self, text: str):
        raw = text.split("\n")
        self.lines = []
        for ln in raw:
            if "\t" in ln:
                raise YamlError("tabs are not supported by the minimal YAML parser")
            stripped = ln.strip()
            if stripped == "" or stripped.startswith("#"):
                continue
            self.lines.append(ln)

    def parse(self):
        if not self.lines:
            return {}
        indent = _leading_spaces(self.lines[0])
        content0 = self.lines[0][indent:]
        if content0.startswith("- "):
            value, _ = self._parse_sequence(0, indent)
        else:
            value, _ = self._parse_mapping(0, indent)
        return value

    def _parse_mapping(self, i, indent, first_pair=None):
        result = {}
        if first_pair is not None:
            key, rest = first_pair
            result[key] = self._resolve_scalar_or_defer(rest)
        n = len(self.lines)
        while i < n:
            line = self.lines[i]
            cur_indent = _leading_spaces(line)
            if cur_indent < indent:
                break
            if cur_indent > indent:
                raise YamlError(f"unexpected indent at line: {line!r}")
            content = line[cur_indent:]
            if content.startswith("- "):
                break
            key, sep, rest = content.partition(":")
            if not sep:
                raise YamlError(f"expected 'key: value' at line: {line!r}")
            key = key.strip()
            rest = rest.strip()
            if rest == "":
                i += 1
                if i < n:
                    child_indent = _leading_spaces(self.lines[i])
                    child_content = self.lines[i][child_indent:]
                    if child_indent >= cur_indent and child_content.startswith("- "):
                        # A block sequence's "- " markers are valid YAML at the
                        # *same* indent as their parent key (this repo's real
                        # metadata.yml files use exactly this style for
                        # system_prompts/tools), as well as indented deeper.
                        value, i = self._parse_sequence(i, child_indent)
                    elif child_indent > cur_indent and child_content.strip() in ("[]", "{}"):
                        value = _unquote(child_content.strip())
                        i += 1
                    elif child_indent > cur_indent:
                        value, i = self._parse_mapping(i, child_indent)
                    else:
                        # Same-or-lower indent and not a sequence marker: the
                        # next line is a sibling key (or a dedent) — this
                        # key's value is empty/null.
                        value = None
                else:
                    value = None
            else:
                value = _unquote(rest)
                i += 1
            result[key] = value
        return result, i

    def _resolve_scalar_or_defer(self, rest):
        return _unquote(rest)

    def _parse_sequence(self, i, indent):
        result = []
        n = len(self.lines)
        while i < n:
            line = self.lines[i]
            cur_indent = _leading_spaces(line)
            if cur_indent != indent:
                break
            content = line[cur_indent:]
            if not content.startswith("- "):
                break
            item_content = content[2:]
            if ":" in item_content and not item_content.lstrip().startswith(('"', "'")):
                key, sep, rest = item_content.partition(":")
                rest = rest.strip()
                item_indent = cur_indent + 2
                mapping, i = self._parse_mapping(i + 1, item_indent, first_pair=(key.strip(), rest))
                result.append(mapping)
            else:
                result.append(_unquote(item_content))
                i += 1
        return result, i
