"""Effigy Layer 2 -- AST + game state -> optimized prompt context.

Generates runtime prompt sections from the parsed CharacterAST combined
with current game state (trust, state variables, known facts, turn count).

No LLM calls. Deterministic.
- Arc phase resolution (which phase is active given current state)
- Voice drift prevention (rotated mes_examples, cross-character contrast)
- Dynamic voice modulation (arc phase voice augments static voice_kernel)
"""

from __future__ import annotations

import collections
import logging
import re
from typing import Any

from effigy.notation import (
    ArcPhaseAST,
    CharacterAST,
)

logger = logging.getLogger(__name__)

# Max NEVER rules surfaced in generation context. LLMs reliably attend
# to ~5-7 constraints of roughly equal weight; 10 is a pragmatic upper
# bound given that effigy also strips inline examples from NEVER rules
# (see _strip_inline_examples), which shrinks the effective weight of
# each constraint. The saturation research assumes equal-length rules;
# after stripping inline WRONG/RIGHT/NOT/YES blocks, rules are short
# enough that 10 fits the same attention budget as 7 bloated ones.
MAX_NEVER_RULES = 10

# Max reasoning tests surfaced in generation context. Tests are longer
# than NEVER rules (question + fail/pass examples + why ≈ 4-6 lines each),
# so 5 tests occupy roughly the same attention budget as 10 NEVER rules.
MAX_TESTS = 5

# Markers authors conventionally use to embed inline examples inside a
# NEVER rule. Case-sensitive (uppercase only) — lowercase 'wrong' in
# prose is fine. Matches `WRONG:`, `RIGHT:`, `NOT:`, `YES:`, `BAD:`,
# `GOOD:`, `EXAMPLE:`, `EX:`, `BEFORE:`, `AFTER:`.
_INLINE_EXAMPLE_MARKERS = re.compile(
    r"\b(?:WRONG|RIGHT|NOT|YES|BAD|GOOD|EXAMPLE|EX|BEFORE|AFTER)\s*:"
)


def _strip_inline_examples(rule: str) -> str:
    """Remove inline WRONG/RIGHT/NOT/YES example text from a NEVER rule.

    Inline examples in NEVER rules have the same LLM-priming problem as
    standalone WRONG blocks: the model pattern-matches on the example
    text regardless of the negative label. Stripping keeps the
    constraint statement and drops the anti-examples.

    Authors who want inline examples mark them with uppercase prefixes
    (``WRONG:``, ``RIGHT:``, ``NOT:``, ``YES:``, ``BAD:``, ``GOOD:``,
    ``EXAMPLE:``, etc.). Prose that happens to use these words in
    lowercase is unaffected.
    """
    m = _INLINE_EXAMPLE_MARKERS.search(rule)
    if not m:
        return rule
    return rule[: m.start()].rstrip(" \n\t-—–:;,.").rstrip()

def validate_never_budget(ast: CharacterAST) -> list[dict[str, Any]]:
    """Report NEVER rules that exceed the per-character cap.

    Returns one warning dict per character whose ``never_would_say`` list is
    longer than ``MAX_NEVER_RULES``. Each warning carries the rule texts that
    would be silently dropped at generation time, in the same priority order
    used by ``build_dialogue_context`` (CRITICAL-prefixed first, then the rest
    in declaration order).

    Empty list if the character is within budget.
    """
    if not ast.never_would_say:
        return []
    critical = [n for n in ast.never_would_say if n.text.upper().startswith("CRITICAL:")]
    regular = [n for n in ast.never_would_say if not n.text.upper().startswith("CRITICAL:")]
    prioritized = critical + regular
    if len(prioritized) <= MAX_NEVER_RULES:
        return []
    dropped = [n.text for n in prioritized[MAX_NEVER_RULES:]]
    return [{
        "char_id": ast.char_id,
        "total": len(prioritized),
        "cap": MAX_NEVER_RULES,
        "critical_count": len(critical),
        "dropped": dropped,
    }]


# Number of MES examples rendered as the "canonical" cache-stable slice.
# These are the first N entries from notation, always shown, no rotation.
# The remaining MES examples rotate in the dynamic block.
CANONICAL_MES_COUNT = 2

# Max MES examples shown in the rotating dynamic slice.
MAX_ROTATING_MES = 2

# Try to import an optional external condition evaluator. Falls back to legacy
# dict-based evaluation if not installed (effigy standalone use).
try:
    from stope.conditions import ConditionParseError
    from stope.conditions import evaluate as _cond_evaluate

    _HAS_CONDITIONS = True
except ImportError:
    _HAS_CONDITIONS = False

_RuinState = collections.namedtuple("_RuinState", ["level"])


class _EffigyConditionState:
    """Lightweight adapter so effigy can use an external condition evaluator
    without requiring a full WorldState object."""

    def __init__(
        self,
        trust: float,
        state_vars: dict[str, float],
        known_facts: set[str],
        char_id: str = "",
    ):
        self._trust = trust
        self._state_vars = state_vars
        self._facts = known_facts
        self._char_id = char_id

    def get_knowledge_set(self) -> set[str]:
        return self._facts

    def get_npc_trust(self, char_id: str) -> dict:
        return {"trust": self._trust}

    def get_flag(self, name: str) -> bool:
        return name in self._facts

    @property
    def ruin(self):
        # Adapter for external condition evaluator's WorldState.ruin.level interface
        return _RuinState(level=self._state_vars.get("ruin", 0))

    @property
    def current_turn(self) -> int:
        return 0

    @property
    def current_loc(self) -> str:
        return ""

    @property
    def clocks(self) -> dict:
        return {}


