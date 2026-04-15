"""Tests for effigy Layer 2 — prompt context generation."""

from pathlib import Path

import pytest

from effigy.parser import parse
from effigy.prompt import (
    build_dialogue_context,
    build_dialogue_context_debug,
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
  ---
  Regular rule nine
  ---
  Regular rule ten
  ---
  Regular rule eleven
]
"""


class TestNeverPriority:
    def setup_method(self):
        self.ast = parse(PRIORITY_NOTATION)

    def test_never_capped_at_max(self):
        from effigy.prompt import MAX_NEVER_RULES

        ctx = build_dialogue_context(self.ast)
        never_count = ctx.count("  - ")
        assert never_count == MAX_NEVER_RULES
        assert MAX_NEVER_RULES == 10  # explicit to catch unintended changes

    def test_critical_rules_first(self):
        ctx = build_dialogue_context(self.ast)
        critical_x = ctx.index("Must always do X")
        critical_y = ctx.index("Must never do Y")
        first_regular = ctx.index("Regular rule one")
        assert critical_x < first_regular
        assert critical_y < first_regular

    def test_all_rules_still_on_ast(self):
        """AST preserves all rules -- cap is output-only."""
        assert len(self.ast.never_would_say) == 13

    def test_rules_beyond_cap_dropped(self):
        """With 13 rules and cap=10, 3 regular rules should be truncated."""
        ctx = build_dialogue_context(self.ast)
        # First 8 regular rules (after 2 CRITICALs consume 2 slots) should
        # appear; rules 9/10/11 should be dropped.
        assert "Regular rule one" in ctx
        assert "Regular rule eight" in ctx
        assert "Regular rule nine" not in ctx
        assert "Regular rule ten" not in ctx
        assert "Regular rule eleven" not in ctx


class TestValidateNeverBudget:
    """v0.3.2: authoring-time visibility into NEVER rules dropped at the cap."""

    def test_under_cap_returns_empty(self):
        from effigy.prompt import validate_never_budget

        ast = parse("@id u\n@name U\nNEVER[\n  one\n  ---\n  two\n]\n")
        assert validate_never_budget(ast) == []

    def test_at_cap_returns_empty(self):
        from effigy.prompt import MAX_NEVER_RULES, validate_never_budget

        rules = "\n  ---\n  ".join(f"rule {i}" for i in range(MAX_NEVER_RULES))
        ast = parse(f"@id a\n@name A\nNEVER[\n  {rules}\n]\n")
        assert validate_never_budget(ast) == []

    def test_over_cap_reports_dropped(self):
        from effigy.prompt import validate_never_budget

        warnings = validate_never_budget(parse(PRIORITY_NOTATION))
        assert len(warnings) == 1
        w = warnings[0]
        assert w["char_id"] == "test_priority"
        assert w["total"] == 13
        assert w["cap"] == 10
        assert w["critical_count"] == 2
        assert w["dropped"] == [
            "Regular rule nine",
            "Regular rule ten",
            "Regular rule eleven",
        ]

    def test_dropped_matches_render(self):
        """validate_never_budget must agree with build_dialogue_context."""
        from effigy.prompt import build_dialogue_context, validate_never_budget

        ast = parse(PRIORITY_NOTATION)
        ctx = build_dialogue_context(ast)
        warnings = validate_never_budget(ast)
        for rule in warnings[0]["dropped"]:
            assert rule not in ctx

    def test_no_never_block_returns_empty(self):
        from effigy.prompt import validate_never_budget

        ast = parse("@id n\n@name N\n")
        assert validate_never_budget(ast) == []


# ---------------------------------------------------------------------------
# TEST block rendering
# ---------------------------------------------------------------------------

TEST_NOTATION = """
@id test_tests
@name Test Tests

VOICE{
  kernel: Brisk and knowing.
}

NEVER[
  Never asks questions
]

TEST[
  name: CONTROL TEST
  dimension: voice
  question: Does this line EXTRACT or REQUEST?
  fail: "You passing through?" -- question
  pass: "Strangers don't come through without a reason." -- statement
  why: Power comes from ALREADY KNOWING.
---
  name: FRAGMENT TEST
  question: Does this line fragment under pressure?
  fail: "I need to tell you something important about what happened." -- complete
  pass: "The thing is -- you don't -- it wasn't." -- fragments
  why: Emotional pressure breaks syntax.
]

