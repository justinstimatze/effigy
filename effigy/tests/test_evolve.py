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


# ---------------------------------------------------------------------------
# v0.2: archetype-specific emotional prose override
# ---------------------------------------------------------------------------


class TestArchetypeEmotionalProse:
    def _state(self, **kwargs):
        from effigy.evolve import EmotionalState
        return EmotionalState(**kwargs)

    def test_override_replaces_default(self):
        state = self._state(fear=0.7)
        override = {"ca_guardian": {"fear_high": "control is slipping — the machinery isn't holding"}}
        ctx = emotional_context(state, "Test", archetype="ca_guardian", prose_override=override)
        assert "control is slipping" in ctx
        assert "fight-or-flight" not in ctx

    def test_override_low_vs_high_level(self):
        override = {"archA": {"fear_high": "HIGH_FEAR", "fear_low": "LOW_FEAR"}}
        ctx_high = emotional_context(self._state(fear=0.7), "T", archetype="archA", prose_override=override)
        ctx_low = emotional_context(self._state(fear=0.3), "T", archetype="archA", prose_override=override)
        assert "HIGH_FEAR" in ctx_high
        assert "LOW_FEAR" in ctx_low

    def test_fallback_to_default_when_axis_missing(self):
        # Override covers fear but not guilt — guilt falls through to default
        state = self._state(fear=0.7, guilt=0.7)
        override = {"archA": {"fear_high": "CUSTOM_FEAR"}}
        ctx = emotional_context(state, "T", archetype="archA", prose_override=override)
        assert "CUSTOM_FEAR" in ctx
        assert "weight of complicity" in ctx  # default guilt prose

    def test_fallback_when_archetype_missing(self):
        state = self._state(fear=0.7)
        override = {"other_arch": {"fear_high": "NOPE"}}
        ctx = emotional_context(state, "T", archetype="ca_guardian", prose_override=override)
        assert "NOPE" not in ctx
        assert "fight-or-flight" in ctx

    def test_no_archetype_uses_default(self):
        state = self._state(fear=0.7)
        ctx = emotional_context(state, "T")
        assert "fight-or-flight" in ctx


# ---------------------------------------------------------------------------
# v0.2: composite emotional states
# ---------------------------------------------------------------------------


class TestCompositeEmotionalStates:
    def _state(self, **kwargs):
        from effigy.evolve import EmotionalState
        return EmotionalState(**kwargs)

    def test_composite_fires_when_both_axes_active(self):
        state = self._state(fear=0.7, guilt=0.7)
        composites = {frozenset({"fear", "guilt"}): "cornered — wants to confess but afraid"}
        ctx = emotional_context(state, "T", composite_states=composites)
        assert "cornered" in ctx
        # Individual lines for fear and guilt should be suppressed
        assert "fight-or-flight" not in ctx
        assert "weight of complicity" not in ctx

    def test_composite_requires_all_axes_above_threshold(self):
        # fear is high, guilt is below threshold — composite should NOT fire
        state = self._state(fear=0.7, guilt=0.1)
        composites = {frozenset({"fear", "guilt"}): "cornered"}
        ctx = emotional_context(state, "T", composite_states=composites)
        assert "cornered" not in ctx
        assert "fight-or-flight" in ctx  # individual fear line appears

    def test_unconsumed_axes_still_render(self):
        state = self._state(fear=0.7, guilt=0.7, curiosity=0.5)
        composites = {frozenset({"fear", "guilt"}): "cornered"}
        ctx = emotional_context(state, "T", composite_states=composites)
        assert "cornered" in ctx
        assert "cautiously drawn in" in ctx  # curiosity low-level prose

    def test_larger_composite_wins_over_smaller(self):
        state = self._state(fear=0.7, guilt=0.7, grief=0.7)
        composites = {
            frozenset({"fear", "guilt"}): "TWO_AXIS",
            frozenset({"fear", "guilt", "grief"}): "THREE_AXIS",
        }
        ctx = emotional_context(state, "T", composite_states=composites)
        assert "THREE_AXIS" in ctx
        assert "TWO_AXIS" not in ctx  # overridden by larger composite

    def test_no_composites_falls_through(self):
        state = self._state(fear=0.7, guilt=0.7)
        ctx = emotional_context(state, "T")
        assert "fight-or-flight" in ctx
        assert "weight of complicity" in ctx


# ---------------------------------------------------------------------------
# v0.2: phase-gated emotional sensitivity modifiers
# ---------------------------------------------------------------------------