def resolve_arc_phase(
    ast: CharacterAST,
    trust: float = 0.0,
    *,
    known_facts: set[str] | None = None,
    state_vars: dict[str, float] | None = None,
) -> ArcPhaseAST | None:
    """Determine which arc phase is currently active.

    Phases are evaluated in order; the LAST phase whose conditions are
    all met is active. This means phases should be ordered from earliest
    to latest (guarded -> thawing -> vulnerable -> resolved).

    Args:
        trust: Trust level (0.0-1.0).
        known_facts: Set of fact IDs the player has discovered.
        state_vars: Arbitrary numeric state variables your game defines.
            Keys must match condition keywords in ARC blocks
            (e.g., ``ruin>=4`` checks ``state_vars["ruin"]``).

    Returns None if no arc phases are defined or none are satisfied.
    """
    if not ast.arc_phases:
        return None

    known_facts = known_facts or set()
    state_vars = state_vars or {}
    active: ArcPhaseAST | None = None
    char_id = getattr(ast, "name", "") or ""
    for phase in ast.arc_phases:
        if _conditions_met(phase, trust, state_vars, known_facts, char_id):
            active = phase

    return active


def _evaluate_conditions_dict(
    conditions: dict,
    trust: float,
    state_vars: dict[str, float],
    known_facts: set[str],
) -> bool:
    """Evaluate a parsed conditions dict (from parser._parse_conditions).

    Shared by arc-phase checks and @when-gate checks so effigy can
    evaluate both without depending on an external DSL library.
    """
    if not conditions:
        return True

    for key, val in conditions.items():
        if key == "trust" and isinstance(val, dict):
            if not _check_comparison(trust, val):
                return False
        elif key == "facts":
            for fact_id in val:
                if fact_id not in known_facts:
                    return False
        elif key == "raw":
            pass  # unparseable conditions, skip
        elif isinstance(val, dict) and "op" in val:
            # Generic numeric state variable (ruin, corruption, tension, etc.)
            actual = state_vars.get(key, 0.0)
            if not _check_comparison(float(actual), val):
                return False

    return True


def _conditions_met(
    phase: ArcPhaseAST,
    trust: float,
    state_vars: dict[str, float],
    known_facts: set[str],
    char_id: str = "",
) -> bool:
    """Check if all conditions in a phase gate are satisfied.

    Uses the external condition DSL when available, otherwise falls back
    to effigy's native dict-based evaluator on ``phase.conditions``.
    """
    cond_str = phase.condition_str
    if cond_str and _HAS_CONDITIONS:
        resolved = cond_str.replace("_NPC_", char_id) if char_id else cond_str
        try:
            state = _EffigyConditionState(trust, state_vars, known_facts, char_id)
            return _cond_evaluate(resolved, state)
        except (ConditionParseError, Exception):
            logger.debug("Condition parse failed for %r, falling back to legacy", resolved)

    return _evaluate_conditions_dict(phase.conditions, trust, state_vars, known_facts)


def _check_comparison(actual: float, spec: dict) -> bool:
    """Check a numeric comparison: {op: ">=", value: 0.35}."""
    op = spec.get("op", ">=")
    target = spec.get("value", 0.0)
    if op == ">=":
        return actual >= target
    elif op == ">":
        return actual > target
    elif op == "<=":
        return actual <= target
    elif op == "<":
        return actual < target
    elif op == "==":
        return actual == target
    elif op == "!=":
        return actual != target
    return True


def resolve_active_goals(
    ast: CharacterAST,
    trust: float = 0.0,
    *,
    known_facts: set[str] | None = None,
    state_vars: dict[str, float] | None = None,
) -> list[dict]:
    """Return goals with adjusted weights based on game state.

    Goal weights shift based on their grows_with field:
    - "trust" -- weight increases proportionally to trust
    - "evidence" -- weight increases with number of known facts
    - Any state_var name -- weight increases with that variable's value
      (internally normalized assuming a 0-10 scale)

    Returns list of {name, weight, active} dicts sorted by weight desc.
    """
    if not ast.goals:
        return []

    known_facts = known_facts or set()
    state_vars = state_vars or {}
    results: list[dict[str, Any]] = []
    for goal in ast.goals:
        weight = goal.weight
        if goal.grows_with:
            growth = goal.grows_with.lower()
            if "trust" in growth:
                weight = min(1.0, weight + trust * 0.3)
            if "evidence" in growth:
                fact_factor = min(1.0, len(known_facts) / 20.0)
                weight = min(1.0, weight + fact_factor * 0.3)
            # Generic state_var growth
            for var_name, var_val in state_vars.items():
                if var_name in growth:
                    weight = min(1.0, weight + min(1.0, var_val / 10.0) * 0.2)

        results.append(
            {
                "name": goal.name,
                "weight": round(weight, 2),
                "active": weight >= 0.6,
                "grows_with": goal.grows_with,
            }
        )

    results.sort(key=lambda g: -float(g["weight"]))
    return results


