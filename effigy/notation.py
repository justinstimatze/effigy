"""Effigy notation — dense personality dossier format.

Effigy sculpts a character from behavioral rules, voice patterns, quirks,
and constraints. The notation captures WHO the character IS — not raw data
fields, but the behavioral fingerprint an LLM needs to generate in-character
dialogue. Every token earns its place.

Block sigils (start a section):
    VOICE{...}      voice rules: kernel + peak + never-would-say
    NEVER[...]      behavioral constraints: what this character would NEVER do/say
    QUIRKS[...]     observable behaviors, physical tells, idiosyncratic habits
    MES[...]        key dialogue exemplars (curated, not exhaustive)
    UNC[...]        uncertainty voice examples
    ARC{...}        arc phases with gate conditions and voice shifts
    GOALS{...}      GOAP-compatible intention layer
    SECRETS[...]    tiered secrets with reveal conditions
    RELS{...}       relationship graph
    SCHED{...}      location schedule
    ERA[...]        era states (multi-era lifespan)
    DM{...}         drivermap profile + situation features
    ARRIVE[...]     arrival lines
    DEPART[...]     departure lines
    WRONG[...]      anti-pattern examples with corrections

Header fields (single-line, before blocks):
    @id             char_id
    @name           display name
    @role           role label
    @arch           archetype tag
    @narr           narrative_role (ally|suspect|antagonist|info_broker)
    @presence       presence_note
    @tropes         comma-separated trope tags
    @theme          thematic representation (what viewpoint on central themes)

Prose escape:
    §...§           verbatim prose (preserved exactly, not compressed)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NarrativeRole(Enum):
    NEUTRAL = "neutral"
    ALLY = "ally"
    SUSPECT = "suspect"
    ANTAGONIST = "antagonist"
    INFO_BROKER = "info_broker"
    MENTOR = "mentor"
    RIVAL = "rival"
    BYSTANDER = "bystander"


# ---------------------------------------------------------------------------
# AST nodes
# ---------------------------------------------------------------------------


@dataclass
class VoiceAST:
    """Voice kernel + optional peak voice + peak activation condition."""

    kernel: str  # the non-negotiable voice description
    peak: str = ""  # voice shift near emotional peaks
    peak_when: str = ""  # condition DSL string; when true, peak replaces kernel
    #                     in <voice_reminder>. Evaluated via resolve_condition.


@dataclass
class SecretAST:
    """A single secret with trust-gated visibility."""

    layer: int  # 1=easy (trust 0.2), 2=moderate (0.4), 3=deep (0.6)
    secret: str
    reveal_condition: str = ""
    related_era: str = ""


@dataclass
class RelationshipAST:
    """Directed relationship to another NPC."""

    target: str  # char_id
    rel_type: str  # suspects, trusts, cultivates, etc.
    intensity: float = 0.5  # 0.0-1.0
    notes: str = ""


@dataclass
class EraStateAST:
    """Character state in a specific era."""

    era_id: str  # game-defined era identifier (e.g., "past", "present")
    status: str = "alive"  # alive, unborn, dead
    age: int | None = None
    occupation: str = ""
    disposition: str = ""
    notes: str = ""


@dataclass
class ScheduleAST:
    """Location schedule across time slots."""

    morning: str | None = None
    afternoon: str | None = None
    evening: str | None = None
    night: str | None = None


@dataclass
class DrivermapAST:
    """Drivermap profile + situation features."""

    profile: dict[str, str] = field(default_factory=dict)  # trait → +/-/neutral
    situation_features: list[str] = field(default_factory=list)


@dataclass
class ArcPhaseAST:
    """A single arc phase with gate conditions and voice shift."""

    name: str  # guarded, thawing, vulnerable, resolved
    conditions: dict[str, Any] = field(default_factory=dict)  # legacy parsed dict
    condition_str: str = ""  # raw condition DSL string for conditions.evaluate()
    voice: str = ""  # voice shift description for this phase
    deflection: str = ""  # how to deflect unknown topics at this phase


@dataclass
class WrongExampleAST:
    """A WRONG/RIGHT anti-example pair for voice drift prevention."""

    context: str = ""  # {{user}} prompt that triggers the wrong response
    wrong: str = ""  # the wrong response (what NOT to generate)
    right: str = ""  # the correct response (what TO generate instead)
    why: str = ""  # explanation of why the wrong response is wrong
    when: str = ""  # condition DSL gate; empty or "*" = always active


@dataclass
class TestAST:
    """A named reasoning test for voice/behavior quality.

    Unlike NEVER rules (binary constraints) or WRONG examples (anti-patterns),
    tests give the LLM a reasoning framework: a question to ask about its own
    output, with fail/pass examples and an explanation of the underlying principle.
    """

    name: str
    question: str
    fail_examples: list[str] = field(default_factory=list)
    pass_examples: list[str] = field(default_factory=list)
    why: str = ""
    dimension: str = ""  # optional: "voice", "agency", "knowledge", etc.
    when: str = ""  # condition DSL gate; empty or "*" = always active


@dataclass
class PostProcRuleAST:
    """A deterministic post-processing rule for generation output.

    Applied by effigy.validators after the LLM produces a response, to
    enforce character voice rules that the prompt alone can't reliably
    enforce. Parsed from POSTPROC[...] blocks.
    """

    action: str = "warn"  # "reject" | "strip" | "warn"
    pattern: str = ""  # regex pattern (case-insensitive by default)
    why: str = ""  # human-readable rationale for the rule
    rule_id: str = ""  # optional stable identifier; auto-generated if empty


@dataclass
class NeverRuleAST:
    """A NEVER-would-say constraint with optional @when gating.

    The ``text`` is the rule string (what the character would never do or
    say). ``when`` is a condition DSL string (same grammar as ARC phase
    gates); empty or ``"*"`` means the rule is always active.

    The ``__str__`` method returns ``text`` so legacy f-string formatting
    still works. Callers reading the rule content should use ``.text``.
    """

    text: str
    when: str = ""

    def __str__(self) -> str:
        return self.text


@dataclass
class GoalAST:
    """A GOAP-compatible goal with weight."""

    name: str  # goal identifier
    weight: float = 0.5  # 0.0-1.0, can shift
    grows_with: str = ""  # what causes it to grow (trust, evidence, etc.)


@dataclass
class CharacterAST:
    """Complete character AST — the root node.

    Fields map to a standard character JSON schema.
    """

    # Header fields
    char_id: str = ""
    name: str = ""
    role: str = ""
    archetype: str = ""
    narrative_role: NarrativeRole = NarrativeRole.NEUTRAL
    presence_note: str = ""
    trope_tags: list[str] = field(default_factory=list)
    theme: str = ""  # thematic representation: what viewpoint on central themes

    # Voice
    voice: VoiceAST | None = None

    # Behavioral dossier (PList+Ali:Chat architecture)
    traits: list[str] = field(default_factory=list)  # PList-style compact behavioral rules
    never_would_say: list[NeverRuleAST] = field(default_factory=list)  # negative constraints
    quirks: list[str] = field(default_factory=list)  # observable behaviors, physical tells

    # Dialogue examples with trust-tier gating.
    # Each entry has .text and .tier ("low"/"moderate"/"high"/"any").
    # select_mes_examples() filters by current trust level.
    mes_examples: list = field(default_factory=list)  # list[parser.MESExample]
    uncertainty_voice: list[str] = field(default_factory=list)

    # Arc system (Layer 3)
    arc_phases: list[ArcPhaseAST] = field(default_factory=list)
    goals: list[GoalAST] = field(default_factory=list)

    # Goal behaviors: maps goal name -> behavioral description of what the
    # character does when that goal is active. Surfaces in intentions_context
    # so the LLM knows what an active intention looks like in practice.
    goal_behaviors: dict[str, str] = field(default_factory=dict)

    # Secrets
    secrets: list[SecretAST] = field(default_factory=list)

    # Relationships
    relationships: list[RelationshipAST] = field(default_factory=list)

    # Schedule
    schedule: ScheduleAST | None = None

    # Era states
    era_states: list[EraStateAST] = field(default_factory=list)

    # Drivermap
    drivermap: DrivermapAST | None = None

    # Wrong examples (anti-patterns for voice drift prevention)
    wrong_examples: list[WrongExampleAST] = field(default_factory=list)

    # Reasoning tests (contextual quality checks rendered into generation prompt)
    tests: list[TestAST] = field(default_factory=list)

    # Post-processing rules (deterministic filters applied to generation output)
    post_processors: list[PostProcRuleAST] = field(default_factory=list)

    # Props -- concrete domain objects the character can reference
    props: list[str] = field(default_factory=list)

    # Arrival/departure lines
    arrival_lines: list[str] = field(default_factory=list)
    departure_lines: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Block keywords (used by parser to dispatch)
# ---------------------------------------------------------------------------

BLOCK_KEYWORDS = {
    "VOICE",
    "TRAITS",
    "NEVER",
    "QUIRKS",
    "MES",
    "UNC",
    "ARC",
    "GOALS",
    "SECRETS",
    "RELS",
    "SCHED",
    "ERA",
    "DM",
    "ARRIVE",
    "DEPART",
    "WRONG",
    "TEST",
    "PROPS",
    "BEHAVIORS",
    "POSTPROC",
}

HEADER_PREFIXES = {
    "@id": "char_id",
    "@name": "name",
    "@role": "role",
    "@arch": "archetype",
    "@narr": "narrative_role",
    "@presence": "presence_note",
    "@tropes": "trope_tags",
    "@theme": "theme",
}