class TestPhaseModifiers:
    def test_modifier_suppresses_axis(self):
        ast = parse(INNKEEPER_NOTATION)
        modifiers = {"guarded": {"fear": 0.3}}
        state = compute_emotional_state(
            ast,
            emotional_inputs={"instability": 1.0},
            arc_phase_name="guarded",
            phase_modifiers=modifiers,
        )
        # ca_guardian fear comes from instability. Raw would be 1.0, suppressed to 0.3.
        assert state.fear == 0.3

    def test_modifier_no_match_unchanged(self):
        ast = parse(INNKEEPER_NOTATION)
        modifiers = {"guarded": {"fear": 0.3}}
        state = compute_emotional_state(
            ast,
            emotional_inputs={"instability": 1.0},
            arc_phase_name="resolved",  # not in modifiers
            phase_modifiers=modifiers,
        )
        assert state.fear == 1.0  # unmodified

    def test_modifier_amplifies_then_clamps(self):
        ast = parse(INNKEEPER_NOTATION)
        modifiers = {"resolved": {"fear": 2.5}}
        state = compute_emotional_state(
            ast,
            emotional_inputs={"instability": 0.5},
            arc_phase_name="resolved",
            phase_modifiers=modifiers,
        )
        # 0.5 * 2.5 = 1.25, clamped to 1.0
        assert state.fear == 1.0

    def test_no_phase_name_no_modifier(self):
        ast = parse(INNKEEPER_NOTATION)
        modifiers = {"guarded": {"fear": 0.3}}
        state = compute_emotional_state(
            ast,
            emotional_inputs={"instability": 1.0},
            phase_modifiers=modifiers,
            # arc_phase_name omitted
        )
        assert state.fear == 1.0


# ---------------------------------------------------------------------------
# v0.2: goal behaviors via BEHAVIORS block
# ---------------------------------------------------------------------------

BEHAVIOR_NOTATION = """
@id test_npc
@name Test NPC
@arch ca_guardian

VOICE{
  kernel: Test voice.
}

GOALS{
  keep_peace    0.9
  help_stranger 0.7
}

BEHAVIORS{
  keep_peace: Redirects heat with hospitality.
  help_stranger: Offers the best seat without being asked.
}
"""


class TestGoalBehaviors:
    def test_parse_behaviors_block(self):
        ast = parse(BEHAVIOR_NOTATION)
        assert ast.goal_behaviors["keep_peace"] == "Redirects heat with hospitality."
        assert "Offers the best seat" in ast.goal_behaviors["help_stranger"]

    def test_intention_description_populated(self):
        ast = parse(BEHAVIOR_NOTATION)
        intentions = compute_intentions(ast, trust=0.0)
        by_name = {i.goal_name: i for i in intentions}
        assert by_name["keep_peace"].description == "Redirects heat with hospitality."
        assert "best seat" in by_name["help_stranger"].description

    def test_intentions_context_renders_behavior(self):
        ast = parse(BEHAVIOR_NOTATION)
        intentions = compute_intentions(ast, trust=0.0)
        ctx = intentions_context(intentions, "Test")
        assert "keep_peace" in ctx
        assert "behavior: Redirects heat" in ctx

    def test_missing_behavior_omits_line(self):
        text = BEHAVIOR_NOTATION.replace(
            "BEHAVIORS{\n  keep_peace: Redirects heat with hospitality.\n  help_stranger: Offers the best seat without being asked.\n}",
            "",
        )
        ast = parse(text)
        intentions = compute_intentions(ast, trust=0.0)
        ctx = intentions_context(intentions, "Test")
        assert "keep_peace" in ctx
        assert "behavior:" not in ctx

    def test_empty_behaviors_block(self):
        text = BEHAVIOR_NOTATION.replace(
            "BEHAVIORS{\n  keep_peace: Redirects heat with hospitality.\n  help_stranger: Offers the best seat without being asked.\n}",
            "BEHAVIORS{\n}",
        )
        ast = parse(text)
        assert ast.goal_behaviors == {}

    def test_orphan_behavior_ignored(self):
        # BEHAVIORS entry references a goal not in GOALS — should be harmless.
        text = """
@id x
GOALS{
  keep_peace 0.9
}
BEHAVIORS{
  keep_peace: Deflects.
  nonexistent_goal: This should be ignored gracefully.
}
"""
        ast = parse(text)
        assert ast.goal_behaviors["nonexistent_goal"] == "This should be ignored gracefully."
        intentions = compute_intentions(ast, trust=0.0)
        names = [i.goal_name for i in intentions]
        assert "nonexistent_goal" not in names  # never becomes an intention
        assert "keep_peace" in names


# ---------------------------------------------------------------------------
# v0.2: end-to-end integration through build_evolution_context
# ---------------------------------------------------------------------------


