"""Effigy Layer 3 — Dynamic profile evolution.

Prior art tracks trust and gossip mechanically; Layer 3 adds:

1. Arc phase transitions (deterministic, checked every turn)
2. Multi-axis emotional state (fear, guilt, curiosity — derived from world state)
3. NPC intentions / active plans (GOAP-compatible)
4. Memory synthesis (periodic LLM calls, gated by turn count)

Zero LLM calls for items 1-3. Item 4 uses a lightweight model, gated behind
turn thresholds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from effigy.notation import CharacterAST
from effigy.prompt import resolve_active_goals

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Emotional axes — derived from world state, no LLM
# ---------------------------------------------------------------------------

@dataclass
class EmotionalState:
    """Multi-axis emotional state for an NPC.

    Each axis is 0.0-1.0. Derived from world state, not tracked directly.
    These modulate dialogue tone without the LLM needing explicit instructions
    for each combination — the NPC's emotional state is described in
    natural language and injected into the prompt.
    """
    fear: float = 0.0
    guilt: float = 0.0
    curiosity: float = 0.0
    grief: float = 0.0
    resolve: float = 0.0


# Example archetype-to-sensitivity mappings from an investigation/mystery game.
# Override with your own via the ``archetype_sensitivities`` parameter on
# ``compute_emotional_state`` and ``build_evolution_context``.
#
# Keys are archetype IDs matching the ``@arch`` field in .effigy files.
# Values map emotional axes to the sensitivity input names that feed them.
# Input names must match keys in the ``emotional_inputs`` dict.
EXAMPLE_ARCHETYPE_SENSITIVITIES: dict[str, dict[str, list[str]]] = {
    "ca_guardian": {
        "fear": ["instability", "exposure"],
        "guilt": ["exposure", "evidence"],
        "resolve": ["trust"],
    },
    "ca_faded_glory": {
        "grief": ["loss_facts"],
        "curiosity": ["player_sharing"],
        "resolve": ["trust", "evidence"],
    },
    "ca_witness": {
        "fear": ["instability"],
        "curiosity": ["player_sharing", "evidence"],
        "grief": ["loss_facts"],
    },
    "ca_truth_seeker": {
        "curiosity": ["evidence", "player_sharing"],
        "fear": ["instability"],
        "resolve": ["trust", "evidence"],
    },
    "ca_keeper": {
        "fear": ["exposure", "instability"],
        "guilt": ["exposure"],
        "resolve": ["trust"],
    },
    "ca_outsider": {
        "curiosity": ["evidence", "player_sharing"],
        "fear": ["instability"],
    },
    "ca_moral_compass": {
        "guilt": ["exposure", "evidence"],
        "fear": ["instability"],
        "resolve": ["trust"],
    },
    "ca_inheritor": {
        "curiosity": ["evidence", "player_sharing"],
        "grief": ["loss_facts"],
        "fear": ["instability"],
    },
}

DEFAULT_LOSS_KEYWORDS = frozenset({"death", "die", "kill", "collapse"})


def compute_emotional_state(
    ast: CharacterAST,
    trust: float = 0.0,
    *,
    known_facts: set[str] | None = None,
    emotional_inputs: dict[str, float] | None = None,
    loss_keywords: set[str] | None = None,
    archetype_sensitivities: dict[str, dict[str, list[str]]] | None = None,
) -> EmotionalState:
    """Derive emotional state from world state and archetype sensitivities.

    No LLM calls. Pure computation.

    Args:
        trust: Trust level (0.0-1.0).
        known_facts: Set of fact IDs the player has discovered.
        emotional_inputs: Pre-normalized 0-1 values keyed by sensitivity
            input names (e.g., ``{"instability": 0.8, "exposure": 0.75}``).
        loss_keywords: Substrings to match against known_facts for grief.
            Defaults to generic keywords: death, die, kill, collapse.
        archetype_sensitivities: Override the example sensitivity mappings.
    """
    known_facts = known_facts or set()
    _loss_kw = loss_keywords if loss_keywords is not None else DEFAULT_LOSS_KEYWORDS
    sensitivities = (archetype_sensitivities or EXAMPLE_ARCHETYPE_SENSITIVITIES).get(
        ast.archetype, {}
    )

    state = EmotionalState()

    # Loss-related facts count
    loss_facts = sum(1 for f in known_facts if any(kw in f for kw in _loss_kw))
    loss_factor = min(1.0, loss_facts / 5.0)

    # Evidence accumulation
    evidence_factor = min(1.0, len(known_facts) / 15.0)

    # Build the unified input values dict
    input_values: dict[str, float] = {
        "trust": trust,
        "loss_facts": loss_factor,
        "evidence": evidence_factor,
    }
    input_values.update(emotional_inputs or {})

    # Compute each axis based on archetype sensitivities
    for axis in ("fear", "guilt", "curiosity", "grief", "resolve"):
        inputs = sensitivities.get(axis, [])
        if not inputs:
            continue
        value = max((input_values.get(inp, 0.0) for inp in inputs), default=0.0)
        setattr(state, axis, round(value, 2))

    return state


def emotional_context(state: EmotionalState, name: str) -> str:
    """Generate natural-language emotional context for prompt injection.

    Only includes axes above threshold (0.2) to avoid noise.
    Returns "" if all axes are below threshold.
    """
    THRESHOLD = 0.2
    lines: list[str] = []

    if state.fear > THRESHOLD:
        if state.fear > 0.6:
            lines.append("deeply unsettled — fight-or-flight proximity")
        else:
            lines.append("on edge, watchful")

    if state.guilt > THRESHOLD:
        if state.guilt > 0.6:
            lines.append("the weight of complicity is becoming unbearable")
        else:
            lines.append("uneasy conscience, deflecting")

    if state.curiosity > THRESHOLD:
        if state.curiosity > 0.6:
            lines.append("intensely engaged — leaning in, asking questions")
        else:
            lines.append("interested, cautiously drawn in")

    if state.grief > THRESHOLD:
        if state.grief > 0.6:
            lines.append("grief is surfacing — pauses, physical stillness")
        else:
            lines.append("touching on old wounds, careful")

    if state.resolve > THRESHOLD:
        if state.resolve > 0.6:
            lines.append("decided — clarity replacing ambiguity")
        else:
            lines.append("building toward a decision")

    if not lines:
        return ""

    return (
        f"EMOTIONAL STATE ({name}, internal — shapes tone, not stated):\n"
        + "\n".join(f"  - {line}" for line in lines)
    )


# ---------------------------------------------------------------------------
# GOAP-compatible intention layer
# ---------------------------------------------------------------------------

@dataclass
class ActiveIntention:
    """A currently active NPC intention (plan)."""
    goal_name: str
    weight: float
    description: str = ""


def compute_intentions(
    ast: CharacterAST,
    trust: float = 0.0,
    *,
    known_facts: set[str] | None = None,
    state_vars: dict[str, float] | None = None,
) -> list[ActiveIntention]:
    """Determine which goals have become active intentions.

    An intention activates when its goal weight crosses 0.6.
    This is the GOAP-compatible layer — intentions feed the
    NPC's observable behavior.
    """
    known_facts = known_facts or set()
    goals = resolve_active_goals(ast, trust, known_facts=known_facts, state_vars=state_vars)
    intentions = []
    for g in goals:
        if g["active"]:
            intentions.append(ActiveIntention(
                goal_name=g["name"],
                weight=g["weight"],
            ))
    return intentions


def intentions_context(intentions: list[ActiveIntention], name: str) -> str:
    """Generate prompt context for active intentions.

    Returns "" if no active intentions.
    """
    if not intentions:
        return ""

    lines = [f"  - {i.goal_name} (priority: {i.weight:.1f})"
             for i in intentions[:3]]
    return (
        f"ACTIVE INTENTIONS ({name} is currently trying to):\n"
        + "\n".join(lines)
    )


# ---------------------------------------------------------------------------
# Memory synthesis (periodic LLM calls — Layer 3's expensive operation)
# ---------------------------------------------------------------------------

SYNTHESIS_INTERVAL = 10


@dataclass
class NpcMemoryState:
    """Accumulated NPC state for memory synthesis."""
    char_id: str
    last_synthesis_turn: int = 0
    synthesis_text: str = ""
    interaction_count: int = 0
    topics_discussed: list[str] = field(default_factory=list)


def should_synthesize(state: NpcMemoryState, current_turn: int) -> bool:
    """Check if it's time for a new memory synthesis."""
    if state.interaction_count == 0:
        return False
    return (current_turn - state.last_synthesis_turn) >= SYNTHESIS_INTERVAL


