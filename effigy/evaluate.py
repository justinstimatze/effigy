"""Effigy evaluation — roundtrip fidelity scoring.

Three tiers:
  Tier 1 (deterministic): Structural completeness — all fields present,
    relationships preserved, enums match. Score 0-1.
  Tier 2 (embedding): Semantic similarity on prose fields. Requires
    sentence-transformers. Score 0-1. (Future)
  Tier 3 (LLM judge): Generate dialogue from original vs expanded, judge
    voice match. Run only on best round. (Future)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from effigy.expand import expand
from effigy.parser import parse


@dataclass
class FieldScore:
    """Score for a single field comparison."""
    field_name: str
    score: float  # 0.0-1.0
    details: str = ""


@dataclass
class EvalResult:
    """Complete evaluation result for one character."""
    char_id: str
    tier1_score: float = 0.0
    field_scores: list[FieldScore] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    extra_fields: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"{self.char_id}: Tier 1 = {self.tier1_score:.2%}"]
        if self.missing_fields:
            lines.append(f"  Missing: {', '.join(self.missing_fields)}")
        for fs in self.field_scores:
            if fs.score < 1.0:
                lines.append(f"  {fs.field_name}: {fs.score:.2%} — {fs.details}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tier 1: Structural completeness
# ---------------------------------------------------------------------------

# Fields that must be present and match exactly
EXACT_FIELDS = ["char_id", "name", "role", "archetype", "narrative_role"]

# Fields where we compare count
COUNT_FIELDS = ["mes_examples", "uncertainty_voice", "secrets",
                "relationships", "era_states", "arrival_lines", "departure_lines"]

# Fields where we compare content presence (non-empty)
PRESENCE_FIELDS = ["voice_kernel", "presence_note"]

# Nested fields with specific comparison logic
NESTED_FIELDS = ["schedule", "drivermap_profile", "npc_situation_features",
                 "trope_tags", "peak_voice"]


def evaluate_tier1(original: dict, expanded: dict) -> EvalResult:
    """Score structural roundtrip fidelity.

    Compares an expanded dict (from .effigy → AST → JSON) against
    the original corpus JSON. Returns a score from 0.0 to 1.0.
    """
    result = EvalResult(char_id=original.get("char_id", "unknown"))
    scores: list[float] = []

    # Exact match fields
    for f in EXACT_FIELDS:
        orig_val = original.get(f, "")
        exp_val = expanded.get(f, "")
        if orig_val == exp_val:
            scores.append(1.0)
            result.field_scores.append(FieldScore(f, 1.0))
        else:
            scores.append(0.0)
            result.field_scores.append(FieldScore(
                f, 0.0, f"expected '{orig_val}', got '{exp_val}'"))

    # Count match fields
    for f in COUNT_FIELDS:
        orig_val = original.get(f, [])
        exp_val = expanded.get(f, [])
        if not isinstance(orig_val, list):
            orig_val = []
        if not isinstance(exp_val, list):
            exp_val = []
        orig_count = len(orig_val)
        exp_count = len(exp_val)
        if orig_count == 0:
            score = 1.0 if exp_count == 0 else 0.5
        else:
            score = min(1.0, exp_count / orig_count)
        scores.append(score)
        result.field_scores.append(FieldScore(
            f, score, f"{exp_count}/{orig_count} items"))

    # Presence fields
    for f in PRESENCE_FIELDS:
        orig_val = original.get(f, "")
        exp_val = expanded.get(f, "")
        if orig_val and exp_val:
            # Check if content matches (exact or very close)
            if orig_val == exp_val:
                score = 1.0
            elif orig_val.lower() in exp_val.lower() or exp_val.lower() in orig_val.lower():
                score = 0.9
            else:
                score = 0.5  # present but different
        elif not orig_val and not exp_val:
            score = 1.0
        elif orig_val and not exp_val:
            score = 0.0
            result.missing_fields.append(f)
        else:
            score = 0.5
        scores.append(score)
        result.field_scores.append(FieldScore(f, score))

    # Schedule
    orig_sched = original.get("schedule", {})
    exp_sched = expanded.get("schedule", {})
    sched_matches = 0
    sched_total = 0
    for slot in ("morning", "afternoon", "evening", "night"):
        orig_loc = orig_sched.get(slot)
        exp_loc = exp_sched.get(slot)
        sched_total += 1
        if orig_loc == exp_loc:
            sched_matches += 1
    sched_score = sched_matches / max(sched_total, 1)
    scores.append(sched_score)
    result.field_scores.append(FieldScore("schedule", sched_score))

    # Drivermap profile
    orig_dm = original.get("drivermap_profile", {})
    exp_dm = expanded.get("drivermap_profile", {})
    if orig_dm:
        dm_matches = sum(1 for k in orig_dm if exp_dm.get(k) == orig_dm[k])
        dm_score = dm_matches / len(orig_dm)
    else:
        dm_score = 1.0 if not exp_dm else 0.5
    scores.append(dm_score)
    result.field_scores.append(FieldScore("drivermap_profile", dm_score))

    # Situation features
    orig_sf = set(original.get("npc_situation_features", []))
    exp_sf = set(expanded.get("npc_situation_features", []))
    if orig_sf:
        sf_score = len(orig_sf & exp_sf) / len(orig_sf)
    else:
        sf_score = 1.0 if not exp_sf else 0.5
    scores.append(sf_score)
    result.field_scores.append(FieldScore("situation_features", sf_score))

    # Trope tags
    orig_tropes = set(original.get("trope_tags", []))
    exp_tropes = set(expanded.get("trope_tags", []))
    if orig_tropes:
        trope_score = len(orig_tropes & exp_tropes) / len(orig_tropes)
    else:
        trope_score = 1.0 if not exp_tropes else 0.5
    scores.append(trope_score)
    result.field_scores.append(FieldScore("trope_tags", trope_score))

    # Peak voice
    orig_pv = original.get("peak_voice", "")
    exp_pv = expanded.get("peak_voice", "")
    if orig_pv and exp_pv:
        pv_score = 1.0 if orig_pv == exp_pv else 0.5
    elif not orig_pv and not exp_pv:
        pv_score = 1.0
    elif orig_pv and not exp_pv:
        pv_score = 0.0
        result.missing_fields.append("peak_voice")
    else:
        pv_score = 0.5
    scores.append(pv_score)
    result.field_scores.append(FieldScore("peak_voice", pv_score))

    # Relationship targets (set match)
    orig_rel_targets = {r["target"] for r in original.get("relationships", [])}
    exp_rel_targets = {r["target"] for r in expanded.get("relationships", [])}
    if orig_rel_targets:
        rel_target_score = len(orig_rel_targets & exp_rel_targets) / len(orig_rel_targets)
    else:
        rel_target_score = 1.0 if not exp_rel_targets else 0.5
    scores.append(rel_target_score)
    result.field_scores.append(FieldScore("relationship_targets", rel_target_score))

    # Compute overall
    result.tier1_score = sum(scores) / len(scores) if scores else 0.0

    return result


def evaluate_effigy_file(
    effigy_path: str | Path,
    json_path: str | Path,
) -> EvalResult:
    """Evaluate a .effigy file against its original JSON.

    Convenience function that parses, expands, and evaluates.
    """
    effigy_text = Path(effigy_path).read_text(encoding="utf-8")
    original = json.loads(Path(json_path).read_text(encoding="utf-8"))

    ast = parse(effigy_text)
    expanded = expand(ast)

    return evaluate_tier1(original, expanded)


def evaluate_all(
    effigy_dir: str | Path,
    corpus_dir: str | Path,
    char_map: dict[str, str] | None = None,
) -> list[EvalResult]:
    """Evaluate all .effigy files against their corpus JSON counterparts.

    char_map maps char_id → json_filename (e.g. {"npc_one": "innkeeper.json"}).
    If not provided, assumes json filename matches char_id.
    """
    effigy_dir = Path(effigy_dir)
    corpus_dir = Path(corpus_dir)
    char_map = char_map or {}

    results = []
    for effigy_file in sorted(effigy_dir.glob("*.effigy")):
        char_id = effigy_file.stem
        json_name = char_map.get(char_id, f"{char_id}.json")
        json_file = corpus_dir / json_name
        if not json_file.exists():
            continue
        results.append(evaluate_effigy_file(effigy_file, json_file))

    return results