class TestEvolutionContextV02Integration:
    """Verify build_evolution_context passes all v0.2 params through correctly.

    Passthrough regressions are exactly the kind of bug that slips through
    unit tests for individual functions. These tests lock in the contract.
    """

    def test_phase_modifier_propagates(self):
        ast = parse(INNKEEPER_NOTATION)
        # Without modifier: instability=1.0 produces fear=1.0 (deeply unsettled).
        # With 0.1 modifier: fear drops to 0.1, below threshold, no emotional line.
        ctx = build_evolution_context(
            ast, trust=0.0,
            emotional_inputs={"instability": 1.0},
            arc_phase_name="guarded",
            phase_modifiers={"guarded": {"fear": 0.1}},
        )
        assert "fight-or-flight" not in ctx
        assert "on edge" not in ctx

    def test_prose_override_propagates(self):
        ast = parse(INNKEEPER_NOTATION)
        override = {"ca_guardian": {"fear_high": "CONTROL_SLIPPING"}}
        ctx = build_evolution_context(
            ast, trust=0.0,
            emotional_inputs={"instability": 1.0},
            prose_override=override,
        )
        assert "CONTROL_SLIPPING" in ctx
        assert "fight-or-flight" not in ctx

    def test_composite_states_propagate(self):
        ast = parse(INNKEEPER_NOTATION)
        # ca_guardian: fear from instability, guilt from exposure
        composites = {frozenset({"fear", "guilt"}): "CORNERED_COMPOSITE"}
        ctx = build_evolution_context(
            ast, trust=0.0,
            emotional_inputs={"instability": 0.8, "exposure": 0.8},
            composite_states=composites,
        )
        assert "CORNERED_COMPOSITE" in ctx
        assert "fight-or-flight" not in ctx

    def test_all_v02_params_together(self):
        ast = parse(INNKEEPER_NOTATION)
        ctx = build_evolution_context(
            ast, trust=0.0,
            emotional_inputs={"instability": 1.0, "exposure": 1.0},
            arc_phase_name="resolved",
            phase_modifiers={"resolved": {"fear": 0.8, "guilt": 0.8}},
            prose_override={"ca_guardian": {"curiosity_high": "CUSTOM_CUR"}},
            composite_states={frozenset({"fear", "guilt"}): "CORNERED_ALL"},
        )
        assert "CORNERED_ALL" in ctx

    def test_goal_behaviors_render_in_context(self):
        ast = parse(BEHAVIOR_NOTATION)
        ctx = build_evolution_context(ast, trust=0.0)
        assert "keep_peace" in ctx
        assert "behavior: Redirects heat" in ctx


# ---------------------------------------------------------------------------
# v0.2: composite + prose_override interaction and tuple-key ergonomics
# ---------------------------------------------------------------------------


class TestCompositeAndOverrideInteraction:
    def _state(self, **kwargs):
        from effigy.evolve import EmotionalState
        return EmotionalState(**kwargs)

    def test_unconsumed_axis_respects_override(self):
        state = self._state(fear=0.7, guilt=0.7, curiosity=0.7)
        composites = {frozenset({"fear", "guilt"}): "cornered"}
        override = {"archA": {"curiosity_high": "CUSTOM_CUR"}}
        ctx = emotional_context(
            state, "T",
            archetype="archA",
            prose_override=override,
            composite_states=composites,
        )
        assert "cornered" in ctx
        assert "CUSTOM_CUR" in ctx
        # Default curiosity prose should NOT appear — override wins
        assert "leaning in" not in ctx

    def test_tuple_keys_accepted(self):
        state = self._state(fear=0.7, guilt=0.7)
        composites = {("fear", "guilt"): "TUPLE_COMPOSITE"}
        ctx = emotional_context(state, "T", composite_states=composites)
        assert "TUPLE_COMPOSITE" in ctx

    def test_list_keys_accepted(self):
        state = self._state(fear=0.7, guilt=0.7)
        composites = {tuple(["fear", "guilt"]): "LIST_COMPOSITE"}
        ctx = emotional_context(state, "T", composite_states=composites)
        assert "LIST_COMPOSITE" in ctx

    def test_unknown_axis_warns_and_skips(self, caplog):
        import logging
        state = self._state(fear=0.7)
        composites = {frozenset({"fear", "nonexistent"}): "NEVER_FIRES"}
        with caplog.at_level(logging.WARNING, logger="effigy.evolve"):
            ctx = emotional_context(state, "T", composite_states=composites)
        assert "NEVER_FIRES" not in ctx
        # Individual fear line still renders
        assert "fight-or-flight" in ctx
        # Warning was logged
        assert any("nonexistent" in rec.message for rec in caplog.records)


class TestPhaseModifiersMultipleAxes:
    def test_modifier_affects_multiple_axes_independently(self):
        ast = parse(INNKEEPER_NOTATION)
        state = compute_emotional_state(
            ast,
            emotional_inputs={"instability": 1.0, "exposure": 1.0},
            arc_phase_name="guarded",
            phase_modifiers={"guarded": {"fear": 0.3, "guilt": 0.5}},
        )
        # ca_guardian: fear from instability+exposure, guilt from exposure+evidence
        # instability=1.0, exposure=1.0 → raw fear=1.0, raw guilt=1.0
        # Modified: fear=0.3, guilt=0.5
        assert state.fear == 0.3
        assert state.guilt == 0.5