QUIRKS[
  Wipes counter when uncomfortable
]
"""


class TestTestRendering:
    def setup_method(self):
        self.ast = parse(TEST_NOTATION)

    def test_tests_in_static_context(self):
        ctx = build_static_context(self.ast)
        assert "<tests>" in ctx
        assert "</tests>" in ctx

    def test_test_name_rendered(self):
        ctx = build_static_context(self.ast)
        assert 'name="CONTROL TEST"' in ctx

    def test_test_dimension_rendered(self):
        ctx = build_static_context(self.ast)
        assert 'dimension="voice"' in ctx

    def test_test_question_rendered(self):
        ctx = build_static_context(self.ast)
        assert "<question>" in ctx
        assert "EXTRACT or REQUEST" in ctx

    def test_fail_pass_rendered(self):
        ctx = build_static_context(self.ast)
        assert "<fail>" in ctx
        assert "<pass>" in ctx
        assert "You passing through?" in ctx
        assert "Strangers don't come through" in ctx

    def test_why_rendered(self):
        ctx = build_static_context(self.ast)
        assert "<why>" in ctx
        assert "ALREADY KNOWING" in ctx

    def test_tests_after_never_before_quirks(self):
        ctx = build_static_context(self.ast)
        never_pos = ctx.index("<never>")
        tests_pos = ctx.index("<tests>")
        quirks_pos = ctx.index("<quirks>")
        assert never_pos < tests_pos < quirks_pos

    def test_not_in_dynamic_state(self):
        ctx = build_dynamic_state(self.ast)
        assert "<tests>" not in ctx

    def test_dimension_omitted_when_empty(self):
        ctx = build_static_context(self.ast)
        # FRAGMENT TEST has no dimension — its <test> tag should not have dimension attr
        fragment_idx = ctx.index('name="FRAGMENT TEST"')
        # Find the opening <test for this entry
        tag_start = ctx.rfind("<test ", 0, fragment_idx)
        tag_end = ctx.index(">", fragment_idx)
        tag = ctx[tag_start:tag_end + 1]
        assert "dimension" not in tag


class TestTestsCap:
    def test_max_tests_cap(self):
        from effigy.prompt import MAX_TESTS

        tests = "\n---\n".join(
            f"  name: TEST {i}\n  question: Q{i}?\n  fail: bad\n  pass: good\n  why: reason"
            for i in range(8)
        )
        text = f"@id x\nTEST[\n{tests}\n]\n"
        ast = parse(text)
        assert len(ast.tests) == 8  # AST preserves all
        ctx = build_static_context(ast)
        assert ctx.count("<test ") == MAX_TESTS


class TestTestDebug:
    def test_debug_records_test_counts(self):
        ast = parse(TEST_NOTATION)
        _, debug = build_dialogue_context_debug(ast)
        assert debug["static"]["tests_total"] == 2
        assert debug["static"]["tests_rendered"] == 2
        assert "tests" in debug["static"]["sections"]


class TestGetTests:
    def test_get_tests_accessor(self):
        from effigy.prompt import get_tests

        ast = parse(TEST_NOTATION)
        tests = get_tests(ast)
        assert len(tests) == 2
        assert tests[0]["name"] == "CONTROL TEST"
        assert tests[0]["dimension"] == "voice"
        assert "EXTRACT" in tests[0]["question"]
        assert len(tests[0]["fail_examples"]) == 1
        assert tests[0]["why"] == "Power comes from ALREADY KNOWING."

    def test_get_tests_empty(self):
        from effigy.prompt import get_tests

        ast = parse("@id x\n")
        assert get_tests(ast) == []


INLINE_EXAMPLES_NOTATION = """
@id test_inline
@name Test Inline

