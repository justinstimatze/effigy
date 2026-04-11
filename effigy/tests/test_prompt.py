"""Tests for effigy Layer 2 — prompt context generation."""

from pathlib import Path

import pytest

from effigy.parser import parse
from effigy.prompt import (
    build_dialogue_context,
    build_dynamic_state,
    build_static_context,
    get_arc_phase_dict,
    resolve_active_goals,
    resolve_arc_phase,
    select_mes_examples,
)

ARC_NOTATION = """
@id test_npc
@name Test NPC

VOICE{
  kernel: Brisk, warm, no-nonsense.
}

ARC{
  guarded → trust>=0.0
    voice: "Polite distance."
  thawing → trust>=0.2 AND fact:knows_her_name
    voice: "Pauses longer."
  vulnerable → trust>=0.4 AND fact:overheard_argument
    voice: "Quiet. Sentences fragment."
  resolved → trust>=0.6 AND ruin>=4
    voice: "Clear. Direct."
}

GOALS{
  keep_peace       0.9
  help_newcomer    0.3   → grows with trust
  tell_truth       0.2   → grows with evidence
}

MES[
{{char}}: Example 1.
---
{{char}}: Example 2.
---
{{char}}: Example 3.
---
{{char}}: Example 4.
---
{{char}}: Example 5.
---
{{char}}: Example 6.
]
"""


class TestArcPhaseResolution:
    def setup_method(self):
        self.ast = parse(ARC_NOTATION)

    def test_base_phase_no_trust(self):
        phase = resolve_arc_phase(self.ast, trust=0.0, known_facts=set(), state_vars={"ruin": 1})
        assert phase is not None
        assert phase.name == "guarded"

    def test_thawing_requires_fact(self):
        phase = resolve_arc_phase(self.ast, trust=0.5, known_facts=set(), state_vars={"ruin": 1})
        assert phase.name == "guarded"

    def test_thawing_with_fact(self):
        phase = resolve_arc_phase(
            self.ast, trust=0.3, known_facts={"knows_her_name"}, state_vars={"ruin": 1}
        )
        assert phase.name == "thawing"

    def test_vulnerable_phase(self):
        phase = resolve_arc_phase(
            self.ast,
            trust=0.5,
            known_facts={"knows_her_name", "overheard_argument"},
            state_vars={"ruin": 1},
        )
        assert phase.name == "vulnerable"

    def test_resolved_requires_ruin(self):
        phase = resolve_arc_phase(
            self.ast,
            trust=0.7,
            known_facts={"knows_her_name", "overheard_argument"},
            state_vars={"ruin": 2},
        )
        assert phase.name == "vulnerable"

    def test_resolved_phase(self):
        phase = resolve_arc_phase(
            self.ast,
            trust=0.7,
            known_facts={"knows_her_name", "overheard_argument"},
            state_vars={"ruin": 5},
        )
        assert phase.name == "resolved"

    def test_no_arc_phases(self):
        ast = parse("@id x\n")
        phase = resolve_arc_phase(ast, 0.5, known_facts=set())
        assert phase is None


class TestGoalResolution:
    def setup_method(self):
        self.ast = parse(ARC_NOTATION)

    def test_static_goal(self):
        goals = resolve_active_goals(self.ast, trust=0.0, known_facts=set(), state_vars={"ruin": 1})
        keep = next(g for g in goals if g["name"] == "keep_peace")
        assert keep["weight"] == 0.9
        assert keep["active"] is True

    def test_growing_goal_low_trust(self):
        goals = resolve_active_goals(self.ast, trust=0.0, known_facts=set(), state_vars={"ruin": 1})
        help_goal = next(g for g in goals if g["name"] == "help_newcomer")
        assert help_goal["weight"] < 0.6
        assert help_goal["active"] is False

    def test_growing_goal_high_trust(self):
        goals = resolve_active_goals(self.ast, trust=0.8, known_facts=set(), state_vars={"ruin": 1})
        help_goal = next(g for g in goals if g["name"] == "help_newcomer")
        assert help_goal["weight"] > 0.3  # grew with trust
        assert help_goal["active"] is True or help_goal["weight"] >= 0.54

    def test_evidence_growth(self):
        many_facts = {f"fact_{i}" for i in range(20)}
        goals = resolve_active_goals(self.ast, trust=0.0, known_facts=many_facts, state_vars={"ruin": 1})
        truth_goal = next(g for g in goals if g["name"] == "tell_truth")
        assert truth_goal["weight"] > 0.2  # grew with evidence

    def test_sorted_by_weight(self):
        goals = resolve_active_goals(self.ast, trust=0.0, known_facts=set(), state_vars={"ruin": 1})
        weights = [g["weight"] for g in goals]
        assert weights == sorted(weights, reverse=True)


