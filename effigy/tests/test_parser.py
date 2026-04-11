"""Tests for effigy parser — .effigy notation → CharacterAST."""

from pathlib import Path

import pytest

from effigy.notation import NarrativeRole
from effigy.parser import parse

# ---------------------------------------------------------------------------
# Minimal notation for unit tests
# ---------------------------------------------------------------------------

MINIMAL = """
@id test_npc
@name Test NPC
@role test
@arch ca_test
@narr ally

VOICE{
  kernel: Sharp and dry.
  peak: The dryness drops.
}
"""

FULL_HEADER = """
@id test_reporter
@name Test Reporter
@role merchant
@arch ca_truth_seeker
@narr ally
@presence Papers spread across the table.
@tropes ca_truth_seeker, np_document_chain, np_wrong_question
"""


class TestHeaders:
    def test_parse_id(self):
        ast = parse(MINIMAL)
        assert ast.char_id == "test_npc"

    def test_parse_name(self):
        ast = parse(MINIMAL)
        assert ast.name == "Test NPC"

    def test_parse_role(self):
        ast = parse(MINIMAL)
        assert ast.role == "test"

    def test_parse_archetype(self):
        ast = parse(MINIMAL)
        assert ast.archetype == "ca_test"

    def test_parse_narrative_role(self):
        ast = parse(MINIMAL)
        assert ast.narrative_role == NarrativeRole.ALLY

    def test_parse_presence(self):
        ast = parse(FULL_HEADER)
        assert "Papers spread" in ast.presence_note

    def test_parse_tropes(self):
        ast = parse(FULL_HEADER)
        assert len(ast.trope_tags) == 3
        assert "ca_truth_seeker" in ast.trope_tags

    def test_invalid_narrative_role_defaults_to_neutral(self):
        text = "@id x\n@narr invalid_role\n"
        ast = parse(text)
        assert ast.narrative_role == NarrativeRole.NEUTRAL


class TestVoice:
    def test_parse_voice_kernel(self):
        ast = parse(MINIMAL)
        assert ast.voice is not None
        assert ast.voice.kernel == "Sharp and dry."

    def test_parse_voice_peak(self):
        ast = parse(MINIMAL)
        assert ast.voice.peak == "The dryness drops."

    def test_voice_without_peak(self):
        text = "@id x\nVOICE{\n  kernel: Just a kernel.\n}\n"
        ast = parse(text)
        assert ast.voice.kernel == "Just a kernel."
        assert ast.voice.peak == ""
        assert ast.voice.peak_when == ""

    def test_parse_voice_peak_when(self):
        text = (
            "@id x\nVOICE{\n  kernel: k.\n  peak: p.\n"
            "  peak_when: ruin>=4 or trust>=0.8\n}\n"
        )
        ast = parse(text)
        assert ast.voice.peak == "p."
        assert ast.voice.peak_when == "ruin>=4 or trust>=0.8"


class TestMesExamples:
    def test_parse_mes_examples(self):
        text = """
@id x
MES[
{{char}}: First line of dialogue.
---
{{char}}: Second line of dialogue.
---
{{char}}: Third line.
]
"""
        ast = parse(text)
        assert len(ast.mes_examples) == 3
        assert "First line" in ast.mes_examples[0].text

    def test_auto_prefix(self):
        text = "@id x\nMES[\nJust a line without prefix.\n]\n"
        ast = parse(text)
        assert ast.mes_examples[0].text.startswith("{{char}}: ")

    def test_preserves_existing_prefix(self):
        text = "@id x\nMES[\n{{char}}: Already prefixed.\n]\n"
        ast = parse(text)
        assert ast.mes_examples[0].text == "{{char}}: Already prefixed."
        # Should NOT double-prefix
        assert not ast.mes_examples[0].text.startswith("{{char}}: {{char}}:")


class TestUncertaintyVoice:
    def test_parse_uncertainty(self):
        text = """
@id x
UNC[
{{char}}: I don't know.
---
{{char}}: Not sure about that.
]
"""
        ast = parse(text)
        assert len(ast.uncertainty_voice) == 2


