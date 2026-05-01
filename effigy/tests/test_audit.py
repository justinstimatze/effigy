"""Tests for effigy.audit — cross-character voice tic detection."""

import pytest

from effigy.audit import (
    TicFinding,
    find_cross_character_tics,
    format_findings_table,
)
from effigy.parser import parse


def _make(char_id: str, mes_lines: list[str], extras: str = "") -> str:
    """Build a minimal .effigy source string with the given MES examples."""
    mes_block = "\n  ---\n".join(f"npc> {line}" for line in mes_lines)
    return f"""@id {char_id}
@name {char_id.title()}

VOICE{{
  kernel: A test character.
}}

MES[
  {mes_block}
]
{extras}
"""


def test_no_findings_for_solo_character():
    ast = parse(_make("solo", ["I keep the ledger.", "The ledger holds the truth."]))
    assert find_cross_character_tics([ast]) == []


def test_shared_distinctive_token_flagged():
    a = parse(_make("alpha", ["I keep the ledger.", "The ledger never lies."]))
    b = parse(_make("beta", ["The ledger is heavy.", "Open the ledger again."]))
    findings = find_cross_character_tics([a, b], min_share=0.5, min_total=3)
    tokens = {f.token for f in findings}
    assert "ledger" in tokens


def test_stopwords_excluded():
    a = parse(_make("alpha", ["The thing the thing.", "The other the other."]))
    b = parse(_make("beta", ["The same the same.", "The that the this."]))
    findings = find_cross_character_tics([a, b], min_share=0.5, min_total=2)
    assert "the" not in {f.token for f in findings}
    assert "that" not in {f.token for f in findings}


def test_min_share_threshold():
    a = parse(_make("alpha", ["Distinctive token here."]))
    b = parse(_make("beta", ["Plain words only."]))
    c = parse(_make("gamma", ["Plain words only."]))
    d = parse(_make("delta", ["Plain words only."]))
    # "distinctive" appears in 1/4 = 0.25 < 0.5 threshold
    findings = find_cross_character_tics([a, b, c, d], min_share=0.5, min_total=1)
    assert "distinctive" not in {f.token for f in findings}


def test_min_total_threshold():
    # Token in many characters but only once each
    asts = [
        parse(_make(f"char{i}", [f"once {chr(ord('a') + i)}{chr(ord('a') + i)}"]))
        for i in range(5)
    ]
    findings = find_cross_character_tics(asts, min_share=0.5, min_total=10)
    assert findings == []


def test_finding_includes_per_character_counts():
    a = parse(_make("alpha", ["I keep the ledger.", "The ledger never lies."]))
    b = parse(_make("beta", ["Open the ledger."]))
    findings = find_cross_character_tics([a, b], min_share=0.5, min_total=3)
    ledger = next((f for f in findings if f.token == "ledger"), None)
    assert ledger is not None
    assert ledger.counts_per_character == {"alpha": 2, "beta": 1}
    assert ledger.total == 3


def test_includes_test_block_examples():
    src_a = """@id alpha
@name Alpha

VOICE{
  kernel: A test character.
}

TEST[
  name: VOICE_TEST
  question: Does this sound right?
  fail: A goblin stomps in.
  pass: A figure stomps in.
  why: Goblins are a model tic.
]
"""
    src_b = """@id beta
@name Beta

VOICE{
  kernel: A test character.
}

TEST[
  name: VOICE_TEST
  question: Same question?
  fail: Another goblin appears.
  pass: Another figure appears.
  why: Same reason.
]
"""
    a = parse(src_a)
    b = parse(src_b)
    findings = find_cross_character_tics([a, b], min_share=0.5, min_total=2)
    assert "goblin" in {f.token for f in findings}


def test_includes_arc_phase_voice():
    src_a = """@id alpha
@name Alpha

VOICE{
  kernel: A test character.
}

ARC{
  open → trust>=0.5
    voice: "Spectral, distant, weathered."
}
"""
    src_b = """@id beta
@name Beta

VOICE{
  kernel: A test character.
}

ARC{
  open → trust>=0.5
    voice: "Weathered tone, clipped phrases."
}
"""
    a = parse(src_a)
    b = parse(src_b)
    findings = find_cross_character_tics([a, b], min_share=0.5, min_total=2)
    assert "weathered" in {f.token for f in findings}