class TestMesExampleRotation:
    def setup_method(self):
        self.ast = parse(ARC_NOTATION)

    def test_max_examples_limits(self):
        selected = select_mes_examples(self.ast, turn=0, max_examples=3)
        assert len(selected) == 3

    def test_first_example_always_included(self):
        for turn in range(10):
            selected = select_mes_examples(self.ast, turn=turn, max_examples=3)
            assert selected[0] == "{{char}}: Example 1."

    def test_rotation_changes_selection(self):
        sel_0 = select_mes_examples(self.ast, turn=0, max_examples=3)
        sel_1 = select_mes_examples(self.ast, turn=1, max_examples=3)
        assert sel_0 != sel_1 or len(self.ast.mes_examples) <= 3

    def test_all_returned_when_few(self):
        ast = parse("@id x\nMES[\n{{char}}: One.\n---\n{{char}}: Two.\n]\n")
        selected = select_mes_examples(ast, turn=0, max_examples=4)
        assert len(selected) == 2


class TestBuildDialogueContext:
    def setup_method(self):
        self.ast = parse(ARC_NOTATION)

    def test_empty_for_no_data(self):
        ast = parse("@id x\n")
        ctx = build_dialogue_context(ast)
        assert ctx == ""

    def test_includes_arc_phase(self):
        ctx = build_dialogue_context(self.ast, trust=0.0, state_vars={"ruin": 1})
        assert '<arc_phase name="guarded">' in ctx

    def test_includes_active_goals(self):
        ctx = build_dialogue_context(self.ast, trust=0.5, state_vars={"ruin": 1})
        assert "keep_peace" in ctx

    def test_includes_voice_reinforcement(self):
        ctx = build_dialogue_context(self.ast)
        assert "Brisk, warm" in ctx


class TestGetArcPhaseDict:
    def setup_method(self):
        self.ast = parse(ARC_NOTATION)

    def test_returns_dict(self):
        result = get_arc_phase_dict(self.ast, trust=0.0, state_vars={"ruin": 1})
        assert isinstance(result, dict)
        assert result["name"] == "guarded"
        assert "voice" in result

    def test_returns_none_for_no_phases(self):
        ast = parse("@id x\n")
        result = get_arc_phase_dict(ast)
        assert result is None


# ---------------------------------------------------------------------------
# v0.2 dossier fields: traits, never, quirks, theme
# ---------------------------------------------------------------------------

V02_NOTATION = """
@id test_v02
@name Test V02
@theme The cost of loyalty to the dead

VOICE{
  kernel: Brisk, warm, no-nonsense.
}

TRAITS[
  brisk, warm, grief-compressed-into-routine, hospitality-as-control
]

NEVER[
  Never uses analytical language
  ---
  Never justifies keeping secrets
]

QUIRKS[
  Wipes the counter when uncomfortable
  ---
  Refills coffee without asking
]

ARC{
  guarded → trust>=0.0
    voice: "Busy deflections."
}

GOALS{
  keep_peace   0.9
}
"""


class TestV02ContextSections:
    def setup_method(self):
        self.ast = parse(V02_NOTATION)
        self.ctx = build_dialogue_context(self.ast)

    def test_includes_traits(self):
        assert "<traits>" in self.ctx
        assert "brisk" in self.ctx
        assert "hospitality-as-control" in self.ctx

    def test_includes_never(self):
        assert "<never>" in self.ctx
        assert "analytical language" in self.ctx
        assert "keeping secrets" in self.ctx

    def test_includes_quirks(self):
        assert "<quirks>" in self.ctx
        assert "counter" in self.ctx
        assert "coffee" in self.ctx

    def test_theme_excluded_from_context(self):
        assert "THEMATIC ROLE" not in self.ctx
        assert "<theme>" not in self.ctx

    def test_section_ordering(self):
        """Static prefix (voice → never → quirks → ... → traits) precedes dynamic (arc → goals → voice_reminder)."""
        voice_pos = self.ctx.index("<voice>")
        never_pos = self.ctx.index("<never>")
        quirk_pos = self.ctx.index("<quirks>")
        trait_pos = self.ctx.index("<traits>")
        arc_pos = self.ctx.index("<arc_phase")
        goal_pos = self.ctx.index("<active_goals>")
        reminder_pos = self.ctx.index("<voice_reminder>")
        assert voice_pos < never_pos < quirk_pos < trait_pos < arc_pos < goal_pos < reminder_pos

    def test_voice_reminder_is_last(self):
        """voice_reminder sandwich: must be the final section for attention."""
        reminder_pos = self.ctx.index("<voice_reminder>")
        # Nothing else should come after voice_reminder's opening tag except its own close.
        tail = self.ctx[reminder_pos:]
        assert tail.count("\n\n") == 0  # no further sections