def build_synthesis_prompt(
    ast: CharacterAST,
    memory_state: NpcMemoryState,
    trust: float = 0.0,
    *,
    known_facts: set[str] | None = None,
    arc_phase_name: str = "",
    state_vars: dict[str, float] | None = None,
    protagonist_label: str = "the player",
    narrative_context: str = "",
) -> tuple[str, str]:
    """Build system + user prompts for memory synthesis.

    Returns (system_prompt, user_prompt) for an LLM call.
    The synthesis generates a paragraph capturing the NPC's current
    understanding of the situation and the other characters.

    Args:
        protagonist_label: How to refer to the player character
            (e.g., "the stranger", "the detective").
        narrative_context: Optional description of what the NPC should
            reflect on (e.g., "how they feel about the investigation").
    """
    system = (
        "You are a narrative state summarizer for a story-driven game. "
        "Generate a single paragraph (3-5 sentences) capturing an NPC's "
        "current internal state. Be specific about what they know, what "
        "they feel, and what they're thinking about. Do not editorialize. "
        "Write in third person present tense."
    )

    known_facts = known_facts or set()
    facts_str = ", ".join(sorted(known_facts)[:10]) if known_facts else "nothing specific"
    topics_str = ", ".join(memory_state.topics_discussed[-5:]) if memory_state.topics_discussed else "nothing"

    state_line = ""
    if state_vars:
        state_parts = [f"{k}: {v}" for k, v in state_vars.items()]
        state_line = f"State variables: {', '.join(state_parts)}\n"

    reflect_on = f", {narrative_context}" if narrative_context else ", and what they are considering doing next"

    user = (
        f"NPC: {ast.name} ({ast.role})\n"
        f"Voice: {ast.voice.kernel if ast.voice else 'unknown'}\n"
        f"Trust level: {trust:.2f} (disposition: "
        f"{'hostile' if trust < -0.1 else 'wary' if trust < 0.2 else 'neutral' if trust < 0.35 else 'warming' if trust < 0.5 else 'warm' if trust < 0.65 else 'confiding'})\n"
        f"{state_line}"
        f"Arc phase: {arc_phase_name or 'none defined'}\n"
        f"Interactions with {protagonist_label}: {memory_state.interaction_count}\n"
        f"Topics discussed: {topics_str}\n"
        f"Player knows: {facts_str}\n\n"
        f"Generate a paragraph describing {ast.name}'s current internal state — "
        f"what they know about {protagonist_label}{reflect_on}."
    )

    return system, user