def test_sort_order_most_pervasive_first():
    a = parse(_make("alpha", ["ledger entry", "ledger note", "spire view"]))
    b = parse(_make("beta", ["ledger book", "ledger again"]))
    c = parse(_make("gamma", ["ledger paper"]))
    findings = find_cross_character_tics([a, b, c], min_share=0.3, min_total=2)
    # "ledger" appears in 3/3 chars, should outrank anything in 1/3
    assert findings[0].token == "ledger"


def test_format_table_empty():
    assert "No cross-character tics" in format_findings_table([], corpus_size=2)


def test_format_table_renders_findings():
    f = TicFinding(token="ledger", counts_per_character={"alpha": 2, "beta": 1})
    output = format_findings_table([f], corpus_size=2)
    assert "ledger" in output
    assert "2/2" in output
    assert "alpha(2)" in output
    assert "beta(1)" in output


def test_finding_spread_method():
    f = TicFinding(token="ledger", counts_per_character={"alpha": 2, "beta": 1})
    assert f.spread(corpus_size=4) == 0.5
    assert f.spread(corpus_size=2) == 1.0


def test_duplicate_char_id_raises():
    a = parse(_make("dupe", ["The ledger holds the truth."]))
    b = parse(_make("dupe", ["The ledger never lies."]))
    with pytest.raises(ValueError, match="Duplicate character identifier"):
        find_cross_character_tics([a, b])


def test_voice_kernel_tokens_included():
    src_a = """@id alpha
@name Alpha

VOICE{
  kernel: Spectral, distant, weathered.
}
"""
    src_b = """@id beta
@name Beta

VOICE{
  kernel: Weathered tone, clipped.
}
"""
    a = parse(src_a)
    b = parse(src_b)
    findings = find_cross_character_tics([a, b], min_share=0.5, min_total=2)
    assert "weathered" in {f.token for f in findings}


def test_quirks_and_traits_included():
    src_a = """@id alpha
@name Alpha

VOICE{
  kernel: A character.
}

QUIRKS[
  always whispers when nervous
]

TRAITS[
  - whispers under pressure
]
"""
    src_b = """@id beta
@name Beta

VOICE{
  kernel: Another character.
}

QUIRKS[
  whispers in every scene
]
"""
    a = parse(src_a)
    b = parse(src_b)
    findings = find_cross_character_tics([a, b], min_share=0.5, min_total=2)
    assert "whispers" in {f.token for f in findings}


def test_never_rules_included():
    src_a = """@id alpha
@name Alpha

VOICE{
  kernel: A character.
}

NEVER[
  - never mentions the goblins
]
"""
    src_b = """@id beta
@name Beta

VOICE{
  kernel: Another character.
}

NEVER[
  - never speaks of goblins
]
"""
    a = parse(src_a)
    b = parse(src_b)
    findings = find_cross_character_tics([a, b], min_share=0.5, min_total=2)
    assert "goblins" in {f.token for f in findings}


def test_sort_is_deterministic():
    # Two findings with identical (spread, total) should sort by token
    src_a = """@id alpha
@name Alpha

VOICE{
  kernel: A character.
}

MES[
  npc> alpha shared once.
  ---
  npc> beta shared once.
]
"""
    src_b = """@id beta
@name Beta

VOICE{
  kernel: A character.
}

MES[
  npc> alpha shared once.
  ---
  npc> beta shared once.
]
"""
    a = parse(src_a)
    b = parse(src_b)
    findings = find_cross_character_tics([a, b], min_share=0.5, min_total=2)
    tokens_in_order = [f.token for f in findings if f.token in ("alpha", "beta")]
    # Both have spread 2/2 and total 2 → tiebreaker is lexicographic
    assert tokens_in_order == ["alpha", "beta"]


def test_cli_json_output(tmp_path, capsys):
    import json as _json
    from effigy.cli import cmd_audit
    import argparse

    a = tmp_path / "alpha.effigy"
    b = tmp_path / "beta.effigy"
    a.write_text(_make("alpha", ["I keep the ledger.", "The ledger never lies."]))
    b.write_text(_make("beta", ["Open the ledger."]))

    args = argparse.Namespace(
        paths=[str(a), str(b)],
        min_share=0.5,
        min_total=3,
        json=True,
    )
    cmd_audit(args)
    captured = capsys.readouterr()
    payload = _json.loads(captured.out)
    assert payload["corpus_size"] == 2
    assert any(f["token"] == "ledger" for f in payload["findings"])
    ledger = next(f for f in payload["findings"] if f["token"] == "ledger")
    assert ledger["counts_per_character"] == {"alpha": 2, "beta": 1}
    assert ledger["spread"] == 1.0
    assert ledger["total"] == 3