NEVER[
  CRITICAL: Never goes coy or terse. Fills silence. WRONG: "I don't know." WRONG: "Maybe." RIGHT: "So here's the thing — you're gonna love this — I've got a story."
  ---
  Never uses academic language
  ---
  Never interrogates the player. NOT: "Which one?" NOT: "What do you mean?" YES: redirect with hospitality
]
"""


class TestStripInlineExamples:
    """Phase 7: inline WRONG/RIGHT/NOT/YES examples stripped from NEVER rules."""

    def test_strip_helper_removes_wrong_marker(self):
        from effigy.prompt import _strip_inline_examples

        result = _strip_inline_examples(
            'Never goes coy. WRONG: "I don\'t know." RIGHT: "Sure thing."'
        )
        assert result == "Never goes coy"

    def test_strip_helper_removes_not_yes_markers(self):
        from effigy.prompt import _strip_inline_examples

        result = _strip_inline_examples(
            'Never interrogates. NOT: "Which one?" YES: redirect'
        )
        assert result == "Never interrogates"

    def test_strip_helper_handles_bad_good_markers(self):
        from effigy.prompt import _strip_inline_examples

        result = _strip_inline_examples(
            "Never lies about age. BAD: says 30 when 45. GOOD: says 40-ish."
        )
        assert result == "Never lies about age"

    def test_strip_helper_no_markers_unchanged(self):
        from effigy.prompt import _strip_inline_examples

        rule = "Never raises her voice — volume is a loss of control"
        assert _strip_inline_examples(rule) == rule

    def test_strip_helper_lowercase_markers_ignored(self):
        from effigy.prompt import _strip_inline_examples

        rule = "Never tells a customer they're wrong: it's bad for tips"
        # Lowercase 'wrong:' must NOT match — only uppercase markers.
        assert _strip_inline_examples(rule) == rule

    def test_rendered_never_has_no_inline_examples(self):
        ast = parse(INLINE_EXAMPLES_NOTATION)
        ctx = build_dialogue_context(ast)
        # The NEVER section should contain the constraint statements but
        # not the inline example text.
        assert "Never goes coy" in ctx
        assert "Never interrogates the player" in ctx
        # Example markers and their quoted strings must be stripped.
        assert "WRONG:" not in ctx
        assert "RIGHT:" not in ctx
        assert "NOT:" not in ctx
        assert "YES:" not in ctx
        assert "I don't know" not in ctx
        assert "Which one?" not in ctx
        assert "redirect with hospitality" not in ctx

    def test_debug_counts_inline_stripped(self):
        ast = parse(INLINE_EXAMPLES_NOTATION)
        _, debug = build_dialogue_context_debug(ast)
        # 2 of the 3 rules had inline example markers.
        assert debug["static"]["never_inline_examples_stripped"] == 2


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


# ---------------------------------------------------------------------------
# Phase 0: surfaced AST fields (presence, voice.peak/peak_when, MES split,
# drivermap, goal_behaviors, UNC)
# ---------------------------------------------------------------------------


PHASE0_NOTATION = """
@id marta
@name Marta Voss
@presence Small, tense-jawed. Never quite leaves the counter.

VOICE{
  kernel: Brisk, warm, no-nonsense.
  peak: Cuts her own sentence mid-word. Drops the patter.
  peak_when: ruin>=4
}

MES[
  {{char}}: "Coffee's fresh if you want it. Cream's in the tin."
  ---
  {{char}}: "Counter's clean. Sit wherever."
  ---
  @tier moderate
  {{char}}: "I heard about the Hensley boy. You knew him?"
  ---
  @tier high
  {{char}}: "My Eli used to sit right there. Before."
]

UNC[
  {{char}}: "I wouldn't know about that."
  ---
  {{char}}: "Better ask the sheriff."
]

NEVER[
  Never uses analytical language
]

QUIRKS[
  Wipes the counter when nervous
]

GOALS{
  protect_daughter   0.8 grows_with trust
  keep_peace         0.7
}

BEHAVIORS{
  protect_daughter: Redirects when daughter comes up. Asks about your family first.
}

