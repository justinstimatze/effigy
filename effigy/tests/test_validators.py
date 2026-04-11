"""Tests for effigy.validators — Phase 3 of the 2026-alignment plan."""

from effigy.notation import CharacterAST, PostProcRuleAST
from effigy.parser import parse
from effigy.validators import (
    RegexValidator,
    ValidationViolation,
    has_blocking_violation,
    revise_if_violated,
    strip_violations,
    validate,
    validators_from_ast,
)


POSTPROC_NOTATION = """
@id test
@name Test

POSTPROC[
  action: strip
  pattern: \\*[^*]*\\*
  why: strip roleplay asterisks
  id: no_asterisks
  ---
  action: reject
  pattern: waiting for exactly
  why: Hank narrator slip
  id: hank_cinematic
  ---
  action: warn
  pattern: \\bdata\\b
  why: academic register slip
]
"""


class TestRegexValidator:
    def test_match_returns_violation(self):
        v = RegexValidator(rule_id="x", pattern="foo", action="warn")
        ast = CharacterAST()
        violations = v.check("foo bar foo", ast)
        assert len(violations) == 2
        assert violations[0].matched_text == "foo"
        assert violations[0].span == (0, 3)
        assert violations[1].span == (8, 11)

    def test_case_insensitive_by_default(self):
        v = RegexValidator(rule_id="x", pattern="FOO", action="warn")
        violations = v.check("foo FOO Foo", CharacterAST())
        assert len(violations) == 3

    def test_reject_action_maps_to_error_severity(self):
        v = RegexValidator(rule_id="x", pattern="bad", action="reject")
        violations = v.check("this is bad", CharacterAST())
        assert len(violations) == 1
        assert violations[0].severity == "error"
        assert violations[0].action == "reject"

    def test_warn_action_maps_to_warn_severity(self):
        v = RegexValidator(rule_id="x", pattern="mild", action="warn")
        violations = v.check("mildly", CharacterAST())
        assert violations[0].severity == "warn"

    def test_message_uses_why_when_present(self):
        v = RegexValidator(rule_id="x", pattern="y", action="warn", why="because")
        violations = v.check("yyy", CharacterAST())
        assert all(vio.message == "because" for vio in violations)

    def test_no_match_returns_empty_list(self):
        v = RegexValidator(rule_id="x", pattern="absent", action="reject")
        assert v.check("present", CharacterAST()) == []


class TestValidatorsFromAST:
    def setup_method(self):
        self.ast = parse(POSTPROC_NOTATION)

    def test_parses_all_rules(self):
        assert len(self.ast.post_processors) == 3

    def test_builds_validators(self):
        validators = validators_from_ast(self.ast)
        assert len(validators) == 3

    def test_rule_ids_preserved(self):
        validators = validators_from_ast(self.ast)
        ids = [v.rule_id for v in validators]
        assert "no_asterisks" in ids
        assert "hank_cinematic" in ids
        # Third rule had no id -> auto-generated
        auto = [i for i in ids if i not in ("no_asterisks", "hank_cinematic")]
        assert len(auto) == 1

    def test_invalid_regex_is_skipped(self):
        ast = CharacterAST()
        ast.post_processors = [
            PostProcRuleAST(action="warn", pattern="[unclosed", rule_id="bad"),
            PostProcRuleAST(action="warn", pattern="ok", rule_id="good"),
        ]
        validators = validators_from_ast(ast)
        assert len(validators) == 1
        assert validators[0].rule_id == "good"


class TestValidate:
    def setup_method(self):
        self.ast = parse(POSTPROC_NOTATION)

    def test_validate_finds_rejected_pattern(self):
        violations = validate("he was waiting for exactly this", self.ast)
        assert any(v.rule_id == "hank_cinematic" for v in violations)

    def test_validate_finds_strip_pattern(self):
        violations = validate("*smirks* yeah sure", self.ast)
        assert any(v.rule_id == "no_asterisks" for v in violations)

    def test_validate_clean_text_empty(self):
        violations = validate("nothing to worry about here", self.ast)
        assert violations == []

    def test_validate_accepts_explicit_validator_list(self):
        custom = [RegexValidator(rule_id="x", pattern="foo", action="warn")]
        violations = validate("foo", self.ast, validators=custom)
        assert len(violations) == 1
        assert violations[0].rule_id == "x"


class TestStripViolations:
    def setup_method(self):
        self.ast = parse(POSTPROC_NOTATION)

    def test_strip_removes_matched_span(self):
        text = "yeah *smirks* sure"
        violations = validate(text, self.ast)
        cleaned = strip_violations(text, violations)
        assert "*smirks*" not in cleaned
        assert "yeah" in cleaned
        assert "sure" in cleaned

    def test_strip_handles_multiple_spans_right_to_left(self):
        text = "a *foo* b *bar* c"
        violations = validate(text, self.ast)
        cleaned = strip_violations(text, violations)
        assert "*foo*" not in cleaned
        assert "*bar*" not in cleaned
        assert "a" in cleaned and "b" in cleaned and "c" in cleaned

    def test_strip_ignores_non_strip_violations(self):
        """Warn/reject violations do not trigger removal."""
        text = "analyze the data carefully"
        violations = validate(text, self.ast)
        # 'data' is a 'warn' rule, not 'strip'. Text must remain intact.
        cleaned = strip_violations(text, violations)
        assert "data" in cleaned

    def test_strip_clean_text_returns_unchanged(self):
        text = "nothing to strip"
        assert strip_violations(text, []) == text