def select_mes_examples(
    ast: CharacterAST,
    turn: int,
    max_examples: int = 4,
    trust: float = 0.0,
) -> list[str]:
    """Select mes_examples with trust-gated filtering and rotation.

    Each MES example has a trust tier (low/moderate/high/any).
    This function filters to examples appropriate for the current
    trust level, then rotates within the allowed pool.

    Trust tier filtering:
      trust < 0.2  -> show "low" + "any" examples only
      trust 0.2-0.5 -> show "low" + "moderate" + "any"
      trust >= 0.5 -> show "moderate" + "high" + "any"
    """
    if not ast.mes_examples:
        return []

    # Determine allowed tiers
    if trust >= 0.5:
        allowed = {"high", "moderate", "any"}
    elif trust >= 0.2:
        allowed = {"low", "moderate", "any"}
    else:
        allowed = {"low", "any"}

    # Filter -- handle both MESExample objects and plain strings (legacy)
    pool: list[str] = []
    for ex in ast.mes_examples:
        if hasattr(ex, "tier"):
            if ex.tier in allowed:
                pool.append(ex.text)
        else:
            # Legacy plain string -- always include
            pool.append(ex)

    if not pool:
        # Fallback: include all examples if none matched
        pool = [ex.text if hasattr(ex, "text") else ex for ex in ast.mes_examples]

    if len(pool) <= max_examples:
        return pool

    # Turn-based rotation
    selected = [pool[0]]
    remaining = pool[1:]
    offset = turn % len(remaining)
    for i in range(min(max_examples - 1, len(remaining))):
        idx = (offset + i) % len(remaining)
        selected.append(remaining[idx])

    return selected


def select_canonical_mes(ast: CharacterAST) -> list[str]:
    """Return the first CANONICAL_MES_COUNT MES examples, no rotation, no trust gate.

    These are cache-stable — byte-identical across turns and trust levels.
    Authors should order their MES block so the most universal examples
    come first.
    """
    results: list[str] = []
    for ex in ast.mes_examples[:CANONICAL_MES_COUNT]:
        text = ex.text if hasattr(ex, "text") else ex
        results.append(text)
    return results


def select_rotating_mes(
    ast: CharacterAST,
    turn: int,
    *,
    trust: float = 0.0,
    max_examples: int = MAX_ROTATING_MES,
) -> list[str]:
    """Return trust-gated, turn-rotated MES examples EXCLUDING the canonical slice.

    Operates on ast.mes_examples[CANONICAL_MES_COUNT:]. The rotating slice
    is dynamic state — changes with turn/trust. Use in build_dynamic_state.
    """
    candidates = ast.mes_examples[CANONICAL_MES_COUNT:]
    if not candidates:
        return []

    if trust >= 0.5:
        allowed = {"high", "moderate", "any"}
    elif trust >= 0.2:
        allowed = {"low", "moderate", "any"}
    else:
        allowed = {"low", "any"}

    pool: list[str] = []
    for ex in candidates:
        if hasattr(ex, "tier"):
            if ex.tier in allowed:
                pool.append(ex.text)
        else:
            pool.append(ex)

    if not pool:
        return []
    if len(pool) <= max_examples:
        return pool

    offset = turn % len(pool)
    return [pool[(offset + i) % len(pool)] for i in range(max_examples)]


def _evaluate_condition_string(
    cond_str: str,
    trust: float,
    state_vars: dict[str, float] | None,
    known_facts: set[str] | None,
    char_id: str = "",
) -> bool:
    """Evaluate a standalone condition string (voice.peak_when, @when gates).

    Uses the external condition DSL when available; otherwise parses the
    string with effigy's native ``_parse_conditions`` grammar (the same
    grammar ARC blocks use) and evaluates via ``_evaluate_conditions_dict``.
    Returns False if neither path can parse the condition.
    """
    if not cond_str:
        return False
    state_vars = state_vars or {}
    known_facts = known_facts or set()
    if _HAS_CONDITIONS:
        resolved = cond_str.replace("_NPC_", char_id) if char_id else cond_str
        try:
            state = _EffigyConditionState(trust, state_vars, known_facts, char_id)
            return bool(_cond_evaluate(resolved, state))
        except Exception:
            logger.debug("DSL eval failed for %r, falling back to native", resolved)

    # Native fallback: parse with effigy's own grammar and evaluate.
    from effigy.parser import _parse_conditions

    try:
        conds = _parse_conditions(cond_str)
    except Exception:
        logger.debug("native condition parse failed for %r", cond_str)
        return False
    if not conds:
        return False
    return _evaluate_conditions_dict(conds, trust, state_vars, known_facts)


def _when_matches(
    when: str,
    trust: float,
    state_vars: dict[str, float] | None,
    known_facts: set[str] | None,
    char_id: str = "",
) -> bool:
    """Evaluate a ``@when`` condition string. Empty or ``"*"`` means always-on.

    ``_evaluate_condition_string`` handles both the external DSL library
    (when installed) and effigy's native grammar fallback, so this path
    works standalone without a runtime dependency on ``stope.conditions``.
    """
    if not when or when.strip() == "*":
        return True
    return _evaluate_condition_string(when, trust, state_vars, known_facts, char_id)


def filter_ast_by_state(
    ast: CharacterAST,
    trust: float = 0.0,
    *,
    state_vars: dict[str, float] | None = None,
    known_facts: set[str] | None = None,
    beat: str | None = None,
) -> CharacterAST:
    """Return a new CharacterAST with ``@when``-gated items pruned by state.

    This is the v0.5.0 pre-filter pass. Callers run it BEFORE
    ``build_static_context`` and ``build_dynamic_state`` so both builders
    consume an already-filtered AST. The static context remains
    byte-stable for a given filtered AST; the cache key is the filtered
    AST's identity (or hash), not the raw state params.

    Filters items with a ``@when`` gate across:
      * ``ast.mes_examples``
      * ``ast.never_would_say``
      * ``ast.wrong_examples``
      * ``ast.tests``

    Items without ``@when`` are always retained. Conditions are evaluated
    with the external DSL library when available, otherwise with effigy's
    native ``_parse_conditions`` grammar (same syntax as ARC phase gates).
    Unparseable conditions are treated as unmet (item dropped).

    When ``beat`` is provided (v0.6.0), also drops items whose ``@beat``
    label is set AND doesn't match ``beat``. Items with no ``@beat`` are
    treated as universal and retained at every beat. NEVER rules have no
    ``beat`` attribute, so beat filtering never removes them — they're
    constraints, not exemplars.

    Falsy ``beat`` values (``None`` or ``""``) disable the beat filter
    entirely — ``beat=""`` is a forgiving no-op rather than "keep only
    universals," so callers can pass ``classifier.choice or ""`` without
    special-casing an empty classifier output.

    This function does not mutate ``ast``. Items not subject to
    ``@when`` filtering are shared by reference with the input AST.
    """
    import dataclasses

    char_id = getattr(ast, "char_id", "") or getattr(ast, "name", "") or ""

    def keep(item) -> bool:
        when = getattr(item, "when", "")
        if when and not _when_matches(when, trust, state_vars, known_facts, char_id):
            return False
        if beat:  # falsy (None or "") → beat filter disabled
            item_beat = getattr(item, "beat", "")
            if item_beat and item_beat != beat:
                return False
        return True

    return dataclasses.replace(
        ast,
        mes_examples=[e for e in ast.mes_examples if keep(e)],
        never_would_say=[n for n in ast.never_would_say if keep(n)],
        wrong_examples=[w for w in ast.wrong_examples if keep(w)],
        tests=[t for t in ast.tests if keep(t)],
    )


