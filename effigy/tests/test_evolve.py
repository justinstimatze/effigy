"""Tests for effigy Layer 3 — dynamic profile evolution."""

import pytest

from effigy.evolve import (
    NpcMemoryState,
    build_evolution_context,
    build_synthesis_prompt,
    compute_emotional_state,
    compute_intentions,
    emotional_context,
    intentions_context,
    should_synthesize,
)
from effigy.parser import parse

INNKEEPER_NOTATION = """
@id test_innkeeper
@name Dael Renn
@arch ca_guardian

VOICE{
  kernel: Measured, warm, evasive.
}

ARC{
  guarded → trust>=0.0
    voice: "Polite distance."
  open → trust>=0.6 AND ruin>=4
    voice: "Direct."
}

GOALS{
  keep_peace       0.9
  help_newcomer    0.3   → grows with trust
  tell_truth       0.2   → grows with evidence
}
"""

OUTSIDER_NOTATION = """
@id test_outsider
@name Alex
@arch ca_outsider

VOICE{
  kernel: Measured, careful.
}

GOALS{
  observe          0.9
  stay_neutral     0.8
  do_right_thing   0.3   → grows with trust
}
"""


class TestEmotionalState:
    def test_baseline(self):
        ast = parse(INNKEEPER_NOTATION)
        state = compute_emotional_state(ast)
        assert state.fear == 0.0
        assert state.grief == 0.0

    def test_grief_from_loss_facts(self):
        grief_notation = INNKEEPER_NOTATION.replace("ca_guardian", "ca_faded_glory")
        ast = parse(grief_notation)
        facts = {"death_records", "collapse_aftermath", "killed_in_incident"}
        state = compute_emotional_state(ast, known_facts=facts)
        assert state.grief > 0.0

    def test_curiosity_from_sharing(self):
        grief_notation = INNKEEPER_NOTATION.replace("ca_guardian", "ca_faded_glory")
        ast = parse(grief_notation)
        state = compute_emotional_state(ast, emotional_inputs={"player_sharing": 0.8})
        assert state.curiosity > 0.0

    def test_fear_from_instability(self):
        ast = parse(OUTSIDER_NOTATION)
        state = compute_emotional_state(ast, emotional_inputs={"instability": 0.8})
        assert state.fear > 0.5

    def test_guardian_guilt_from_exposure(self):
        ast = parse(INNKEEPER_NOTATION)
        state = compute_emotional_state(ast, emotional_inputs={"exposure": 0.75})
        assert state.guilt > 0.5

    def test_unknown_archetype_returns_zero_state(self):
        ast = parse("@id x\n@arch unknown_arch\nVOICE{\n  kernel: Test.\n}\n")
        state = compute_emotional_state(ast, emotional_inputs={"instability": 1.0})
        assert state.fear == 0.0


class TestEmotionalInputsDict:
    def test_custom_input_propagates(self):
        ast = parse(INNKEEPER_NOTATION)
        state = compute_emotional_state(ast, emotional_inputs={"instability": 0.9})
        assert state.fear > 0.5

    def test_unknown_input_ignored(self):
        ast = parse(INNKEEPER_NOTATION)
        state = compute_emotional_state(ast, emotional_inputs={"nonexistent": 1.0})
        assert state.fear == 0.0

    def test_custom_loss_keywords(self):
        grief_notation = INNKEEPER_NOTATION.replace("ca_guardian", "ca_faded_glory")
        ast = parse(grief_notation)
        facts = {"locked_vault", "hidden_ledger"}
        state_default = compute_emotional_state(ast, known_facts=facts)
        state_custom = compute_emotional_state(
            ast, known_facts=facts, loss_keywords={"locked", "hidden"}
        )
        assert state_custom.grief >= state_default.grief

    def test_custom_archetype_sensitivities(self):
        ast = parse("@id x\n@arch custom_type\nVOICE{\n  kernel: Test.\n}\n")
        custom = {"custom_type": {"fear": ["danger"], "curiosity": ["mystery"]}}
        state = compute_emotional_state(
            ast,
            emotional_inputs={"danger": 0.8, "mystery": 0.6},
            archetype_sensitivities=custom,
        )
        assert state.fear > 0.7
        assert state.curiosity > 0.5