class TestHasBlockingViolation:
    def setup_method(self):
        self.ast = parse(POSTPROC_NOTATION)

    def test_blocking_when_reject_present(self):
        violations = validate("he was waiting for exactly this", self.ast)
        assert has_blocking_violation(violations) is True

    def test_not_blocking_when_only_warn_or_strip(self):
        violations = validate("*smirks* analyze the data", self.ast)
        assert all(v.action != "reject" for v in violations)
        assert has_blocking_violation(violations) is False

    def test_not_blocking_when_empty(self):
        assert has_blocking_violation([]) is False


class TestPostprocParser:
    def test_parses_action_pattern_why_id(self):
        ast = parse(POSTPROC_NOTATION)
        rules = ast.post_processors
        assert len(rules) == 3
        no_asterisks = next(r for r in rules if r.rule_id == "no_asterisks")
        assert no_asterisks.action == "strip"
        assert no_asterisks.why == "strip roleplay asterisks"

    def test_missing_action_is_skipped(self):
        text = (
            "@id x\nPOSTPROC[\n"
            "  pattern: foo\n"
            "  why: no action\n"
            "]\n"
        )
        ast = parse(text)
        assert ast.post_processors == []

    def test_missing_pattern_is_skipped(self):
        text = (
            "@id x\nPOSTPROC[\n"
            "  action: strip\n"
            "  why: no pattern\n"
            "]\n"
        )
        ast = parse(text)
        assert ast.post_processors == []

    def test_invalid_action_is_skipped(self):
        text = (
            "@id x\nPOSTPROC[\n"
            "  action: explode\n"
            "  pattern: foo\n"
            "]\n"
        )
        ast = parse(text)
        assert ast.post_processors == []

    def test_auto_id_when_missing(self):
        text = (
            "@id x\nPOSTPROC[\n"
            "  action: warn\n"
            "  pattern: foo\n"
            "]\n"
        )
        ast = parse(text)
        assert len(ast.post_processors) == 1
        assert ast.post_processors[0].rule_id  # non-empty


class TestReviseIfViolated:
    def setup_method(self):
        self.ast = parse(POSTPROC_NOTATION)

    def test_returns_original_when_no_violations(self):
        calls: list[str] = []

        def fake_llm(feedback: str) -> str:
            calls.append(feedback)
            return "should never run"

        text = "nothing to see here"
        result, violations = revise_if_violated(
            text, self.ast, llm_call=fake_llm, max_retries=1
        )
        assert result == text
        assert calls == []
        assert violations == []

    def test_retries_once_on_blocking_violation(self):
        calls: list[str] = []

        def fake_llm(feedback: str) -> str:
            calls.append(feedback)
            return "clean text now"

        bad_text = "he was waiting for exactly this moment"
        result, violations = revise_if_violated(
            bad_text, self.ast, llm_call=fake_llm, max_retries=1
        )
        assert len(calls) == 1
        assert "hank_cinematic" in calls[0]
        assert result == "clean text now"
        # Second validation against clean text -> no blocking violations.
        assert not any(v.action == "reject" for v in violations)

    def test_max_retries_zero_returns_original_and_violations(self):
        def fake_llm(feedback: str) -> str:
            raise AssertionError("llm should not be called when max_retries=0")

        bad_text = "he was waiting for exactly this"
        result, violations = revise_if_violated(
            bad_text, self.ast, llm_call=fake_llm, max_retries=0
        )
        assert result == bad_text
        assert any(v.action == "reject" for v in violations)

    def test_exhausted_retries_returns_last_attempt(self):
        calls: list[str] = []

        def fake_llm(feedback: str) -> str:
            calls.append(feedback)
            # Keeps producing the same blocking violation forever.
            return "still waiting for exactly this"

        result, violations = revise_if_violated(
            "waiting for exactly nothing",
            self.ast,
            llm_call=fake_llm,
            max_retries=2,
        )
        assert len(calls) == 2
        # Final result is the last thing the LLM returned.
        assert result == "still waiting for exactly this"
        # And its violations are still surfaced to the caller.
        assert any(v.action == "reject" for v in violations)

    def test_feedback_includes_rule_id_and_matched_text(self):
        captured: list[str] = []

        def fake_llm(feedback: str) -> str:
            captured.append(feedback)
            return "clean"

        revise_if_violated(
            "he was waiting for exactly the right moment",
            self.ast,
            llm_call=fake_llm,
            max_retries=1,
        )
        assert len(captured) == 1
        fb = captured[0]
        assert "hank_cinematic" in fb
        assert "waiting for exactly" in fb

    def test_only_reject_violations_trigger_retry(self):
        """Warn-only violations should not cause a retry loop."""
        calls: list[str] = []

        def fake_llm(feedback: str) -> str:
            calls.append(feedback)
            return "changed"

        # 'analyze the data' hits the 'data' warn rule but no reject rule.
        text = "analyze the data carefully"
        result, violations = revise_if_violated(
            text, self.ast, llm_call=fake_llm, max_retries=1
        )
        assert calls == []  # no retry happened
        assert result == text
        # But the warn violation is still reported.
        assert any(v.severity == "warn" for v in violations)
