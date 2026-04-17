"""Effigy Layer 1 — AST → JSON (deterministic, no LLM).

Expands a CharacterAST back to the character JSON format,
producing a dict that matches your character corpus JSON schema.

This is the compile step: .effigy → JSON for corpus ingestion.
"""

from __future__ import annotations

from effigy.notation import (
    CharacterAST,
    DrivermapAST,
    ScheduleAST,
)


def expand(ast: CharacterAST) -> dict:
    """Expand a CharacterAST to the character JSON format.

    Returns a dict matching the character JSON schema.
    All fields are populated; missing AST fields produce empty defaults.
    """
    result: dict = {}

    # Header fields
    result["char_id"] = ast.char_id
    result["name"] = ast.name
    result["presence_note"] = ast.presence_note
    result["role"] = ast.role
    result["archetype"] = ast.archetype

    # Voice
    if ast.voice:
        result["voice_kernel"] = ast.voice.kernel
    else:
        result["voice_kernel"] = ""

    # Dialogue examples
    result["mes_examples"] = [ex.text if hasattr(ex, "text") else ex for ex in ast.mes_examples]
    result["narrative_role"] = ast.narrative_role.value
    result["uncertainty_voice"] = list(ast.uncertainty_voice)

    # Trope tags
    result["trope_tags"] = list(ast.trope_tags)

    # Era states
    result["era_states"] = []
    for era in ast.era_states:
        era_dict: dict = {"era_id": era.era_id, "status": era.status}
        if era.age is not None:
            era_dict["age"] = era.age
        if era.occupation:
            era_dict["occupation"] = era.occupation
        if era.disposition:
            era_dict["disposition"] = era.disposition
        if era.notes:
            era_dict["notes"] = era.notes
        result["era_states"].append(era_dict)

    # Secrets
    result["secrets"] = []
    for secret in ast.secrets:
        s: dict = {
            "layer": secret.layer,
            "secret": secret.secret,
        }
        if secret.reveal_condition:
            s["reveal_condition"] = secret.reveal_condition
            # Extract REQUIRES fact gates from reveal_condition
            import re as _re

            _req_match = _re.search(
                r"REQUIRES player knows\s+(.+?)(?:\s*[—\-.]|$)",
                secret.reveal_condition,
            )
            if _req_match:
                _facts = [f.strip() for f in _req_match.group(1).split(" or ")]
                s["requires_fact"] = _facts
        if secret.related_era:
            s["related_era"] = secret.related_era
        result["secrets"].append(s)

    # Relationships
    result["relationships"] = []
    for rel in ast.relationships:
        r: dict = {
            "target": rel.target,
            "type": rel.rel_type,
            "intensity": rel.intensity,
        }
        if rel.notes:
            r["notes"] = rel.notes
        result["relationships"].append(r)

    # Schedule
    sched = ast.schedule or ScheduleAST()
    result["schedule"] = {
        "morning": sched.morning,
        "afternoon": sched.afternoon,
        "evening": sched.evening,
        "night": sched.night,
    }

    # Arrival/departure
    result["arrival_lines"] = list(ast.arrival_lines)
    result["departure_lines"] = list(ast.departure_lines)

    # Drivermap
    dm = ast.drivermap or DrivermapAST()
    if dm.profile:
        result["drivermap_profile"] = dict(dm.profile)
    if dm.situation_features:
        result["npc_situation_features"] = list(dm.situation_features)

    # Peak voice (from voice block)
    if ast.voice and ast.voice.peak:
        result["peak_voice"] = ast.voice.peak

    # Behavioral dossier fields
    if ast.traits:
        result["traits"] = list(ast.traits)
    if ast.never_would_say:
        # Preserve @when gates in JSON output when present. When any rule
        # has a gate, emit every rule as a uniform {"text", "when"} dict so
        # consumers have one code path. Otherwise emit plain strings for
        # backward compatibility with pre-@when consumers.
        if any(n.when for n in ast.never_would_say):
            result["never_would_say"] = [
                {"text": n.text, "when": n.when}
                for n in ast.never_would_say
            ]
        else:
            result["never_would_say"] = [n.text for n in ast.never_would_say]
    if ast.quirks:
        result["quirks"] = list(ast.quirks)
    if ast.theme:
        result["theme"] = ast.theme
    if ast.goal_behaviors:
        result["goal_behaviors"] = dict(ast.goal_behaviors)

    # Reasoning tests
    if ast.tests:
        result["tests"] = []
        for t in ast.tests:
            td: dict = {
                "name": t.name,
                "question": t.question,
            }
            if t.dimension:
                td["dimension"] = t.dimension
            if t.fail_examples:
                td["fail_examples"] = list(t.fail_examples)
            if t.pass_examples:
                td["pass_examples"] = list(t.pass_examples)
            if t.why:
                td["why"] = t.why
            if t.beat:
                td["beat"] = t.beat
            result["tests"].append(td)

    return result


def expand_to_json(ast: CharacterAST, indent: int = 2) -> str:
    """Expand to JSON string."""
    import json

    return json.dumps(expand(ast), indent=indent, ensure_ascii=False)