class TestArcPhases:
    def test_parse_arc_phases(self):
        text = """
@id x
ARC{
  guarded → trust>=0.0
    voice: "Polite distance."
  thawing → trust>=0.2 AND fact:knows_her_name
    voice: "Pauses longer."
  resolved → trust>=0.6 AND ruin>=4
    voice: "Clear. Direct."
}
"""
        ast = parse(text)
        assert len(ast.arc_phases) == 3
        assert ast.arc_phases[0].name == "guarded"
        assert ast.arc_phases[1].name == "thawing"
        assert ast.arc_phases[2].name == "resolved"

    def test_arc_conditions(self):
        text = """
@id x
ARC{
  phase1 → trust>=0.35 AND fact:some_fact
    voice: "Voice shift."
}
"""
        ast = parse(text)
        phase = ast.arc_phases[0]
        assert "trust" in phase.conditions
        assert phase.conditions["trust"]["op"] == ">="
        assert phase.conditions["trust"]["value"] == 0.35
        assert "facts" in phase.conditions
        assert "some_fact" in phase.conditions["facts"]

    def test_arc_voice(self):
        text = '@id x\nARC{\n  test → trust>=0.0\n    voice: "The voice shift."\n}\n'
        ast = parse(text)
        assert ast.arc_phases[0].voice == "The voice shift."


class TestGoals:
    def test_parse_goals(self):
        text = """
@id x
GOALS{
  protect_memory   0.9
  help_stranger    0.3   → grows with trust
  find_truth       0.8   → grows with evidence
}
"""
        ast = parse(text)
        assert len(ast.goals) == 3
        assert ast.goals[0].name == "protect_memory"
        assert ast.goals[0].weight == 0.9
        assert ast.goals[0].grows_with == ""

    def test_grows_with(self):
        text = "@id x\nGOALS{\n  help 0.3 → grows with trust\n}\n"
        ast = parse(text)
        assert ast.goals[0].grows_with == "trust"


class TestSecrets:
    def test_parse_secrets(self):
        text = """
@id x
SECRETS[
layer: 1
secret: The first secret.
reveal: When trust is high.
era: incident
---
layer: 2
secret: The second secret.
reveal: When player discovers X.
era: present
]
"""
        ast = parse(text)
        assert len(ast.secrets) == 2
        assert ast.secrets[0].layer == 1
        assert "first secret" in ast.secrets[0].secret
        assert ast.secrets[0].reveal_condition == "When trust is high."
        assert ast.secrets[0].related_era == "incident"

    def test_secret_layer_2(self):
        text = """
@id x
SECRETS[
layer: 2
secret: A moderate secret.
reveal: Moderate trust required.
era: present
]
"""
        ast = parse(text)
        assert ast.secrets[0].layer == 2


class TestRelationships:
    def test_parse_rels(self):
        text = """
@id x
RELS{
  town_mayor protects 0.6 "Owes her a favor."
  old_fisher trusts 0.8 "Her most loyal regular."
}
"""
        ast = parse(text)
        assert len(ast.relationships) == 2
        assert ast.relationships[0].target == "town_mayor"
        assert ast.relationships[0].rel_type == "protects"
        assert ast.relationships[0].intensity == 0.6
        assert "favor" in ast.relationships[0].notes


class TestSchedule:
    def test_parse_schedule(self):
        text = """
@id x
SCHED{
  morning: home
  afternoon: library
  evening: tavern
  night: home
}
"""
        ast = parse(text)
        assert ast.schedule is not None
        assert ast.schedule.morning == "home"
        assert ast.schedule.afternoon == "library"
        assert ast.schedule.evening == "tavern"
        assert ast.schedule.night == "home"

    def test_partial_schedule(self):
        text = "@id x\nSCHED{\n  morning: tavern\n  afternoon: tavern\n}\n"
        ast = parse(text)
        assert ast.schedule.morning == "tavern"
        assert ast.schedule.night is None


class TestEraStates:
    def test_parse_eras(self):
        text = """
@id x
ERA[
era: founding
status: unborn
---
era: present
status: alive
age: 34
occupation: Reporter
disposition: Investigating.
notes: Has been in town 4 months.
]
"""
        ast = parse(text)
        assert len(ast.era_states) == 2
        assert ast.era_states[0].era_id == "founding"
        assert ast.era_states[0].status == "unborn"
        assert ast.era_states[1].age == 34
        assert ast.era_states[1].occupation == "Reporter"


class TestDrivermap:
    def test_parse_drivermap(self):
        text = """
@id x
DM{
  big_five_O: +
  bas_sensitivity: +
  need_for_cognition: +
  features: novelty, stakes, ambiguity
}
"""
        ast = parse(text)
        assert ast.drivermap is not None
        assert ast.drivermap.profile["big_five_O"] == "+"
        assert len(ast.drivermap.situation_features) == 3
        assert "novelty" in ast.drivermap.situation_features


class TestArrivalDeparture:
    def test_parse_arrival(self):
        text = "@id x\nARRIVE[\n*slides into booth*\n---\n*pushes through door*\n]\n"
        ast = parse(text)
        assert len(ast.arrival_lines) == 2
        assert "*slides into booth*" in ast.arrival_lines[0]

    def test_parse_departure(self):
        text = "@id x\nDEPART[\n*closes binder*\n]\n"
        ast = parse(text)
        assert len(ast.departure_lines) == 1