def next_beat(
    phase: ArcPhaseAST | None,
    covered: set[str] | None = None,
) -> str | None:
    """Return the next beat to target in a phase's authored progression.

    Returns ``None`` when ``phase`` is ``None`` or the phase has no
    ``beats:`` list. This gives callers a clean gate:

        beat = next_beat(phase, covered)
        if beat:
            filtered = filter_ast_by_state(ast, ..., beat=beat)
            # compiled single-beat path
        else:
            filtered = filter_ast_by_state(ast, ...)
            # kitchen-sink path

    When a phase has beats, returns the first beat in authored order that
    isn't yet in ``covered``. If all beats are covered, cycles back to
    ``phase.beats[0]`` so the caller can decide when to reset ``covered``
    rather than being told "you're done."
    """
    if phase is None or not phase.beats:
        return None
    covered = covered or set()
    for b in phase.beats:
        if b not in covered:
            return b
    return phase.beats[0]


def validate_when_conditions(ast: CharacterAST) -> list[str]:
    """Return parse errors for all ``@when`` conditions in the AST.

    Empty list means every condition parses cleanly. Used as a pre-commit
    check to catch typos like ``@when trsut>=0.6`` at author time rather
    than at runtime (where the silent failure would be "item disappears").

    When the external DSL library is available it's used for validation;
    otherwise the native ``_parse_conditions`` grammar is used. The native
    grammar covers everything ARC blocks accept (trust/state_var
    comparisons, ``fact:``, ``AND``), so this works standalone.
    """
    from effigy.parser import _parse_conditions

    errors: list[str] = []
    char_id = getattr(ast, "char_id", "") or ""

    def all_whens():
        for i, ex in enumerate(ast.mes_examples):
            yield f"MES[{i}]", getattr(ex, "when", "")
        for i, rule in enumerate(ast.never_would_say):
            yield f"NEVER[{i}]", getattr(rule, "when", "")
        for i, we in enumerate(ast.wrong_examples):
            yield f"WRONG[{i}]", getattr(we, "when", "")
        for i, t in enumerate(ast.tests):
            yield f"TEST[{i}]", getattr(t, "when", "")

    for label, when in all_whens():
        if not when or when.strip() == "*":
            continue
        if _HAS_CONDITIONS:
            resolved = when.replace("_NPC_", char_id) if char_id else when
            try:
                state = _EffigyConditionState(0.0, {}, set(), char_id)
                _cond_evaluate(resolved, state)
                continue
            except ConditionParseError as e:
                errors.append(f"{label}: {when!r}: {e}")
                continue
            except Exception:
                pass  # fall through to native check
        # Native: parseable means at least one condition was extracted
        # AND no "raw" (unrecognized) parts snuck through.
        try:
            conds = _parse_conditions(when)
        except Exception as e:
            errors.append(f"{label}: {when!r}: {e}")
            continue
        if not conds:
            errors.append(f"{label}: {when!r}: no recognizable conditions")
        elif "raw" in conds:
            errors.append(
                f"{label}: {when!r}: unrecognized parts {conds['raw']!r}"
            )
    return errors


# Minimum exemplar count per declared beat before validator flags it.
# Below MIN_EXEMPLARS_PER_BEAT_ERROR: hard error (not enough variety).
# Below MIN_EXEMPLARS_PER_BEAT_WARN: warning (generation quality degrades).
MIN_EXEMPLARS_PER_BEAT_ERROR = 2
MIN_EXEMPLARS_PER_BEAT_WARN = 3


