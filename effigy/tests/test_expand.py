"""Tests for effigy Layer 1 — AST → JSON expansion."""

import json
from pathlib import Path

import pytest

from effigy.expand import expand, expand_to_json
from effigy.parser import parse

MINIMAL = """
@id test_npc
@name Test NPC
@role test
@arch ca_test
@narr ally
@presence Standing there.
@tropes ca_test, np_test

VOICE{
  kernel: Sharp and dry.
  peak: The dryness drops.
}

MES[
{{char}}: First example.
---
{{char}}: Second example.
]

UNC[
{{char}}: Don't know.
]

SECRETS[
layer: 1
secret: A secret.
reveal: Ask nicely.
era: present
]

RELS{
  other_npc trusts 0.5 "Good friends."
}

SCHED{
  morning: tavern
  afternoon: library
}

ERA[
era: present
status: alive
age: 30
occupation: Tester
disposition: Testing things.
notes: A test NPC.
]

DM{
  big_five_O: +
  features: novelty, stakes
}

ARRIVE[
*walks in*
]

DEPART[
*walks out*
]
"""


class TestExpand:
    def setup_method(self):
        self.ast = parse(MINIMAL)
        self.result = expand(self.ast)

    def test_char_id(self):
        assert self.result["char_id"] == "test_npc"

    def test_name(self):
        assert self.result["name"] == "Test NPC"

    def test_role(self):
        assert self.result["role"] == "test"

    def test_archetype(self):
        assert self.result["archetype"] == "ca_test"

    def test_narrative_role(self):
        assert self.result["narrative_role"] == "ally"

    def test_presence_note(self):
        assert self.result["presence_note"] == "Standing there."

    def test_voice_kernel(self):
        assert self.result["voice_kernel"] == "Sharp and dry."

    def test_peak_voice(self):
        assert self.result["peak_voice"] == "The dryness drops."

    def test_mes_examples(self):
        assert len(self.result["mes_examples"]) == 2
        assert "First example" in self.result["mes_examples"][0]

    def test_uncertainty_voice(self):
        assert len(self.result["uncertainty_voice"]) == 1

    def test_trope_tags(self):
        assert "ca_test" in self.result["trope_tags"]

    def test_secrets(self):
        assert len(self.result["secrets"]) == 1
        assert self.result["secrets"][0]["layer"] == 1

    def test_relationships(self):
        assert len(self.result["relationships"]) == 1
        assert self.result["relationships"][0]["target"] == "other_npc"

    def test_schedule(self):
        assert self.result["schedule"]["morning"] == "tavern"
        assert self.result["schedule"]["night"] is None

    def test_era_states(self):
        assert len(self.result["era_states"]) == 1
        assert self.result["era_states"][0]["age"] == 30

    def test_drivermap_profile(self):
        assert self.result["drivermap_profile"]["big_five_O"] == "+"

    def test_situation_features(self):
        assert "novelty" in self.result["npc_situation_features"]

    def test_arrival_lines(self):
        assert len(self.result["arrival_lines"]) == 1

    def test_departure_lines(self):
        assert len(self.result["departure_lines"]) == 1

    def test_expand_to_json(self):
        json_str = expand_to_json(self.ast)
        parsed = json.loads(json_str)
        assert parsed["char_id"] == "test_npc"


# ---------------------------------------------------------------------------
# Roundtrip: .effigy → AST → JSON, compare to original JSON
# ---------------------------------------------------------------------------

# Roundtrip tests require matching .effigy + .json pairs from a game corpus.
# These are integration tests that skip when corpus files are not available.
_EFFIGY_DIRS = [
    Path(__file__).parent / "fixtures",
    Path(__file__).parent.parent / "test-notations",
]
# Set EFFIGY_CORPUS_DIR env var to point to your corpus for roundtrip tests
import os as _os
CORPUS_DIR = Path(_os.environ.get("EFFIGY_CORPUS_DIR", "/nonexistent"))


def _find_effigy(filename: str) -> Path | None:
    for d in _EFFIGY_DIRS:
        p = d / filename
        if p.exists():
            return p
    return None