class TestBehaviors:
    def test_parse_behaviors(self):
        text = """
@id x
BEHAVIORS{
  keep_peace: Redirects heat with hospitality.
  protect_regulars: Never names them directly.
}
"""
        ast = parse(text)
        assert ast.goal_behaviors["keep_peace"] == "Redirects heat with hospitality."
        assert ast.goal_behaviors["protect_regulars"] == "Never names them directly."

    def test_empty_behaviors(self):
        text = "@id x\nBEHAVIORS{\n}\n"
        ast = parse(text)
        assert ast.goal_behaviors == {}

    def test_multiline_behavior_value(self):
        text = """
@id x
BEHAVIORS{
  keep_peace: Redirects heat with hospitality.
    Refills drinks to cut off arguments.
}
"""
        ast = parse(text)
        # Continuation lines are appended to the previous key's value
        assert "Redirects heat" in ast.goal_behaviors["keep_peace"]
        assert "Refills drinks" in ast.goal_behaviors["keep_peace"]


class TestNeverWouldSay:
    def test_parse_never(self):
        text = """@id x
NEVER[
  Never uses journalistic precision
  ---
  Never asks probing follow-up questions
  ---
  Never brings up past events directly until vulnerable phase
]
"""
        ast = parse(text)
        assert len(ast.never_would_say) == 3
        assert "journalistic" in ast.never_would_say[0]

    def test_empty_never(self):
        text = "@id x\nNEVER[\n]\n"
        ast = parse(text)
        assert ast.never_would_say == []


class TestQuirks:
    def test_parse_quirks(self):
        text = """@id x
QUIRKS[
  Wipes the same spot on the counter when uncomfortable
  ---
  Refills coffee without being asked
  ---
  Checks the clock when she wants a conversation to end
]
"""
        ast = parse(text)
        assert len(ast.quirks) == 3
        assert "counter" in ast.quirks[0]
        assert "coffee" in ast.quirks[1]


class TestTheme:
    def test_parse_theme_header(self):
        text = "@id x\n@theme The cost of loyalty to the dead\n"
        ast = parse(text)
        assert "loyalty" in ast.theme


class TestComments:
    def test_comments_ignored(self):
        text = "# This is a comment\n@id test_npc\n# Another comment\n@name Test\n"
        ast = parse(text)
        assert ast.char_id == "test_npc"
        assert ast.name == "Test"


class TestEmptyInput:
    def test_empty_string(self):
        ast = parse("")
        assert ast.char_id == ""
        assert ast.name == ""

    def test_whitespace_only(self):
        ast = parse("   \n\n  \n")
        assert ast.char_id == ""


# ---------------------------------------------------------------------------
# Integration: parse real .effigy files
# ---------------------------------------------------------------------------

# Integration test fixture directory
_EFFIGY_DIRS = [
    Path(__file__).parent / "fixtures",
    Path(__file__).parent.parent / "test-notations",
]


def _find_effigy(filename: str) -> Path | None:
    for d in _EFFIGY_DIRS:
        p = d / filename
        if p.exists():
            return p
    return None


# For backwards compat with tests that use EFFIGY_DIR directly
EFFIGY_DIR = _EFFIGY_DIRS[0]


@pytest.mark.parametrize(
    "filename",
    [
        "test_npc.effigy",
    ],
)
class TestRealFiles:
    def _load(self, filename):
        path = _find_effigy(filename)
        if path is None:
            pytest.skip(f"{filename} not found")
        return parse(path.read_text())

    def test_parses_without_error(self, filename):
        ast = self._load(filename)
        assert ast.char_id != ""

    def test_has_voice(self, filename):
        ast = self._load(filename)
        assert ast.voice is not None
        assert ast.voice.kernel != ""

    def test_has_mes_examples(self, filename):
        ast = self._load(filename)
        assert len(ast.mes_examples) >= 2  # v0.2: Ali:Chat uses fewer, richer examples

    def test_has_secrets(self, filename):
        ast = self._load(filename)
        assert len(ast.secrets) >= 1, f"{filename}: no secrets parsed"
        # Every NPC should have secrets across multiple layers
        layers = {s.layer for s in ast.secrets}
        assert len(layers) >= 2, f"{filename}: secrets span only {layers}"

    def test_has_relationships(self, filename):
        ast = self._load(filename)
        assert len(ast.relationships) >= 4

    def test_has_arc_phases(self, filename):
        ast = self._load(filename)
        assert len(ast.arc_phases) >= 3

    def test_has_goals(self, filename):
        ast = self._load(filename)
        assert len(ast.goals) >= 3