def validate_beat_references(ast: CharacterAST) -> list[str]:
    """Cross-check ``@beat`` annotations against phase ``beats:`` lists.

    Returns a list of error/warning strings. Empty means the beat layout
    is clean. Intended for pre-commit hooks, same style as
    ``validate_when_conditions`` and ``validate_never_budget``.

    Checks (in order):

    1. Every ``@beat NAME`` on MES/WRONG/TEST appears in some phase's
       ``beats:`` list. Catches typos like ``@beat COS`` when the phase
       declares ``COST``. Prefix: ``ERROR``.
    2. Every name in a phase's ``beats:`` list has at least
       ``MIN_EXEMPLARS_PER_BEAT_ERROR`` MES examples tagged with it.
       Prefix: ``ERROR``. Below this the beat path produces degenerate
       output.
    3. Beats that pass (1) and (2) but have fewer than
       ``MIN_EXEMPLARS_PER_BEAT_WARN`` MES examples get a ``WARN``.
       Generation quality drops noticeably below 3 exemplars per beat.

    WRONG and TEST beats are validated against the same name set but
    don't count toward the exemplar budget — only MES entries do (those
    are what the LLM generates from).
    """
    errors: list[str] = []

    declared_beats: set[str] = set()
    beats_by_phase: dict[str, list[str]] = {}
    for phase in ast.arc_phases:
        if phase.beats:
            beats_by_phase[phase.name] = list(phase.beats)
            declared_beats.update(phase.beats)

    # --- (1) unknown @beat references ---
    def _check_known(label: str, beat_val: str) -> None:
        if beat_val and beat_val not in declared_beats:
            errors.append(
                f"ERROR {label}: @beat {beat_val!r} not declared in any "
                f"phase's beats: list (known: {sorted(declared_beats) or 'none'})"
            )

    for i, ex in enumerate(ast.mes_examples):
        _check_known(f"MES[{i}]", getattr(ex, "beat", ""))
    for i, we in enumerate(ast.wrong_examples):
        _check_known(f"WRONG[{i}]", getattr(we, "beat", ""))
    for i, t in enumerate(ast.tests):
        _check_known(f"TEST[{i}]", getattr(t, "beat", ""))

    # --- (2, 3) exemplar counts per declared beat ---
    mes_counts: dict[str, int] = {}
    for ex in ast.mes_examples:
        b = getattr(ex, "beat", "")
        if b:
            mes_counts[b] = mes_counts.get(b, 0) + 1

    for phase_name, beats in beats_by_phase.items():
        # Dedupe while preserving authored order and flag any duplicates
        # as their own ERROR. Without dedup we'd report the same
        # exemplar-count finding once per occurrence.
        seen: set[str] = set()
        unique_beats: list[str] = []
        dupes: list[str] = []
        for beat_name in beats:
            if beat_name in seen:
                if beat_name not in dupes:
                    dupes.append(beat_name)
            else:
                seen.add(beat_name)
                unique_beats.append(beat_name)
        for d in dupes:
            errors.append(
                f"ERROR phase {phase_name!r}: beat {d!r} appears multiple "
                f"times in beats: list"
            )
        for beat_name in unique_beats:
            count = mes_counts.get(beat_name, 0)
            if count < MIN_EXEMPLARS_PER_BEAT_ERROR:
                errors.append(
                    f"ERROR phase {phase_name!r} beat {beat_name!r}: "
                    f"{count} MES exemplar(s), need at least "
                    f"{MIN_EXEMPLARS_PER_BEAT_ERROR}"
                )
            elif count < MIN_EXEMPLARS_PER_BEAT_WARN:
                errors.append(
                    f"WARN phase {phase_name!r} beat {beat_name!r}: "
                    f"only {count} MES exemplar(s), quality degrades "
                    f"below {MIN_EXEMPLARS_PER_BEAT_WARN}"
                )

    return errors


def _compress_drivermap_profile(profile: dict[str, str]) -> str:
    """Compress a drivermap profile dict to a short one-liner.

    {"evidence": "+", "conflict": "-"} -> "evidence+, conflict-"
    """
    parts: list[str] = []
    for k, v in profile.items():
        v = (v or "").strip()
        if v in ("+", "-"):
            parts.append(f"{k}{v}")
        elif v == "neutral" or not v:
            parts.append(f"{k}=")
        else:
            parts.append(f"{k}:{v}")
    return ", ".join(parts)


