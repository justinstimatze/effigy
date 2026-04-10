"""Effigy corpus — load character JSONs for the discovery loop.

Generic corpus loader. Callers provide the corpus directory and
an optional char_id → filename mapping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CharacterSpec:
    """A character specification from the corpus."""
    char_id: str
    name: str
    json_data: dict
    json_text: str  # raw JSON string
    token_estimate: int

    @property
    def complexity(self) -> int:
        """Heuristic complexity score for seed selection.

        Higher = more complex. Based on field counts and prose length.
        """
        d = self.json_data
        score = 0
        score += len(d.get("mes_examples", [])) * 3
        score += len(d.get("secrets", [])) * 5
        score += len(d.get("relationships", [])) * 2
        score += len(d.get("era_states", [])) * 2
        score += len(d.get("voice_kernel", "")) // 50
        score += 10 if d.get("peak_voice") else 0
        score += len(d.get("drivermap_profile", {})) * 3
        return score


def load_corpus(
    corpus_dir: str | Path,
    char_map: dict[str, str] | None = None,
    char_ids: list[str] | None = None,
) -> list[CharacterSpec]:
    """Load character JSONs as CharacterSpec objects.

    Args:
        corpus_dir: Path to the directory containing character JSON files.
        char_map: Optional mapping of char_id → JSON filename. When a
            char_id is not found in this map, falls back to ``{char_id}.json``.
        char_ids: Specific char_ids to load. When *char_map* is provided and
            *char_ids* is None, defaults to all keys in *char_map*. Otherwise
            loads only explicitly listed IDs.

    Returns list sorted by complexity (ascending — most complex last,
    matching the discovery.py convention for seed selection).
    """
    corpus_dir = Path(corpus_dir)
    char_map = char_map or {}
    if char_ids is None:
        char_ids = list(char_map.keys()) if char_map else []

    specs: list[CharacterSpec] = []
    for char_id in char_ids:
        filename = char_map.get(char_id, f"{char_id}.json")
        json_path = corpus_dir / filename
        if not json_path.exists():
            continue

        json_text = json_path.read_text(encoding="utf-8")
        json_data = json.loads(json_text)

        specs.append(CharacterSpec(
            char_id=char_id,
            name=json_data.get("name", char_id),
            json_data=json_data,
            json_text=json_text,
            token_estimate=len(json_text) // 4,
        ))

    # Sort by complexity ascending (most complex last = seed apps)
    specs.sort(key=lambda s: s.complexity)
    return specs


def char_to_text(spec: CharacterSpec) -> str:
    """Convert a CharacterSpec to a readable text representation for prompting.

    A human-readable description of what the notation must capture.
    """
    d = spec.json_data
    lines = [
        f"# {d['name']} ({d.get('char_id', '')})",
        f"Role: {d.get('role', '')}, Archetype: {d.get('archetype', '')}",
        f"Narrative role: {d.get('narrative_role', '')}",
        "",
        f"Presence: {d.get('presence_note', '')}",
        "",
        f"Voice kernel: {d.get('voice_kernel', '')}",
    ]

    if d.get("peak_voice"):
        lines.append(f"Peak voice: {d['peak_voice']}")

    lines.append("")

    # Mes examples
    mes = d.get("mes_examples", [])
    if mes:
        lines.append(f"Dialogue examples ({len(mes)}):")
        for ex in mes:
            lines.append(f"  {ex}")
        lines.append("")

    # Uncertainty voice
    unc = d.get("uncertainty_voice", [])
    if unc:
        lines.append(f"Uncertainty voice ({len(unc)}):")
        for u in unc:
            lines.append(f"  {u}")
        lines.append("")

    # Secrets
    secrets = d.get("secrets", [])
    if secrets:
        lines.append(f"Secrets ({len(secrets)}):")
        for s in secrets:
            lines.append(f"  Layer {s['layer']}: {s['secret'][:100]}...")
            if s.get("reveal_condition"):
                lines.append(f"    Reveal: {s['reveal_condition'][:80]}...")
            if s.get("related_era"):
                lines.append(f"    Era: {s['related_era']}")
        lines.append("")

    # Relationships
    rels = d.get("relationships", [])
    if rels:
        lines.append(f"Relationships ({len(rels)}):")
        for r in rels:
            lines.append(f"  → {r['target']}: {r['type']} ({r.get('intensity', 0.5)}) — {r.get('notes', '')[:60]}")
        lines.append("")

    # Schedule
    sched = d.get("schedule", {})
    if sched:
        slots = [f"{k}: {v}" for k, v in sched.items() if v]
        lines.append(f"Schedule: {', '.join(slots)}")
        lines.append("")

    # Era states
    eras = d.get("era_states", [])
    if eras:
        lines.append(f"Era states ({len(eras)}):")
        for e in eras:
            era_line = f"  {e['era_id']}: {e['status']}"
            if e.get("age"):
                era_line += f", age {e['age']}"
            if e.get("occupation"):
                era_line += f", {e['occupation'][:50]}"
            lines.append(era_line)
            if e.get("disposition"):
                lines.append(f"    Disposition: {e['disposition'][:80]}...")
            if e.get("notes"):
                lines.append(f"    Notes: {e['notes'][:80]}...")
        lines.append("")

    # Drivermap
    dm = d.get("drivermap_profile", {})
    if dm:
        traits = [f"{k}={v}" for k, v in dm.items()]
        lines.append(f"Drivermap: {', '.join(traits)}")
    sf = d.get("npc_situation_features", [])
    if sf:
        lines.append(f"Situation features: {', '.join(sf)}")

    # Trope tags
    tropes = d.get("trope_tags", [])
    if tropes:
        lines.append(f"Trope tags: {', '.join(tropes)}")

    # Arrival/departure
    arr = d.get("arrival_lines", [])
    dep = d.get("departure_lines", [])
    if arr:
        lines.append(f"Arrival lines ({len(arr)}): {arr[0][:60]}...")
    if dep:
        lines.append(f"Departure lines ({len(dep)}): {dep[0][:60]}...")

    return "\n".join(lines)


def corpus_summary(specs: list[CharacterSpec]) -> str:
    """Print a summary of the corpus for discovery loop status."""
    lines = [f"Corpus: {len(specs)} characters"]
    for s in specs:
        lines.append(f"  {s.char_id}: {s.token_estimate} tokens, complexity={s.complexity}")
    total = sum(s.token_estimate for s in specs)
    lines.append(f"Total: {total} tokens")
    return "\n".join(lines)