# ---------------------------------------------------------------------------
# Generic state_vars tests (bug fix validation)
# ---------------------------------------------------------------------------


class TestGenericStateVars:
    def test_custom_numeric_condition(self):
        """Verify non-ruin numeric conditions work."""
        text = '@id x\nARC{\n  base → trust>=0.0\n    voice: "Base."\n  escalated → corruption>=3\n    voice: "Escalated."\n}\n'
        ast = parse(text)
        phase = resolve_arc_phase(ast, trust=0.5, known_facts=set(), state_vars={"corruption": 1})
        assert phase.name == "base"
        phase = resolve_arc_phase(ast, trust=0.5, known_facts=set(), state_vars={"corruption": 4})
        assert phase.name == "escalated"

    def test_missing_state_var_defaults_to_zero(self):
        text = '@id x\nARC{\n  base → trust>=0.0\n    voice: "Base."\n  gated → tension>=5\n    voice: "Tense."\n}\n'
        ast = parse(text)
        phase = resolve_arc_phase(ast, trust=0.5, known_facts=set(), state_vars={})
        assert phase.name == "base"

    def test_multiple_state_vars(self):
        text = '@id x\nARC{\n  base → trust>=0.0\n    voice: "Base."\n  hot → tension>=3 AND corruption>=2\n    voice: "Hot."\n}\n'
        ast = parse(text)
        phase = resolve_arc_phase(ast, trust=0.5, known_facts=set(), state_vars={"tension": 4, "corruption": 1})
        assert phase.name == "base"  # corruption too low
        phase = resolve_arc_phase(ast, trust=0.5, known_facts=set(), state_vars={"tension": 4, "corruption": 3})
        assert phase.name == "hot"


class TestKeywordOnlyEnforcement:
    def test_known_facts_cannot_be_positional(self):
        ast = parse("@id x\n")
        with pytest.raises(TypeError):
            resolve_arc_phase(ast, 0.0, set(), {"ruin": 1})

    def test_state_vars_cannot_be_positional(self):
        ast = parse("@id x\n")
        with pytest.raises(TypeError):
            resolve_active_goals(ast, 0.0, set())


# ---------------------------------------------------------------------------
# Integration: real .effigy files
# ---------------------------------------------------------------------------

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


@pytest.mark.parametrize(
    "filename",
    [
        "test_npc.effigy",
    ],
)
class TestRealFileContext:
    """Test prompt context generation on real .effigy files."""

    def _load(self, filename):
        path = _find_effigy(filename)
        if path is None:
            pytest.skip(f"{filename} not found")
        return parse(path.read_text())

    def test_base_phase_exists(self, filename):
        ast = self._load(filename)
        phase = resolve_arc_phase(ast, trust=0.0, known_facts=set(), state_vars={"ruin": 1})
        assert phase is not None, "Should have a base arc phase"

    def test_context_nonempty(self, filename):
        ast = self._load(filename)
        ctx = build_dialogue_context(ast)
        assert len(ctx) > 100, "Context should be substantial"

    def test_context_has_never(self, filename):
        ast = self._load(filename)
        ctx = build_dialogue_context(ast)
        assert "<never>" in ctx

    def test_context_has_traits(self, filename):
        ast = self._load(filename)
        ctx = build_dialogue_context(ast)
        assert "<traits>" in ctx

    def test_context_has_quirks(self, filename):
        ast = self._load(filename)
        ctx = build_dialogue_context(ast)
        assert "<quirks>" in ctx

    def test_context_excludes_theme(self, filename):
        ast = self._load(filename)
        ctx = build_dialogue_context(ast)
        assert "THEMATIC ROLE" not in ctx

    def test_arc_phase_dict(self, filename):
        ast = self._load(filename)
        result = get_arc_phase_dict(ast, trust=0.0, state_vars={"ruin": 1})
        assert result is not None
        assert "name" in result
        assert "voice" in result


WRONG_NOTATION = """
@id test_wrong
@name Test Wrong

VOICE{
  kernel: Brisk and warm.
}

NEVER[
  Never uses academic language
]

WRONG[
  WRONG: "The data suggests a correlation between the variables."
  RIGHT: "Something's off with those numbers."
  WHY: Academic register breaks character voice.
  ---
  WRONG: "I've been documenting the anomalies in my field notes."
  RIGHT: "I wrote some stuff down."
  WHY: Too formal for this character.
]
"""