def build_static_context(
    ast: CharacterAST,
    *,
    voice_override: str | None = None,
    suppress_peak: bool = True,
    mes_override: list[str] | None = None,
    _debug: dict | None = None,
) -> str:
    """Turn-invariant character definition — the cacheable prefix.

    This section depends only on the parsed AST. No trust, no turn, no
    state_vars. The output is byte-stable for a given AST, making it
    eligible for prompt prefix caching (Anthropic, OpenAI, etc.).

    Sections are ordered strongest-signal-first for attention:
    presence → voice → voice_examples → never → tests → quirks →
    props → relationships → traits → drivermap.

    Args:
        voice_override: If provided, replaces ``ast.voice.kernel`` in the
            rendered ``<voice>`` block. Use when a late-arc phase voice
            should dominate the entire prompt rather than compete with
            the guarded-phase kernel. Cache key changes per override.
        suppress_peak: When ``voice_override`` is set, suppress the
            ``<peak>`` element (peak contrasts kernel; when kernel IS the
            phase voice, peak becomes noise). Defaults True. Set False to
            keep peak rendering alongside the override.
        mes_override: If provided, replaces the canonical MES slice.
            Typically phase-appropriate examples computed by the caller.
        _debug: Optional dict; when provided, populated with per-section
            observability data (section names emitted, counts, truncation
            info). Public callers should use build_dialogue_context_debug
            rather than passing _debug directly.
    """
    sections: list[str] = []
    dbg_sections: list[str] = []

    # --- Presence note (brief physical/mood opener) ---
    if ast.presence_note:
        sections.append(f"<presence>{ast.presence_note}</presence>")
        dbg_sections.append("presence")

    # --- Voice (kernel + optional peak + peak_when condition) ---
    if voice_override:
        voice_lines = ["<voice>", f"  <kernel>{voice_override}</kernel>"]
        has_peak = False
        if not suppress_peak and ast.voice and ast.voice.peak:
            has_peak = True
            peak_attr = ""
            if ast.voice.peak_when:
                peak_attr = f' when="{ast.voice.peak_when}"'
            voice_lines.append(f"  <peak{peak_attr}>{ast.voice.peak}</peak>")
        voice_lines.append("</voice>")
        sections.append("\n".join(voice_lines))
        dbg_sections.append("voice")
        if _debug is not None:
            _debug["voice_kernel_chars"] = len(voice_override)
            _debug["has_peak"] = has_peak
            _debug["has_peak_when"] = bool(
                ast.voice.peak_when if ast.voice else False
            )
            _debug["voice_override"] = True
    elif ast.voice and ast.voice.kernel:
        voice_lines = ["<voice>", f"  <kernel>{ast.voice.kernel}</kernel>"]
        has_peak = False
        if ast.voice.peak:
            has_peak = True
            peak_attr = ""
            if ast.voice.peak_when:
                peak_attr = f' when="{ast.voice.peak_when}"'
            voice_lines.append(f"  <peak{peak_attr}>{ast.voice.peak}</peak>")
        voice_lines.append("</voice>")
        sections.append("\n".join(voice_lines))
        dbg_sections.append("voice")
        if _debug is not None:
            _debug["voice_kernel_chars"] = len(ast.voice.kernel)
            _debug["has_peak"] = has_peak
            _debug["has_peak_when"] = bool(ast.voice.peak_when)

    # --- Canonical voice examples (cache-stable MES slice) ---
    canonical_mes = mes_override if mes_override is not None else select_canonical_mes(ast)
    if canonical_mes:
        ex_lines = ['<voice_examples canonical="true">']
        for ex in canonical_mes:
            ex_lines.append(f"  {ex}")
        ex_lines.append("</voice_examples>")
        sections.append("\n".join(ex_lines))
        dbg_sections.append("voice_examples_canonical")
        if _debug is not None:
            _debug["mes_canonical_count"] = len(canonical_mes)

    # --- Never-would-say constraints (capped, prioritized, stripped) ---
    # Sort `CRITICAL:`-prefixed rules first, then cap at MAX_NEVER_RULES.
    # Strip inline WRONG/RIGHT/NOT/YES example blocks from each rule
    # (same priming problem as standalone WRONG examples).
    if ast.never_would_say:
        critical = [n for n in ast.never_would_say if n.text.upper().startswith("CRITICAL:")]
        regular = [n for n in ast.never_would_say if not n.text.upper().startswith("CRITICAL:")]
        prioritized = (critical + regular)[:MAX_NEVER_RULES]
        prioritized_text = [n.text for n in prioritized]
        stripped = [_strip_inline_examples(t) for t in prioritized_text]
        # Drop rules that became empty after stripping (authoring error).
        stripped = [s for s in stripped if s]
        never_lines = [f"  - {n}" for n in stripped]
        sections.append("<never>\n" + "\n".join(never_lines) + "\n</never>")
        dbg_sections.append("never")
        if _debug is not None:
            _debug["never_total"] = len(ast.never_would_say)
            _debug["never_rendered"] = len(stripped)
            _debug["never_dropped"] = len(ast.never_would_say) - len(stripped)
            _debug["never_critical_count"] = len(critical)
            _debug["never_inline_examples_stripped"] = sum(
                1 for p, s in zip(prioritized_text, stripped) if p != s
            )

    # --- Reasoning tests (contextual quality checks) ---
    if ast.tests:
        capped = ast.tests[:MAX_TESTS]
        test_lines = ["<tests>"]
        for t in capped:
            dim_attr = f' dimension="{t.dimension}"' if t.dimension else ""
            test_lines.append(f'  <test name="{t.name}"{dim_attr}>')
            test_lines.append(f"    <question>{t.question}</question>")
            for f in t.fail_examples:
                test_lines.append(f"    <fail>{f}</fail>")
            for p in t.pass_examples:
                test_lines.append(f"    <pass>{p}</pass>")
            if t.why:
                test_lines.append(f"    <why>{t.why}</why>")
            test_lines.append("  </test>")
        test_lines.append("</tests>")
        sections.append("\n".join(test_lines))
        dbg_sections.append("tests")
        if _debug is not None:
            _debug["tests_total"] = len(ast.tests)
            _debug["tests_rendered"] = len(capped)

    # --- Observable quirks ---
    if ast.quirks:
        quirk_lines = [f"  - {q}" for q in ast.quirks]
        sections.append("<quirks>\n" + "\n".join(quirk_lines) + "\n</quirks>")
        dbg_sections.append("quirks")

    # --- Props (concrete domain objects to reach for) ---
    if ast.props:
        sections.append("<props>\n  " + " ".join(ast.props) + "\n</props>")
        dbg_sections.append("props")

    # --- NPC-to-NPC relationships ---
    if ast.relationships:
        rel_lines = []
        for rel in ast.relationships:
            rel_lines.append(
                f'  <rel target="{rel.target}" type="{rel.rel_type}" '
                f'intensity="{rel.intensity:.1f}">{rel.notes}</rel>'
            )
        sections.append("<relationships>\n" + "\n".join(rel_lines) + "\n</relationships>")
        dbg_sections.append("relationships")
        if _debug is not None:
            _debug["relationships_count"] = len(ast.relationships)

    # --- Behavioral traits (PList) ---
    if ast.traits:
        sections.append(f"<traits>{', '.join(ast.traits)}</traits>")
        dbg_sections.append("traits")

    # --- Drivermap profile (structured motivation) ---
    if ast.drivermap and ast.drivermap.profile:
        compressed = _compress_drivermap_profile(ast.drivermap.profile)
        if compressed:
            sections.append(f"<drivermap>{compressed}</drivermap>")
            dbg_sections.append("drivermap")

    if _debug is not None:
        _debug["sections"] = dbg_sections

    return "\n\n".join(sections)


