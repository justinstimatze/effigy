"""Effigy metrics — character-domain measurements.

Token counting, compression ratios, and structural metrics
for the character notation domain.
"""

from __future__ import annotations

from dataclasses import dataclass


def estimate_tokens(text: str) -> int:
    """Estimate token count using the ~4 chars/token heuristic.

    For more accurate counting, use tiktoken or the anthropic tokenizer.
    This is sufficient for comparative metrics.
    """
    return max(1, len(text) // 4)


@dataclass
class CharacterMetrics:
    """Metrics for a single character's notation vs JSON."""
    char_id: str
    json_tokens: int = 0
    effigy_tokens: int = 0
    json_bytes: int = 0
    effigy_bytes: int = 0

    @property
    def compression_ratio(self) -> float:
        """JSON tokens / effigy tokens. >1.0 means compression."""
        if self.effigy_tokens == 0:
            return 0.0
        return self.json_tokens / self.effigy_tokens

    @property
    def byte_ratio(self) -> float:
        if self.effigy_bytes == 0:
            return 0.0
        return self.json_bytes / self.effigy_bytes

    def summary(self) -> str:
        return (
            f"{self.char_id}: "
            f"JSON={self.json_tokens}tok/{self.json_bytes}B, "
            f"Effigy={self.effigy_tokens}tok/{self.effigy_bytes}B, "
            f"ratio={self.compression_ratio:.2f}x"
        )


def measure_character(
    char_id: str,
    json_text: str,
    effigy_text: str,
) -> CharacterMetrics:
    """Measure compression metrics for a character."""
    return CharacterMetrics(
        char_id=char_id,
        json_tokens=estimate_tokens(json_text),
        effigy_tokens=estimate_tokens(effigy_text),
        json_bytes=len(json_text.encode("utf-8")),
        effigy_bytes=len(effigy_text.encode("utf-8")),
    )


@dataclass
class CorpusMetrics:
    """Aggregate metrics across all characters."""
    characters: list[CharacterMetrics]

    @property
    def mean_compression(self) -> float:
        ratios = [c.compression_ratio for c in self.characters if c.compression_ratio > 0]
        return sum(ratios) / len(ratios) if ratios else 0.0

    @property
    def total_json_tokens(self) -> int:
        return sum(c.json_tokens for c in self.characters)

    @property
    def total_effigy_tokens(self) -> int:
        return sum(c.effigy_tokens for c in self.characters)

    def summary(self) -> str:
        lines = [f"Characters: {len(self.characters)}"]
        for c in self.characters:
            lines.append(f"  {c.summary()}")
        lines.append(f"Mean compression: {self.mean_compression:.2f}x")
        lines.append(f"Total JSON: {self.total_json_tokens} tokens")
        lines.append(f"Total Effigy: {self.total_effigy_tokens} tokens")
        return "\n".join(lines)