class TestWrongExclusion:
    def setup_method(self):
        self.ast = parse(WRONG_NOTATION)

    def test_wrong_not_in_dialogue_context(self):
        ctx = build_dialogue_context(self.ast)
        assert "DO NOT generate" not in ctx
        assert "<wrong>" not in ctx
        assert "data suggests" not in ctx
        assert "field notes" not in ctx

    def test_never_still_present(self):
        ctx = build_dialogue_context(self.ast)
        assert "<never>" in ctx
        assert "academic language" in ctx

    def test_voice_still_present(self):
        ctx = build_dialogue_context(self.ast)
        assert "Brisk and warm" in ctx

    def test_wrong_accessible_via_getter(self):
        from effigy.prompt import get_wrong_examples

        examples = get_wrong_examples(self.ast)
        assert len(examples) == 2
        assert "data suggests" in examples[0]["wrong"]
        assert examples[0]["right"] == "Something's off with those numbers."
        assert examples[0]["why"] == "Academic register breaks character voice."


PRIORITY_NOTATION = """
@id test_priority
@name Test Priority

NEVER[
  Regular rule one
  ---
  Regular rule two
  ---
  Regular rule three
  ---
  Regular rule four
  ---
  Regular rule five
  ---
  Regular rule six
  ---
  CRITICAL: Must always do X
  ---
  CRITICAL: Must never do Y
  ---
  Regular rule seven
  ---
  Regular rule eight
]
"""


class TestNeverPriority:
    def setup_method(self):
        self.ast = parse(PRIORITY_NOTATION)

    def test_never_capped_at_seven(self):
        ctx = build_dialogue_context(self.ast)
        never_count = ctx.count("  - ")
        assert never_count == 7

    def test_critical_rules_first(self):
        ctx = build_dialogue_context(self.ast)
        critical_x = ctx.index("Must always do X")
        critical_y = ctx.index("Must never do Y")
        first_regular = ctx.index("Regular rule one")
        assert critical_x < first_regular
        assert critical_y < first_regular

    def test_all_rules_still_on_ast(self):
        """AST preserves all rules -- cap is output-only."""
        assert len(self.ast.never_would_say) == 10


class TestStaticDynamicSplit:
    """Phase 1: build_static_context / build_dynamic_state contracts."""

    def setup_method(self):
        self.ast = parse(V02_NOTATION)

    def test_static_is_byte_stable_across_state(self):
        """Static context must not change with turn, trust, or state_vars."""
        s_a = build_static_context(self.ast)
        s_b = build_static_context(self.ast)
        assert s_a == s_b
        assert s_a  # non-empty

    def test_static_contains_only_static_sections(self):
        ctx = build_static_context(self.ast)
        assert "<voice>" in ctx
        assert "<never>" in ctx
        assert "<quirks>" in ctx
        assert "<traits>" in ctx
        assert "<arc_phase" not in ctx
        assert "<active_goals>" not in ctx
        assert "<voice_reminder>" not in ctx

    def test_dynamic_contains_only_dynamic_sections(self):
        ctx = build_dynamic_state(self.ast, trust=0.5, state_vars={"ruin": 1})
        assert "<arc_phase" in ctx
        assert "<voice_reminder>" in ctx
        assert "<kernel>" not in ctx  # static voice block is NOT here
        assert "<never>" not in ctx

    def test_dynamic_changes_with_trust(self):
        """Different trust levels should yield different dynamic context."""
        arc_ast = parse(ARC_NOTATION)
        low = build_dynamic_state(arc_ast, trust=0.0)
        high = build_dynamic_state(
            arc_ast, trust=0.5, known_facts={"knows_her_name", "overheard_argument"}
        )
        assert low != high
        assert 'name="guarded"' in low
        assert 'name="vulnerable"' in high

    def test_dialogue_context_is_static_plus_dynamic(self):
        """build_dialogue_context must equal static + '\\n\\n' + dynamic."""
        static = build_static_context(self.ast)
        dynamic = build_dynamic_state(self.ast, trust=0.5, state_vars={"ruin": 1})
        combined = build_dialogue_context(self.ast, trust=0.5, state_vars={"ruin": 1})
        assert combined == "\n\n".join(p for p in (static, dynamic) if p)

    def test_static_empty_ast_returns_empty(self):
        empty_ast = parse("@id empty\n")
        assert build_static_context(empty_ast) == ""

    def test_dynamic_empty_ast_returns_empty(self):
        empty_ast = parse("@id empty\n")
        assert build_dynamic_state(empty_ast) == ""
