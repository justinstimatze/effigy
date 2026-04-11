"""Effigy post-processing validators.

Deterministic filters applied to generation output to enforce character
voice rules that prompt-based steering can't reliably hit on its own.

Rules are defined in `.effigy` files via POSTPROC[...] blocks and parsed
into `ast.post_processors`. `validators_from_ast(ast)` turns those into
runnable Validator objects; `validate(text, ast)` runs them and returns
a list of ValidationViolation; `strip_violations(text, violations)`
removes spans marked with action="strip".

This module is the deterministic layer underneath the stochastic LLM
output — hard rules (things that MUST be enforced) belong here rather
than in prompt instructions, which are probabilistic.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from effigy.notation import CharacterAST

Action = Literal["reject", "strip", "warn"]
Severity = Literal["warn", "error"]


@dataclass
class ValidationViolation:
    """A single rule-violation found in generated text."""

    rule_id: str
    severity: Severity
    message: str
    matched_text: str
    span: tuple[int, int]
    action: Action


class Validator(Protocol):
    rule_id: str

    def check(
        self, text: str, ast: CharacterAST
    ) -> list[ValidationViolation]: ...


@dataclass
class RegexValidator:
    """A regex-based validator: reports every match as a violation."""

    rule_id: str
    pattern: str
    action: Action = "warn"
    why: str = ""
    flags: int = re.IGNORECASE
    _compiled: re.Pattern = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._compiled = re.compile(self.pattern, self.flags)

    def check(self, text: str, ast: CharacterAST) -> list[ValidationViolation]:
        severity: Severity = "error" if self.action == "reject" else "warn"
        return [
            ValidationViolation(
                rule_id=self.rule_id,
                severity=severity,
                message=self.why or f"matched /{self.pattern}/",
                matched_text=m.group(0),
                span=m.span(),
                action=self.action,
            )
            for m in self._compiled.finditer(text)
        ]


def validators_from_ast(ast: CharacterAST) -> list[Validator]:
    """Build validators from the AST's POSTPROC rules.

    Malformed rules (invalid regex) are skipped rather than raising —
    a bad rule in one character file shouldn't poison validation of
    an unrelated response.
    """
    validators: list[Validator] = []
    for rule in ast.post_processors:
        try:
            validators.append(
                RegexValidator(
                    rule_id=rule.rule_id or "postproc",
                    pattern=rule.pattern,
                    action=rule.action,  # type: ignore[arg-type]
                    why=rule.why,
                )
            )
        except re.error:
            # Invalid regex; skip and continue.
            continue
    return validators


def validate(
    text: str,
    ast: CharacterAST,
    validators: list[Validator] | None = None,
) -> list[ValidationViolation]:
    """Run every validator against text and return all violations found.

    If `validators` is None, builds them from ast.post_processors.
    """
    runners = validators if validators is not None else validators_from_ast(ast)
    out: list[ValidationViolation] = []
    for v in runners:
        out.extend(v.check(text, ast))
    return out


def strip_violations(text: str, violations: list[ValidationViolation]) -> str:
    """Remove spans marked with action='strip'.

    Processes matches right-to-left so earlier spans keep their
    original offsets. Collapses double spaces introduced by strip
    operations and trims the result.
    """
    strips = sorted(
        (v for v in violations if v.action == "strip"),
        key=lambda v: -v.span[0],
    )
    result = text
    for v in strips:
        start, end = v.span
        result = result[:start] + result[end:]
    result = re.sub(r"[ \t]{2,}", " ", result)
    return result.strip()


def has_blocking_violation(violations: list[ValidationViolation]) -> bool:
    """True if any violation has action='reject' (severity='error')."""
    return any(v.action == "reject" for v in violations)


def _format_revise_feedback(violations: list[ValidationViolation]) -> str:
    """Build a concrete retry prompt citing specific rule violations.

    Names each violated rule with its matched text so the LLM has
    something specific to avoid rather than a vague 'try again'.
    """
    errors = [v for v in violations if v.severity == "error"]
    if not errors:
        return ""
    lines = ["Your previous response violated these rules:"]
    for v in errors:
        lines.append(
            f"  - [{v.rule_id}] {v.message} (matched: {v.matched_text!r})"
        )
    lines.append("")
    lines.append("Regenerate, avoiding these patterns. Keep everything else.")
    return "\n".join(lines)


def revise_if_violated(
    generated: str,
    ast: CharacterAST,
    *,
    llm_call: Callable[[str], str],
    validators: list[Validator] | None = None,
    max_retries: int = 1,
) -> tuple[str, list[ValidationViolation]]:
    """Generate → validate → if reject-level violations found, revise once.

    This is the constitutional revise loop (Phase 5). It's the stochastic
    counterpart to `strip_violations` (deterministic): use strip when you
    can mechanically fix the output, use revise when the fix requires
    regeneration.

    Args:
        generated: initial LLM output.
        ast: character AST providing the POSTPROC rules.
        llm_call: caller-supplied callable ``(feedback_prompt) -> str``
            that regenerates the response given retry feedback. Effigy
            does not import any LLM SDK — the caller owns the model.
        validators: optional explicit validator list. Defaults to
            ``validators_from_ast(ast)``.
        max_retries: how many times to retry on blocking violations.
            Defaults to 1. More retries rarely help and usually indicate
            the underlying prompt needs work rather than the retry loop.

    Returns ``(final_text, violations)``. ``violations`` is the list from
    the *final* validation pass — callers can inspect it to see what
    remained unresolved, even after retries were exhausted.
    """
    runners = validators if validators is not None else validators_from_ast(ast)
    violations = validate(generated, ast, runners)

    if not has_blocking_violation(violations) or max_retries <= 0:
        return generated, violations

    feedback = _format_revise_feedback(violations)
    revised = llm_call(feedback)
    return revise_if_violated(
        revised,
        ast,
        llm_call=llm_call,
        validators=runners,
        max_retries=max_retries - 1,
    )
