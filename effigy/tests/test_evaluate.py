"""Tests for effigy evaluation — roundtrip fidelity scoring."""

from pathlib import Path

import pytest

from effigy.evaluate import EvalResult, evaluate_effigy_file, evaluate_tier1

# ---------------------------------------------------------------------------
# Synthetic data for unit tests
# ---------------------------------------------------------------------------

def _make_original():
    """A minimal corpus-style JSON dict."""
    return {
        "char_id": "test_npc",
        "name": "Test NPC",
        "role": "tester",
        "archetype": "ca_test",
        "narrative_role": "ally",
        "voice_kernel": "Sharp and dry.",
        "presence_note": "Standing there.",
        "peak_voice": "The sharpness drops.",
        "mes_examples": ["{{char}}: One.", "{{char}}: Two.", "{{char}}: Three."],
        "uncertainty_voice": ["{{char}}: Dunno."],
        "secrets": [
            {"layer": 1, "secret": "A secret.", "reveal_condition": "Ask.", "related_era": "present"},
        ],
        "relationships": [
            {"target": "other_npc", "type": "trusts", "intensity": 0.5, "notes": "Friends."},
        ],
        "schedule": {"morning": "tavern", "afternoon": "library", "evening": None, "night": None},
        "era_states": [
            {"era_id": "present", "status": "alive", "age": 30},
        ],
        "drivermap_profile": {"big_five_O": "+", "big_five_C": "+"},
        "npc_situation_features": ["novelty", "stakes"],
        "trope_tags": ["ca_test", "np_test"],
        "arrival_lines": ["*walks in*"],
        "departure_lines": ["*walks out*"],
    }


class TestTier1PerfectMatch:
    """When expanded matches original exactly, score should be 1.0."""

    def test_perfect_score(self):
        original = _make_original()
        result = evaluate_tier1(original, original.copy())
        assert result.tier1_score == 1.0
        assert not result.missing_fields

    def test_char_id_in_result(self):
        original = _make_original()
        result = evaluate_tier1(original, original)
        assert result.char_id == "test_npc"


class TestTier1PartialMatch:
    def test_wrong_char_id(self):
        original = _make_original()
        expanded = _make_original()
        expanded["char_id"] = "wrong_id"
        result = evaluate_tier1(original, expanded)
        assert result.tier1_score < 1.0
        # Check the specific field scored 0
        char_id_score = next(fs for fs in result.field_scores if fs.field_name == "char_id")
        assert char_id_score.score == 0.0

    def test_fewer_mes_examples(self):
        original = _make_original()
        expanded = _make_original()
        expanded["mes_examples"] = ["{{char}}: One."]  # 1 of 3
        result = evaluate_tier1(original, expanded)
        mes_score = next(fs for fs in result.field_scores if fs.field_name == "mes_examples")
        assert 0.3 <= mes_score.score <= 0.4  # 1/3

    def test_more_mes_examples(self):
        """More examples than original should cap at 1.0."""
        original = _make_original()
        expanded = _make_original()
        expanded["mes_examples"] = ["{{char}}: One."] * 5
        result = evaluate_tier1(original, expanded)
        mes_score = next(fs for fs in result.field_scores if fs.field_name == "mes_examples")
        assert mes_score.score == 1.0

    def test_missing_voice_kernel(self):
        original = _make_original()
        expanded = _make_original()
        expanded["voice_kernel"] = ""
        result = evaluate_tier1(original, expanded)
        vk_score = next(fs for fs in result.field_scores if fs.field_name == "voice_kernel")
        assert vk_score.score == 0.0
        assert "voice_kernel" in result.missing_fields

    def test_different_voice_kernel(self):
        """Different but present voice_kernel gets partial credit."""
        original = _make_original()
        expanded = _make_original()
        expanded["voice_kernel"] = "Completely different voice."
        result = evaluate_tier1(original, expanded)
        vk_score = next(fs for fs in result.field_scores if fs.field_name == "voice_kernel")
        assert vk_score.score == 0.5

    def test_schedule_partial(self):
        original = _make_original()
        expanded = _make_original()
        expanded["schedule"] = {"morning": "tavern", "afternoon": "park", "evening": None, "night": None}
        result = evaluate_tier1(original, expanded)
        sched_score = next(fs for fs in result.field_scores if fs.field_name == "schedule")
        assert sched_score.score == 0.75  # 3/4 match

    def test_missing_relationship_target(self):
        original = _make_original()
        expanded = _make_original()
        expanded["relationships"] = [
            {"target": "wrong_npc", "type": "trusts", "intensity": 0.5, "notes": ""},
        ]
        result = evaluate_tier1(original, expanded)
        rel_score = next(fs for fs in result.field_scores if fs.field_name == "relationship_targets")
        assert rel_score.score == 0.0

    def test_drivermap_partial(self):
        original = _make_original()
        expanded = _make_original()
        expanded["drivermap_profile"] = {"big_five_O": "+", "big_five_C": "-"}  # C wrong
        result = evaluate_tier1(original, expanded)
        dm_score = next(fs for fs in result.field_scores if fs.field_name == "drivermap_profile")
        assert dm_score.score == 0.5

    def test_missing_tropes(self):
        original = _make_original()
        expanded = _make_original()
        expanded["trope_tags"] = ["ca_test"]  # missing np_test
        result = evaluate_tier1(original, expanded)
        trope_score = next(fs for fs in result.field_scores if fs.field_name == "trope_tags")
        assert trope_score.score == 0.5


