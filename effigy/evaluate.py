"""Effigy evaluation — roundtrip fidelity scoring + generation metrics.

Roundtrip fidelity (original purpose of this module):
  Tier 1 (deterministic): Structural completeness — all fields present,
    relationships preserved, enums match. Score 0-1.
  Tier 2 (embedding): Semantic similarity on prose fields. Requires
    sentence-transformers. Score 0-1. (Future)
  Tier 3 (LLM judge): Generate dialogue from original vs expanded, judge
    voice match. Run only on best round. (Future)

Generation-quality metrics (Phase 6 of the 2026-alignment plan):
  wrong_bleed_score  — how much generated text overlaps with WRONG examples
  voice_drift_score  — how close generated text is to MES voice exemplars
  compliance_check   — caller-supplied LLM judge over NEVER rules
  evaluate_generation — convenience wrapper returning all three
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from effigy.expand import expand
from effigy.notation import CharacterAST
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


# ---------------------------------------------------------------------------
# Generation-quality metrics (Phase 6 of the 2026-alignment plan)
# ---------------------------------------------------------------------------


def _char_ngrams(text: str, n: int = 4) -> set[str]:
    """Return set of character n-grams from text. Whitespace normalized."""
    cleaned = " ".join(text.split()).lower()
    if len(cleaned) < n:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def _longest_common_substring_len(a: str, b: str) -> int:
    """Length of the longest common substring between a and b.

    O(len(a) * len(b)) DP. Fine for short strings (a few hundred chars
    each). Call sites should pass normalized, short inputs.
    """
    if not a or not b:
        return 0
    # Use a rolling 1-D array for space.
    prev = [0] * (len(b) + 1)
    best = 0
    for i, ca in enumerate(a, 1):
        curr = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                curr[j] = prev[j - 1] + 1
                if curr[j] > best:
                    best = curr[j]
        prev = curr
    return best


def wrong_bleed_score(generated: str, ast: CharacterAST) -> float:
    """How much does the generated text bleed from a WRONG example?

    Returns the max normalized longest-common-substring length over all
    ``ast.wrong_examples`` entries. Result is in [0.0, 1.0] where 1.0
    means the generated text contains a WRONG example verbatim (or
    nearly so), normalized by the WRONG example's length.

    Empty wrong_examples returns 0.0.
    """
    if not ast.wrong_examples or not generated:
        return 0.0

    gen_norm = " ".join(generated.split()).lower()
    best = 0.0
    for we in ast.wrong_examples:
        if not we.wrong:
            continue
        wrong_norm = " ".join(we.wrong.split()).lower()
        if not wrong_norm:
            continue
        lcs = _longest_common_substring_len(gen_norm, wrong_norm)
        score = lcs / len(wrong_norm)
        if score > best:
            best = score
    return best


def voice_drift_score(generated: str, ast: CharacterAST) -> float:
    """How similar is generated text to the character's MES voice?

    Returns a char-4gram Jaccard similarity between ``generated`` and
    the concatenation of all ``ast.mes_examples``. Higher values mean
    the generation is more on-voice; lower means drift.

    Uses character n-grams (not embeddings) to avoid runtime deps.
    Callers wanting semantic similarity should compute their own
    embedding-based score — the signature is stable enough to swap in.

    Returns 0.0 when the character has no MES examples.
    """
    if not ast.mes_examples or not generated:
        return 0.0

    mes_blob_parts: list[str] = []
    for ex in ast.mes_examples:
        text = ex.text if hasattr(ex, "text") else ex
        mes_blob_parts.append(text)
    mes_blob = " ".join(mes_blob_parts)

    return _jaccard(_char_ngrams(generated, 4), _char_ngrams(mes_blob, 4))


def compliance_check(
    generated: str,
    ast: CharacterAST,
    judge: Callable[[str, str], bool],
) -> dict[str, bool]:
    """Run an LLM-judge callable over each NEVER rule.

    Args:
        generated: the generated response to check.
        ast: the character AST (uses ast.never_would_say).
        judge: caller-supplied callable ``(rule, text) -> bool`` that
            returns True when the text violates the rule. Effigy does
            not import any LLM SDK — the caller owns the judge.

    Returns a dict ``{rule_text: violated_bool}``. Empty NEVER list
    returns an empty dict.
    """
    return {rule: bool(judge(rule, generated)) for rule in ast.never_would_say}


def evaluate_generation(
    generated: str,
    ast: CharacterAST,
    *,
    judge: Callable[[str, str], bool] | None = None,
) -> dict:
    """Run all generation-quality metrics and return a flat dict.

    Suitable for logging alongside each generation call. Includes:
        wrong_bleed      — see wrong_bleed_score
        voice_drift      — see voice_drift_score
        compliance       — see compliance_check (only if judge given)
        compliance_count — count of violated NEVER rules

    Pure-text metrics are always included; compliance requires a judge.
    """
    metrics: dict = {
        "wrong_bleed": wrong_bleed_score(generated, ast),
        "voice_drift": voice_drift_score(generated, ast),
    }
    if judge is not None:
        compliance = compliance_check(generated, ast, judge)
        metrics["compliance"] = compliance
        metrics["compliance_count"] = sum(1 for v in compliance.values() if v)
    return metrics
