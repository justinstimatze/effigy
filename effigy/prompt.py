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


def build_dialogue_context(
    ast: CharacterAST,
    trust: float = 0.0,
    known_facts: set[str] | None = None,
    turn: int = 0,
    state_vars: dict[str, float] | None = None,
) -> str:
    """Build the complete effigy context for injection into a dialogue system.

    Returns a string ready for injection into the narrator/dialogue prompt.
    Returns "" if the AST has no meaningful data to contribute.

    Args:
        trust: Trust level (0.0-1.0).
        known_facts: Set of fact IDs the player has discovered.
        turn: Current turn number (for MES example rotation).
        state_vars: Arbitrary numeric state variables (e.g., {"ruin": 4}).
    """
    known_facts = known_facts or set()
    sections: list[str] = []

    # --- Arc phase ---
    phase = resolve_arc_phase(ast, trust, known_facts=known_facts, state_vars=state_vars)
    if phase:
        phase_section = f"CHARACTER ARC PHASE: {phase.name.upper()}"
        if phase.voice:
            phase_section += f"\nVoice shift: {phase.voice}"
        sections.append(phase_section)

    # --- Active goals ---
    goals = resolve_active_goals(ast, trust, known_facts=known_facts, state_vars=state_vars)
    active_goals = [g for g in goals if g["active"]]
    if active_goals:
        goal_lines = []
        for g in active_goals[:3]:
            line = f"  - {g['name']} (priority: {g['weight']:.1f}"
            if g.get("grows_with"):
                line += f", grows with {g['grows_with']}"
            line += ")"
            goal_lines.append(line)
        sections.append(
            "ACTIVE GOALS (what this character is trying to accomplish):\n" + "\n".join(goal_lines)
        )

    # --- NPC-to-NPC relationships ---
    if ast.relationships:
        rel_lines = []
        for rel in ast.relationships:
            rel_lines.append(
                f"  - {rel.target}: {rel.rel_type} ({rel.intensity:.1f}) -- {rel.notes}"
            )
        sections.append(
            "RELATIONSHIPS (how this character feels about other NPCs):\n" + "\n".join(rel_lines)
        )

    # --- Behavioral traits (PList) ---
    if ast.traits:
        sections.append(f"BEHAVIORAL TRAITS: {', '.join(ast.traits)}")

    # --- Voice reinforcement (drift prevention) ---
    if ast.voice and ast.voice.kernel:
        sections.append(f"VOICE REINFORCEMENT: {ast.voice.kernel}")

    # --- Never-would-say constraints ---
    if ast.never_would_say:
        never_lines = [f"  - {n}" for n in ast.never_would_say]
        sections.append("NEVER (this character would NEVER):\n" + "\n".join(never_lines))

    # --- Observable quirks ---
    if ast.quirks:
        quirk_lines = [f"  - {q}" for q in ast.quirks]
        sections.append("BEHAVIORAL QUIRKS:\n" + "\n".join(quirk_lines))

    # --- Props (concrete domain objects to reach for) ---
    if ast.props:
        sections.append(
            "PROPS (concrete objects this character can reference -- "
            "use naturally, do NOT list or info-dump):\n" + "  " + " ".join(ast.props)
        )

    # --- Wrong examples (anti-patterns) ---
    if ast.wrong_examples:
        wrong_lines = []
        for we in ast.wrong_examples:
            entry = f'  WRONG: "{we.wrong}"'
            if we.right:
                entry += f'\n  RIGHT: "{we.right}"'
            if we.why:
                entry += f"\n  WHY: {we.why}"
            wrong_lines.append(entry)
        sections.append(
            "DO NOT generate dialogue like these examples:\n" + "\n  ---\n".join(wrong_lines)
        )

    # --- Thematic representation ---
    if ast.theme:
        sections.append(f"THEMATIC ROLE: {ast.theme}")

    if not sections:
        return ""

    return "\n\n".join(sections)


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