class TestTier1EdgeCases:
    def test_empty_original(self):
        """Both empty should score 1.0 for most fields."""
        original = {"char_id": "", "name": ""}
        expanded = {"char_id": "", "name": ""}
        result = evaluate_tier1(original, expanded)
        assert result.tier1_score > 0.5

    def test_no_secrets_both(self):
        original = _make_original()
        expanded = _make_original()
        original["secrets"] = []
        expanded["secrets"] = []
        result = evaluate_tier1(original, expanded)
        sec_score = next(fs for fs in result.field_scores if fs.field_name == "secrets")
        assert sec_score.score == 1.0


class TestEvalResultSummary:
    def test_summary_includes_char_id(self):
        result = EvalResult(char_id="test_npc", tier1_score=0.85)
        assert "test_npc" in result.summary()
        assert "85.00%" in result.summary()


# ---------------------------------------------------------------------------
# Integration: evaluate real .effigy against real .json
# ---------------------------------------------------------------------------

# Integration tests require matching .effigy + .json pairs from a game corpus.
# Set EFFIGY_CORPUS_DIR env var to point to your corpus.
import os as _os
_EFFIGY_DIRS = [
    Path(__file__).parent / "fixtures",
    Path(__file__).parent.parent / "test-notations",
]
CORPUS_DIR = Path(_os.environ.get("EFFIGY_CORPUS_DIR", "/nonexistent"))


def _find_effigy(filename: str) -> Path | None:
    for d in _EFFIGY_DIRS:
        p = d / filename
        if p.exists():
            return p
    return None


@pytest.mark.parametrize("effigy_file,json_file", [
    # Add effigy_filename, json_filename pairs here.
    # Set EFFIGY_CORPUS_DIR to point to the JSON corpus directory.
])
class TestRealFileEvaluation:
    """Evaluate real .effigy files against corpus JSON."""

    def test_tier1_above_threshold(self, effigy_file, json_file):
        effigy_path = _find_effigy(effigy_file)
        json_path = CORPUS_DIR / json_file
        if effigy_path is None or not json_path.exists():
            pytest.skip("files not found")
        result = evaluate_effigy_file(effigy_path, json_path)
        # v0.2 dossiers intentionally diverge on prose fields;
        # structural fields should still score well
        assert result.tier1_score >= 0.60, (
            f"{effigy_file} Tier 1 score {result.tier1_score:.2%} below threshold.\n"
            f"{result.summary()}"
        )

    def test_exact_fields_match(self, effigy_file, json_file):
        """char_id, name, role should match exactly."""
        effigy_path = _find_effigy(effigy_file)
        json_path = CORPUS_DIR / json_file
        if effigy_path is None or not json_path.exists():
            pytest.skip("files not found")
        result = evaluate_effigy_file(effigy_path, json_path)
        for fs in result.field_scores:
            if fs.field_name in ("char_id", "name", "role"):
                assert fs.score == 1.0, f"{fs.field_name}: {fs.details}"
