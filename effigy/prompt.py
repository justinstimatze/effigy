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
from typing import Any

from effigy.notation import (
    ArcPhaseAST,
    CharacterAST,
)

logger = logging.getLogger(__name__)

# Max NEVER rules surfaced in generation context. LLMs reliably attend
# to ~5-7 constraints during generation; beyond that, adding rules makes
# ALL rules less effective (constraint saturation).
MAX_NEVER_RULES = 7

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


def _conditions_met(
    phase: ArcPhaseAST,
    trust: float,
    state_vars: dict[str, float],
    known_facts: set[str],
    char_id: str = "",
) -> bool:
    """Check if all conditions in a phase gate are satisfied.

    Uses an optional external condition evaluator when available, falls back
    to legacy dict-based evaluation otherwise.
    """
    # Try unified condition DSL first
    cond_str = phase.condition_str
    if cond_str and _HAS_CONDITIONS:
        resolved = cond_str.replace("_NPC_", char_id) if char_id else cond_str
        try:
            state = _EffigyConditionState(trust, state_vars, known_facts, char_id)
            return _cond_evaluate(resolved, state)
        except (ConditionParseError, Exception):
            logger.debug("Condition parse failed for %r, falling back to legacy", resolved)

    # Legacy dict-based evaluation
    conditions = phase.conditions
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
    """Evaluate a standalone condition DSL string (e.g., voice.peak_when).

    Returns False when the external conditions library is unavailable or
    the condition fails to parse. Used for non-arc-phase conditions that
    need to reuse the same DSL.
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
            logger.debug("condition string parse failed for %r", resolved)
            return False
    return False


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


def build_static_context(ast: CharacterAST) -> str:
    """Turn-invariant character definition — the cacheable prefix.

    This section depends only on the parsed AST. No trust, no turn, no
    state_vars. The output is byte-stable for a given AST, making it
    eligible for prompt prefix caching (Anthropic, OpenAI, etc.).

    Sections are ordered strongest-signal-first for attention:
    voice → NEVER → quirks → props → relationships → traits.
    """
    sections: list[str] = []

    # --- Presence note (brief physical/mood opener) ---
    if ast.presence_note:
        sections.append(f"<presence>{ast.presence_note}</presence>")

    # --- Voice (kernel + optional peak + peak_when condition) ---
    if ast.voice and ast.voice.kernel:
        voice_lines = ["<voice>", f"  <kernel>{ast.voice.kernel}</kernel>"]
        if ast.voice.peak:
            peak_attr = ""
            if ast.voice.peak_when:
                peak_attr = f' when="{ast.voice.peak_when}"'
            voice_lines.append(f"  <peak{peak_attr}>{ast.voice.peak}</peak>")
        voice_lines.append("</voice>")
        sections.append("\n".join(voice_lines))

    # --- Canonical voice examples (cache-stable MES slice) ---
    canonical_mes = select_canonical_mes(ast)
    if canonical_mes:
        ex_lines = ['<voice_examples canonical="true">']
        for ex in canonical_mes:
            ex_lines.append(f"  {ex}")
        ex_lines.append("</voice_examples>")
        sections.append("\n".join(ex_lines))

    # --- Never-would-say constraints (capped, prioritized) ---
    # Sort `CRITICAL:`-prefixed rules first, then cap at MAX_NEVER_RULES.
    if ast.never_would_say:
        critical = [n for n in ast.never_would_say if n.upper().startswith("CRITICAL:")]
        regular = [n for n in ast.never_would_say if not n.upper().startswith("CRITICAL:")]
        prioritized = (critical + regular)[:MAX_NEVER_RULES]
        never_lines = [f"  - {n}" for n in prioritized]
        sections.append("<never>\n" + "\n".join(never_lines) + "\n</never>")

    # --- Observable quirks ---
    if ast.quirks:
        quirk_lines = [f"  - {q}" for q in ast.quirks]
        sections.append("<quirks>\n" + "\n".join(quirk_lines) + "\n</quirks>")

    # --- Props (concrete domain objects to reach for) ---
    if ast.props:
        sections.append("<props>\n  " + " ".join(ast.props) + "\n</props>")

    # --- NPC-to-NPC relationships ---
    if ast.relationships:
        rel_lines = []
        for rel in ast.relationships:
            rel_lines.append(
                f'  <rel target="{rel.target}" type="{rel.rel_type}" '
                f'intensity="{rel.intensity:.1f}">{rel.notes}</rel>'
            )
        sections.append("<relationships>\n" + "\n".join(rel_lines) + "\n</relationships>")

    # --- Behavioral traits (PList) ---
    if ast.traits:
        sections.append(f"<traits>{', '.join(ast.traits)}</traits>")

    # --- Drivermap profile (structured motivation) ---
    if ast.drivermap and ast.drivermap.profile:
        compressed = _compress_drivermap_profile(ast.drivermap.profile)
        if compressed:
            sections.append(f"<drivermap>{compressed}</drivermap>")

    return "\n\n".join(sections)


def build_dynamic_state(
    ast: CharacterAST,
    *,
    trust: float = 0.0,
    known_facts: set[str] | None = None,
    turn: int = 0,
    state_vars: dict[str, float] | None = None,
    uncertain: bool = False,
) -> str:
    """Per-turn state context — recomputed every generation call.

    This section depends on trust, turn, known_facts, and state_vars.
    It must come after build_static_context() in the final prompt so
    the static prefix remains cache-eligible.

    Args:
        uncertain: When True, emit <uncertainty_voice> examples. The
            caller should set this when the player input has question/
            hedge signals the character won't confidently answer.
    """
    known_facts = known_facts or set()
    state_vars = state_vars or {}
    sections: list[str] = []

    # --- Arc phase ---
    phase = resolve_arc_phase(ast, trust, known_facts=known_facts, state_vars=state_vars)
    if phase:
        phase_lines = [f'<arc_phase name="{phase.name}">']
        if phase.voice:
            phase_lines.append(f"  <voice_shift>{phase.voice}</voice_shift>")
        phase_lines.append("</arc_phase>")
        sections.append("\n".join(phase_lines))

    # --- Active goals (spliced with goal_behaviors when available) ---
    goals = resolve_active_goals(ast, trust, known_facts=known_facts, state_vars=state_vars)
    active_goals = [g for g in goals if g["active"]]
    if active_goals:
        goal_lines = ["<active_goals>"]
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
        goal_lines.append("</active_goals>")
        sections.append("\n".join(goal_lines))

    # --- Rotating voice examples (trust-gated, turn-rotated) ---
    rotating_mes = select_rotating_mes(ast, turn, trust=trust)
    if rotating_mes:
        ex_lines = ['<voice_examples rotating="true">']
        for ex in rotating_mes:
            ex_lines.append(f"  {ex}")
        ex_lines.append("</voice_examples>")
        sections.append("\n".join(ex_lines))

    # --- Uncertainty voice (opt-in via kwarg) ---
    if uncertain and ast.uncertainty_voice:
        unc_lines = ["<uncertainty_voice>"]
        for ex in ast.uncertainty_voice:
            unc_lines.append(f"  {ex}")
        unc_lines.append("</uncertainty_voice>")
        sections.append("\n".join(unc_lines))

    # --- Voice reminder (sandwich: last thing before generation) ---
    # Counters lost-in-the-middle. Swaps to voice.peak when peak_when
    # condition evaluates true; otherwise uses kernel.
    if ast.voice and ast.voice.kernel:
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

    return "\n\n".join(sections)


def build_dialogue_context(
    ast: CharacterAST,
    trust: float = 0.0,
    known_facts: set[str] | None = None,
    turn: int = 0,
    state_vars: dict[str, float] | None = None,
    uncertain: bool = False,
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
    static = build_static_context(ast)
    dynamic = build_dynamic_state(
        ast,
        trust=trust,
        known_facts=known_facts,
        turn=turn,
        state_vars=state_vars,
        uncertain=uncertain,
    )
    parts = [p for p in (static, dynamic) if p]
    return "\n\n".join(parts)


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