class TestEmotionalContext:
    def test_empty_below_threshold(self):
        from effigy.evolve import EmotionalState
        state = EmotionalState(fear=0.1, guilt=0.1)
        ctx = emotional_context(state, "Test")
        assert ctx == ""

    def test_includes_high_fear(self):
        from effigy.evolve import EmotionalState
        state = EmotionalState(fear=0.7)
        ctx = emotional_context(state, "Dael")
        assert "fight-or-flight" in ctx

    def test_includes_moderate_guilt(self):
        from effigy.evolve import EmotionalState
        state = EmotionalState(guilt=0.3)
        ctx = emotional_context(state, "Dael")
        assert "uneasy" in ctx

    def test_includes_header(self):
        from effigy.evolve import EmotionalState
        state = EmotionalState(curiosity=0.5)
        ctx = emotional_context(state, "Dael")
        assert "EMOTIONAL STATE" in ctx
        assert "Dael" in ctx


class TestIntentions:
    def test_no_goals_returns_empty(self):
        ast = parse("@id x\n")
        intentions = compute_intentions(ast)
        assert intentions == []

    def test_high_weight_goal_active(self):
        ast = parse(INNKEEPER_NOTATION)
        intentions = compute_intentions(ast, trust=0.0, state_vars={"ruin": 1})
        names = [i.goal_name for i in intentions]
        assert "keep_peace" in names

    def test_low_weight_goal_inactive(self):
        ast = parse(INNKEEPER_NOTATION)
        intentions = compute_intentions(ast, trust=0.0, state_vars={"ruin": 1})
        names = [i.goal_name for i in intentions]
        assert "help_newcomer" not in names

    def test_growing_goal_activates(self):
        ast = parse(INNKEEPER_NOTATION)
        intentions = compute_intentions(
            ast, trust=1.0, known_facts=set(), state_vars={"ruin": 1}
        )
        names = [i.goal_name for i in intentions]
        assert "help_newcomer" in names


class TestIntentionsContext:
    def test_empty_for_no_intentions(self):
        ctx = intentions_context([], "Test")
        assert ctx == ""

    def test_includes_header(self):
        from effigy.evolve import ActiveIntention
        intentions = [ActiveIntention("keep_peace", 0.9)]
        ctx = intentions_context(intentions, "Dael")
        assert "ACTIVE INTENTIONS" in ctx
        assert "Dael" in ctx


class TestMemorySynthesis:
    def test_should_not_synthesize_no_interactions(self):
        state = NpcMemoryState(char_id="test")
        assert should_synthesize(state, current_turn=20) is False

    def test_should_synthesize_after_interval(self):
        state = NpcMemoryState(
            char_id="test",
            last_synthesis_turn=5,
            interaction_count=3,
        )
        assert should_synthesize(state, current_turn=15) is True

    def test_should_not_synthesize_too_soon(self):
        state = NpcMemoryState(
            char_id="test",
            last_synthesis_turn=5,
            interaction_count=3,
        )
        assert should_synthesize(state, current_turn=10) is False

    def test_build_synthesis_prompt(self):
        ast = parse(INNKEEPER_NOTATION)
        state = NpcMemoryState(
            char_id="test_innkeeper",
            interaction_count=5,
            topics_discussed=["the market", "local merchants"],
        )
        system, user = build_synthesis_prompt(
            ast, state, trust=0.3,
            known_facts={"knows_her_name", "overheard_argument"},
            arc_phase_name="thawing",
            state_vars={"ruin": 2},
        )
        assert "Dael Renn" in user
        assert "thawing" in user
        assert "narrative state" in system.lower()

    def test_build_synthesis_prompt_custom_protagonist(self):
        ast = parse(INNKEEPER_NOTATION)
        state = NpcMemoryState(char_id="test", interaction_count=1)
        _, user = build_synthesis_prompt(
            ast, state, trust=0.3,
            protagonist_label="the detective",
            narrative_context="what they know about the case",
        )
        assert "the detective" in user
        assert "the case" in user


class TestBuildEvolutionContext:
    def test_empty_for_no_data(self):
        ast = parse("@id x\n@arch unknown\n")
        ctx = build_evolution_context(ast)
        assert ctx == ""

    def test_includes_emotional_state(self):
        ast = parse(INNKEEPER_NOTATION)
        ctx = build_evolution_context(ast, emotional_inputs={"instability": 0.8})
        assert "EMOTIONAL STATE" in ctx

    def test_includes_intentions(self):
        ast = parse(INNKEEPER_NOTATION)
        ctx = build_evolution_context(ast, trust=0.0, state_vars={"ruin": 1})
        assert "ACTIVE INTENTIONS" in ctx

    def test_includes_synthesis(self):
        ast = parse(INNKEEPER_NOTATION)
        ctx = build_evolution_context(
            ast,
            synthesis_text="Dael is becoming more cautious.",
        )
        assert "NPC STATE SUMMARY" in ctx
        assert "more cautious" in ctx