def build_dynamic_state(
    ast: CharacterAST,
    *,
    trust: float = 0.0,
    known_facts: set[str] | None = None,
    turn: int = 0,
    state_vars: dict[str, float] | None = None,
    uncertain: bool = False,
    voice_reminder_override: str | None = None,
    _debug: dict | None = None,
) -> str:
    """Per-turn state context — recomputed every generation call.

    This section depends on trust, turn, known_facts, and state_vars.
    It must come after build_static_context() in the final prompt so
    the static prefix remains cache-eligible.

    Args:
        uncertain: When True, emit <uncertainty_voice> examples. The
            caller should set this when the player input has question/
            hedge signals the character won't confidently answer.
        _debug: Optional dict; when provided, populated with per-section
            observability data. Public callers should use
            build_dialogue_context_debug rather than passing _debug.
    """
    known_facts = known_facts or set()
    state_vars = state_vars or {}
    sections: list[str] = []
    dbg_sections: list[str] = []

    # --- Arc phase ---
    phase = resolve_arc_phase(ast, trust, known_facts=known_facts, state_vars=state_vars)
    if phase:
        phase_lines = [f'<arc_phase name="{phase.name}">']
        if phase.voice:
            phase_lines.append(f"  <voice_shift>{phase.voice}</voice_shift>")
        phase_lines.append("</arc_phase>")
        sections.append("\n".join(phase_lines))
        dbg_sections.append("arc_phase")
        if _debug is not None:
            _debug["arc_phase"] = phase.name
            _debug["arc_condition"] = phase.condition_str

    # --- Active goals (spliced with goal_behaviors when available) ---
    goals = resolve_active_goals(ast, trust, known_facts=known_facts, state_vars=state_vars)
    active_goals = [g for g in goals if g["active"]]
    if active_goals:
        goal_lines = ["<active_goals>"]
        rendered_goals: list[dict] = []
        for g in active_goals[:3]:
            attrs = f'weight="{g["weight"]:.1f}"'
            if g.get("grows_with"):
                attrs += f' grows_with="{g["grows_with"]}"'
            name = g["name"]
            behavior = ast.goal_behaviors.get(name, "")
            if behavior:
                goal_lines.append(
                    f'  <goal {attrs} name="{name}">{behavior}</goal>'
                )
            else:
                goal_lines.append(f'  <goal {attrs} name="{name}"/>')
            rendered_goals.append(
                {"name": name, "weight": g["weight"], "has_behavior": bool(behavior)}
            )
        goal_lines.append("</active_goals>")
        sections.append("\n".join(goal_lines))
        dbg_sections.append("active_goals")
        if _debug is not None:
            _debug["active_goals"] = rendered_goals

    # --- Rotating voice examples (trust-gated, turn-rotated) ---
    rotating_mes = select_rotating_mes(ast, turn, trust=trust)
    if rotating_mes:
        ex_lines = ['<voice_examples rotating="true">']
        for ex in rotating_mes:
            ex_lines.append(f"  {ex}")
        ex_lines.append("</voice_examples>")
        sections.append("\n".join(ex_lines))
        dbg_sections.append("voice_examples_rotating")
        if _debug is not None:
            _debug["mes_rotating_count"] = len(rotating_mes)

    # --- Uncertainty voice (opt-in via kwarg) ---
    if uncertain and ast.uncertainty_voice:
        unc_lines = ["<uncertainty_voice>"]
        for ex in ast.uncertainty_voice:
            unc_lines.append(f"  {ex}")
        unc_lines.append("</uncertainty_voice>")
        sections.append("\n".join(unc_lines))
        dbg_sections.append("uncertainty_voice")

    # --- Voice reminder (sandwich: last thing before generation) ---
    # Counters lost-in-the-middle. Swaps to voice.peak when peak_when
    # condition evaluates true; otherwise uses kernel. Override wins.
    if voice_reminder_override:
        sections.append(f"<voice_reminder>{voice_reminder_override}</voice_reminder>")
        dbg_sections.append("voice_reminder")
        if _debug is not None:
            _debug["voice_reminder_peak"] = False
            _debug["voice_reminder_override"] = True
    elif ast.voice and ast.voice.kernel:
        active_voice = ast.voice.kernel
        peak_active = False
        if ast.voice.peak and ast.voice.peak_when:
            char_id = getattr(ast, "char_id", "") or getattr(ast, "name", "") or ""
            if _evaluate_condition_string(
                ast.voice.peak_when,
                trust,
                state_vars,
                known_facts,
                char_id=char_id,
            ):
                active_voice = ast.voice.peak
                peak_active = True
        attr = ' peak="true"' if peak_active else ""
        sections.append(f"<voice_reminder{attr}>{active_voice}</voice_reminder>")
        dbg_sections.append("voice_reminder")
        if _debug is not None:
            _debug["voice_reminder_peak"] = peak_active

    if _debug is not None:
        _debug["sections"] = dbg_sections
        _debug["uncertain"] = uncertain
        _debug["trust"] = trust
        _debug["turn"] = turn
        _debug["state_vars"] = dict(state_vars)

    return "\n\n".join(sections)


def build_dialogue_context(
    ast: CharacterAST,
    trust: float = 0.0,
    known_facts: set[str] | None = None,
    turn: int = 0,
    state_vars: dict[str, float] | None = None,
    uncertain: bool = False,
    *,
    voice_override: str | None = None,
    suppress_peak: bool = True,
    mes_override: list[str] | None = None,
    voice_reminder_override: str | None = None,
) -> str:
    """Build the complete effigy context for injection into a dialogue system.

    Thin wrapper over build_static_context() + build_dynamic_state().
    Callers wanting prompt-cache wins should invoke the two builders
    separately and place the cache boundary between them.

    Returns a string ready for injection into the narrator/dialogue prompt.
    Returns "" if the AST has no meaningful data to contribute.

    Args:
        trust: Trust level (0.0-1.0).
        known_facts: Set of fact IDs the player has discovered.
        turn: Current turn number (for MES example rotation).
        state_vars: Arbitrary numeric state variables (e.g., {"ruin": 4}).
        uncertain: When True, emit uncertainty-voice examples (opt-in).
    """
    filtered = filter_ast_by_state(
        ast, trust, state_vars=state_vars, known_facts=known_facts
    )
    static = build_static_context(
        filtered,
        voice_override=voice_override,
        suppress_peak=suppress_peak,
        mes_override=mes_override,
    )
    dynamic = build_dynamic_state(
        filtered,
        trust=trust,
        known_facts=known_facts,
        turn=turn,
        state_vars=state_vars,
        uncertain=uncertain,
        voice_reminder_override=voice_reminder_override,
    )
    parts = [p for p in (static, dynamic) if p]
    return "\n\n".join(parts)