@pytest.mark.parametrize(
    "char_id,json_file,effigy_file",
    [
        # Add your char_id, json_filename, effigy_filename triples here.
        # Set EFFIGY_CORPUS_DIR to point to the JSON corpus directory.
        # Example: ("innkeeper", "innkeeper.json", "innkeeper.effigy"),
    ],
)
class TestRoundtrip:
    """Parse .effigy → expand → compare key fields to original JSON."""

    def _load(self, char_id, json_file, effigy_file):
        json_path = CORPUS_DIR / json_file
        effigy_path = _find_effigy(effigy_file)
        if not json_path.exists() or effigy_path is None:
            pytest.skip("files not found")
        original = json.loads(json_path.read_text())
        ast = parse(effigy_path.read_text())
        expanded = expand(ast)
        return original, expanded

    def test_char_id_match(self, char_id, json_file, effigy_file):
        original, expanded = self._load(char_id, json_file, effigy_file)
        assert expanded["char_id"] == original["char_id"]

    def test_name_match(self, char_id, json_file, effigy_file):
        original, expanded = self._load(char_id, json_file, effigy_file)
        assert expanded["name"] == original["name"]

    def test_role_match(self, char_id, json_file, effigy_file):
        original, expanded = self._load(char_id, json_file, effigy_file)
        assert expanded["role"] == original["role"]

    def test_narrative_role_match(self, char_id, json_file, effigy_file):
        original, expanded = self._load(char_id, json_file, effigy_file)
        assert expanded["narrative_role"] == original["narrative_role"]

    def test_mes_examples_present(self, char_id, json_file, effigy_file):
        """v0.2 dossiers use fewer, richer Ali:Chat examples — check minimum."""
        _original, expanded = self._load(char_id, json_file, effigy_file)
        assert len(expanded["mes_examples"]) >= 2

    def test_secrets_count(self, char_id, json_file, effigy_file):
        original, expanded = self._load(char_id, json_file, effigy_file)
        # v0.2 dossiers may add secrets beyond the original JSON
        assert len(expanded["secrets"]) >= len(original["secrets"])

    def test_relationships_count(self, char_id, json_file, effigy_file):
        original, expanded = self._load(char_id, json_file, effigy_file)
        # v0.2 dossiers may add relationships beyond the original JSON
        assert len(expanded["relationships"]) >= len(original["relationships"])

    def test_relationship_targets(self, char_id, json_file, effigy_file):
        original, expanded = self._load(char_id, json_file, effigy_file)
        orig_targets = {r["target"] for r in original["relationships"]}
        exp_targets = {r["target"] for r in expanded["relationships"]}
        # Dossiers may add relationships beyond the original JSON
        assert orig_targets <= exp_targets, f"Missing targets: {orig_targets - exp_targets}"

    def test_schedule_match(self, char_id, json_file, effigy_file):
        original, expanded = self._load(char_id, json_file, effigy_file)
        for slot in ("morning", "afternoon", "evening"):
            assert expanded["schedule"].get(slot) == original["schedule"].get(slot), (
                f"Schedule mismatch on {slot}"
            )

    def test_drivermap_profile_match(self, char_id, json_file, effigy_file):
        original, expanded = self._load(char_id, json_file, effigy_file)
        if "drivermap_profile" in original:
            assert expanded.get("drivermap_profile") == original["drivermap_profile"]

    def test_voice_kernel_present(self, char_id, json_file, effigy_file):
        """v0.2 dossiers may rewrite voice — check non-empty, not exact match."""
        _original, expanded = self._load(char_id, json_file, effigy_file)
        assert expanded["voice_kernel"], "voice_kernel should be non-empty"

    def test_peak_voice_present(self, char_id, json_file, effigy_file):
        """v0.2 dossiers may rewrite peak voice — check non-empty."""
        original, expanded = self._load(char_id, json_file, effigy_file)
        if "peak_voice" in original:
            assert expanded.get("peak_voice"), "peak_voice should be non-empty"