def synthesize_memory(
    ast: CharacterAST,
    memory_state: NpcMemoryState,
    trust: float = 0.0,
    *,
    known_facts: set[str] | None = None,
    arc_phase_name: str = "",
    state_vars: dict[str, float] | None = None,
    protagonist_label: str = "the player",
    narrative_context: str = "",
    call_fn: Any = None,
    model: str = "claude-haiku-4-5-20251001",
) -> str:
    """Generate a memory synthesis paragraph.

    Uses the provided call_fn(system, user, model) -> str for the LLM call.
    If call_fn is None, returns "" (synthesis disabled).

    This is the ONLY place in effigy where LLM calls happen.
    """
    if call_fn is None:
        return ""

    system, user = build_synthesis_prompt(
        ast, memory_state, trust,
        known_facts=known_facts,
        arc_phase_name=arc_phase_name,
        state_vars=state_vars,
        protagonist_label=protagonist_label,
        narrative_context=narrative_context,
    )

    try:
        result = call_fn(system, user, model)
        return result.strip() if isinstance(result, str) else ""
    except Exception as exc:
        logger.warning("Memory synthesis failed for %s: %s", ast.char_id, exc)
        return ""


# ---------------------------------------------------------------------------
# Full evolution context (combines all Layer 3 outputs)
# ---------------------------------------------------------------------------

def build_evolution_context(
    ast: CharacterAST,
    trust: float = 0.0,
    *,
    known_facts: set[str] | None = None,
    state_vars: dict[str, float] | None = None,
    emotional_inputs: dict[str, float] | None = None,
    synthesis_text: str = "",
    archetype_sensitivities: dict[str, dict[str, list[str]]] | None = None,
    loss_keywords: set[str] | None = None,
) -> str:
    """Build the complete Layer 3 evolution context.

    Combines emotional state, intentions, and memory synthesis
    into a single string for prompt injection.

    No LLM calls — synthesis_text must be pre-generated and passed in.
    """
    known_facts = known_facts or set()
    sections: list[str] = []

    name = ast.name or ast.char_id

    # Emotional state
    emo_state = compute_emotional_state(
        ast, trust,
        known_facts=known_facts,
        emotional_inputs=emotional_inputs,
        archetype_sensitivities=archetype_sensitivities,
        loss_keywords=loss_keywords,
    )
    emo_ctx = emotional_context(emo_state, name)
    if emo_ctx:
        sections.append(emo_ctx)

    # Intentions
    intentions = compute_intentions(ast, trust, known_facts=known_facts, state_vars=state_vars)
    int_ctx = intentions_context(intentions, name)
    if int_ctx:
        sections.append(int_ctx)

    # Memory synthesis (pre-generated)
    if synthesis_text:
        sections.append(
            f"NPC STATE SUMMARY ({name}):\n{synthesis_text}"
        )

    if not sections:
        return ""

    return "\n\n".join(sections)