def build_dialogue_context_debug(
    ast: CharacterAST,
    trust: float = 0.0,
    known_facts: set[str] | None = None,
    turn: int = 0,
    state_vars: dict[str, float] | None = None,
    uncertain: bool = False,
    *,
    voice_override: str | None = None,
    suppress_peak: bool = True,
    mes_override: list[str] | None = None,
    voice_reminder_override: str | None = None,
) -> tuple[str, dict]:
    """Build the dialogue context AND return a debug dict of what went in.

    Returns (context_string, debug_dict). The debug dict has a stable
    schema:

    ``{
        "static": {
            "sections": [...], "voice_kernel_chars": N, "has_peak": bool,
            "has_peak_when": bool, "voice_override": bool,
            "mes_canonical_count": N,
            "never_total": N, "never_rendered": N, "never_dropped": N,
            "never_critical_count": N, "relationships_count": N,
        },
        "dynamic": {
            "sections": [...], "arc_phase": str, "arc_condition": str,
            "active_goals": [{"name", "weight", "has_behavior"}],
            "mes_rotating_count": N, "voice_reminder_peak": bool,
            "voice_reminder_override": bool,
            "uncertain": bool, "trust": float, "turn": int,
            "state_vars": dict,
        },
        "when_filtered_mes": N, "when_filtered_never": N,
        "when_filtered_wrong": N, "when_filtered_tests": N,
        "total_chars": N, "static_chars": N, "dynamic_chars": N,
    }``

    Callers should log the debug dict alongside the generation call to
    enable post-hoc analysis of which context configurations produce
    the best voice adherence.
    """
    debug: dict = {"static": {}, "dynamic": {}}
    filtered = filter_ast_by_state(
        ast, trust, state_vars=state_vars, known_facts=known_facts
    )
    debug["when_filtered_mes"] = len(ast.mes_examples) - len(filtered.mes_examples)
    debug["when_filtered_never"] = len(ast.never_would_say) - len(filtered.never_would_say)
    debug["when_filtered_wrong"] = len(ast.wrong_examples) - len(filtered.wrong_examples)
    debug["when_filtered_tests"] = len(ast.tests) - len(filtered.tests)
    static = build_static_context(
        filtered,
        voice_override=voice_override,
        suppress_peak=suppress_peak,
        mes_override=mes_override,
        _debug=debug["static"],
    )
    dynamic = build_dynamic_state(
        filtered,
        trust=trust,
        known_facts=known_facts,
        turn=turn,
        state_vars=state_vars,
        uncertain=uncertain,
        voice_reminder_override=voice_reminder_override,
        _debug=debug["dynamic"],
    )
    parts = [p for p in (static, dynamic) if p]
    ctx = "\n\n".join(parts)
    debug["total_chars"] = len(ctx)
    debug["static_chars"] = len(static)
    debug["dynamic_chars"] = len(dynamic)
    return ctx, debug


def get_wrong_examples(ast: CharacterAST) -> list[dict[str, str]]:
    """Return WRONG examples for eval/reference use.

    WRONG examples are anti-patterns used by eval judges to score
    NPC voice quality. They are intentionally excluded from
    build_dialogue_context() because they prime LLMs to reproduce
    the exact patterns they illustrate.
    """
    results: list[dict[str, str]] = []
    for we in ast.wrong_examples:
        entry: dict[str, str] = {"wrong": we.wrong}
        if we.right:
            entry["right"] = we.right
        if we.why:
            entry["why"] = we.why
        if we.beat:
            entry["beat"] = we.beat
        results.append(entry)
    return results


def get_tests(ast: CharacterAST) -> list[dict[str, Any]]:
    """Return TEST entries for eval/reference use."""
    results: list[dict[str, Any]] = []
    for t in ast.tests:
        entry: dict[str, Any] = {"name": t.name, "question": t.question}
        if t.dimension:
            entry["dimension"] = t.dimension
        if t.fail_examples:
            entry["fail_examples"] = list(t.fail_examples)
        if t.pass_examples:
            entry["pass_examples"] = list(t.pass_examples)
        if t.why:
            entry["why"] = t.why
        if t.beat:
            entry["beat"] = t.beat
        results.append(entry)
    return results


def get_arc_phase_dict(
    ast: CharacterAST,
    trust: float = 0.0,
    known_facts: set[str] | None = None,
    state_vars: dict[str, float] | None = None,
) -> dict | None:
    """Return the current arc phase as a dict.

    Returns None if no arc phases defined or none satisfied.
    Returns {name, voice, conditions} dict.
    """
    known_facts = known_facts or set()
    phase = resolve_arc_phase(ast, trust, known_facts=known_facts, state_vars=state_vars)
    if phase is None:
        return None
    result = {
        "name": phase.name,
        "voice": phase.voice,
        "conditions": phase.conditions,
    }
    if phase.deflection:
        result["deflection"] = phase.deflection
    return result