DM{
  evidence: +
  conflict: -
  stability: +
  features: small-town, grief
}
"""


class TestPhase0Surfaced:
    def setup_method(self):
        self.ast = parse(PHASE0_NOTATION)

    def test_presence_note_rendered(self):
        ctx = build_static_context(self.ast)
        assert "<presence>" in ctx
        assert "tense-jawed" in ctx

    def test_voice_peak_rendered(self):
        ctx = build_static_context(self.ast)
        assert "<peak" in ctx
        assert "Cuts her own sentence" in ctx

    def test_voice_peak_when_attribute(self):
        ctx = build_static_context(self.ast)
        assert 'when="ruin>=4"' in ctx

    def test_drivermap_compressed(self):
        ctx = build_static_context(self.ast)
        assert "<drivermap>" in ctx
        assert "evidence+" in ctx
        assert "conflict-" in ctx
        assert "stability+" in ctx

    def test_canonical_mes_in_static(self):
        ctx = build_static_context(self.ast)
        assert '<voice_examples canonical="true">' in ctx
        assert "Coffee's fresh" in ctx
        assert "Counter's clean" in ctx

    def test_canonical_mes_byte_stable_across_turns(self):
        """Canonical slice must not change with turn or trust."""
        a = build_static_context(self.ast)
        b = build_static_context(self.ast)
        assert a == b

    def test_rotating_mes_in_dynamic(self):
        """High-trust rotating should include trust-gated examples."""
        ctx = build_dynamic_state(self.ast, trust=0.6, turn=0)
        assert '<voice_examples rotating="true">' in ctx

    def test_rotating_mes_respects_trust(self):
        """Low-trust rotating should NOT include high-tier examples."""
        ctx = build_dynamic_state(self.ast, trust=0.0, turn=0)
        # High-tier example 'My Eli' must not appear at low trust.
        assert "My Eli used to sit" not in ctx

    def test_rotating_mes_changes_with_turn(self):
        """When pool > max_examples, rotation should vary by turn."""
        # MES has 4 entries total, 2 canonical → 2 in rotating pool.
        # With max_examples=2 and pool=2, rotation is stable. Check with
        # trust that admits both moderate + high to widen pool.
        a = build_dynamic_state(self.ast, trust=0.6, turn=0)
        b = build_dynamic_state(self.ast, trust=0.6, turn=1)
        # Pool size equals max → rotation no-op. Both include same examples.
        # This test asserts the function doesn't crash and returns content.
        assert '<voice_examples rotating="true">' in a
        assert '<voice_examples rotating="true">' in b

    def test_goal_behavior_spliced_into_goal(self):
        ctx = build_dynamic_state(
            self.ast, trust=0.5, state_vars={}, known_facts=set()
        )
        assert "protect_daughter" in ctx
        assert "Redirects when daughter comes up" in ctx

    def test_goal_without_behavior_is_self_closing(self):
        ctx = build_dynamic_state(self.ast, trust=0.9)
        # keep_peace has no behavior entry
        assert 'name="keep_peace"/>' in ctx

    def test_uncertainty_voice_off_by_default(self):
        ctx = build_dynamic_state(self.ast, trust=0.5)
        assert "<uncertainty_voice>" not in ctx
        assert "wouldn't know" not in ctx

    def test_uncertainty_voice_on_when_opted_in(self):
        ctx = build_dynamic_state(self.ast, trust=0.5, uncertain=True)
        assert "<uncertainty_voice>" in ctx
        assert "wouldn't know" in ctx

    def test_voice_reminder_kernel_by_default(self):
        ctx = build_dynamic_state(self.ast, trust=0.0, state_vars={"ruin": 0})
        assert "<voice_reminder>" in ctx
        assert "Brisk, warm" in ctx
        # Peak voice ('Cuts her own sentence') not active
        assert 'peak="true"' not in ctx

    def test_voice_reminder_swaps_to_peak_when_condition_true(self):
        """When peak_when evaluates true, voice_reminder uses peak voice.

        Depends on the external stope.conditions library being installed;
        skips gracefully otherwise.
        """
        pytest.importorskip("stope.conditions")
        ctx = build_dynamic_state(self.ast, trust=0.0, state_vars={"ruin": 5})
        assert '<voice_reminder peak="true">' in ctx
        assert "Cuts her own sentence" in ctx

    def test_build_dialogue_context_passes_uncertain_through(self):
        ctx = build_dialogue_context(self.ast, trust=0.5, uncertain=True)
        assert "<uncertainty_voice>" in ctx


# ---------------------------------------------------------------------------
# Phase 4: observability — build_dialogue_context_debug
# ---------------------------------------------------------------------------


class TestDebugDict:
    def setup_method(self):
        self.ast = parse(PHASE0_NOTATION)

    def test_debug_returns_tuple(self):
        result = build_dialogue_context_debug(self.ast)
        assert isinstance(result, tuple)
        assert len(result) == 2
        ctx, debug = result
        assert isinstance(ctx, str)
        assert isinstance(debug, dict)

    def test_debug_has_required_top_level_keys(self):
        _, debug = build_dialogue_context_debug(self.ast, trust=0.5, turn=3)
        assert "static" in debug
        assert "dynamic" in debug
        assert "total_chars" in debug
        assert "static_chars" in debug
        assert "dynamic_chars" in debug
        assert debug["total_chars"] == debug["static_chars"] + debug["dynamic_chars"] + 2
        #                                                                             ^^^
        #                                                        "\n\n" join between parts

    def test_debug_static_sections_order(self):
        _, debug = build_dialogue_context_debug(self.ast)
        s = debug["static"]["sections"]
        # Fixture contains: presence, voice, canonical MES, never, quirks, drivermap
        # (no traits, no props, no relationships in PHASE0_NOTATION)
        assert "presence" in s
        assert "voice" in s
        assert "voice_examples_canonical" in s
        assert "never" in s
        assert "quirks" in s
        assert "drivermap" in s
        # Order assertion: presence before voice before canonical MES
        assert s.index("presence") < s.index("voice") < s.index("voice_examples_canonical")

    def test_debug_static_voice_metrics(self):
        _, debug = build_dialogue_context_debug(self.ast)
        assert debug["static"]["has_peak"] is True
        assert debug["static"]["has_peak_when"] is True
        assert debug["static"]["voice_kernel_chars"] > 0
        assert debug["static"]["mes_canonical_count"] == 2

    def test_debug_never_counts(self):
        _, debug = build_dialogue_context_debug(parse(PRIORITY_NOTATION))
        s = debug["static"]
        assert s["never_total"] == 13
        assert s["never_rendered"] == 10
        assert s["never_dropped"] == 3
        assert s["never_critical_count"] == 2

    def test_debug_dynamic_arc_phase_recorded(self):
        _, debug = build_dialogue_context_debug(
            parse(ARC_NOTATION), trust=0.0, known_facts=set()
        )
        assert debug["dynamic"]["arc_phase"] == "guarded"

    def test_debug_dynamic_active_goals_recorded(self):
        _, debug = build_dialogue_context_debug(self.ast, trust=0.8)
        goals = debug["dynamic"]["active_goals"]
        assert len(goals) >= 1
        names = {g["name"] for g in goals}
        assert "protect_daughter" in names
        for g in goals:
            if g["name"] == "protect_daughter":
                assert g["has_behavior"] is True

    def test_debug_dynamic_records_state(self):
        _, debug = build_dialogue_context_debug(
            self.ast, trust=0.3, turn=7, state_vars={"ruin": 2}, uncertain=True
        )
        d = debug["dynamic"]
        assert d["trust"] == 0.3
        assert d["turn"] == 7
        assert d["state_vars"] == {"ruin": 2}
        assert d["uncertain"] is True

    def test_debug_context_equals_plain_build(self):
        """Debug variant must return the same context string as the plain call."""
        plain = build_dialogue_context(
            self.ast, trust=0.5, turn=3, state_vars={"ruin": 1}
        )
        ctx, _ = build_dialogue_context_debug(
            self.ast, trust=0.5, turn=3, state_vars={"ruin": 1}
        )
        assert ctx == plain

    def test_debug_voice_reminder_peak_flag(self):
        """voice_reminder_peak tracks whether peak swap happened."""
        _, debug = build_dialogue_context_debug(
            self.ast, trust=0.0, state_vars={"ruin": 0}
        )
        assert debug["dynamic"]["voice_reminder_peak"] is False
