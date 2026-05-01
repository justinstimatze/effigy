"""Static cross-character tic detection over an effigy corpus.

Inspired by OpenAI's investigation into the GPT-5.x "goblin" tic — when
multiple characters in a corpus share an authoring fingerprint (favorite
metaphors, distinctive vocabulary), the model overweights the shared
substrate and bleeds it across characters. This module surfaces those
shared content tokens so the author can spot and prune unintended overlap.

The scan is purely static — no LLM calls. It tokenizes the prompt-rendered
surface of each character (MES, WRONG, TEST examples, plus arc-phase voice
and deflection prose) and reports tokens that appear in many characters'
voice surface above a frequency floor.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from effigy.notation import CharacterAST

# Generic English stopwords + character-dossier high-frequency words that
# carry no voice signal. Kept conservative — better to leak a few real tics
# through than to mask one with an over-aggressive filter.
STOPWORDS = frozenset("""
    a an the and or but if so as at by for in of on to up with from
    is are was were be been being am do does did doing have has had having
    i you he she it we they me him her us them my your his its our their
    this that these those there here what when where why how which who whom
    not no yes also too very just only really still even any some all
    will would could should may might must can shall ought now then
    one two three first second other another same different new old
    say says said tell tells told asked asks ask reply replied replies
    look looks looked see sees saw seen
    go goes went gone come comes came
    get gets got take takes took give gives gave
    know knows knew think thinks thought
    yeah okay alright fine sure right
    out over under across through about around back away off into onto
    own way ways beat moment turn turns next last
    i'm i've i'll you're you've you'll he's she's it's they're they've
    we're we've don't doesn't didn't won't can't couldn't wouldn't shouldn't
    isn't aren't wasn't weren't hasn't haven't hadn't that's there's here's
    let's what's who's how's where's when's
""".split())

# Match a-z words including apostrophes and hyphens. Min length filter applied
# separately so we can keep "no" / "yes" classified as stopwords above.
WORD_RE = re.compile(r"[a-z][a-z'\-]+")

# Strip mustache template variables ({{char}}, {{user}}) and speaker prefixes
# ("npc>", "user>", "{{char}}:") — these are scaffolding, not voice content.
_TEMPLATE_VAR_RE = re.compile(r"\{\{[^}]*\}\}")
# After template vars are stripped, lines often start with leftover prefix
# punctuation followed by a speaker tag (e.g. ": npc> "). Eat any leading
# combination of whitespace, punctuation, and word>/word: pairs.
_SPEAKER_PREFIX_RE = re.compile(
    r"^[\s:>]*(?:[a-zA-Z_][a-zA-Z0-9_-]*\s*[:>]\s*)+",
    re.MULTILINE,
)


def _strip_scaffolding(text: str) -> str:
    text = _TEMPLATE_VAR_RE.sub("", text)
    text = _SPEAKER_PREFIX_RE.sub("", text)
    return text


@dataclass
class TicFinding:
    """A token shared by many characters' voice surface."""

    token: str
    counts_per_character: dict[str, int]

    @property
    def characters(self) -> list[str]:
        return sorted(self.counts_per_character)

    @property
    def total(self) -> int:
        return sum(self.counts_per_character.values())

    def spread(self, corpus_size: int) -> float:
        return len(self.counts_per_character) / corpus_size


def _extract_voice_text(ast: CharacterAST) -> list[str]:
    """Pull all prompt-bound voice surface from a character.

    Includes everything the LLM will see at generation time as voice
    exemplar or voice-shaping prose:

    - VOICE kernel + peak
    - Traits, quirks, NEVER rules
    - MES dialogue
    - WRONG/RIGHT examples (context, wrong, right)
    - TEST fail/pass examples
    - Per-arc-phase voice and deflection
    - Arrival/departure lines
    """
    pieces: list[str] = []

    if ast.voice:
        if ast.voice.kernel:
            pieces.append(ast.voice.kernel)
        if ast.voice.peak:
            pieces.append(ast.voice.peak)

    pieces.extend(ast.traits)
    pieces.extend(ast.quirks)

    for never in ast.never_would_say:
        if never.text:
            pieces.append(never.text)

    for ex in ast.mes_examples:
        text = getattr(ex, "text", ex)
        if text:
            pieces.append(text)

    for w in ast.wrong_examples:
        if w.context:
            pieces.append(w.context)
        if w.wrong:
            pieces.append(w.wrong)
        if w.right:
            pieces.append(w.right)

    for t in ast.tests:
        pieces.extend(t.fail_examples)
        pieces.extend(t.pass_examples)

    for phase in ast.arc_phases:
        if phase.voice:
            pieces.append(phase.voice)
        if phase.deflection:
            pieces.append(phase.deflection)

    pieces.extend(ast.arrival_lines)
    pieces.extend(ast.departure_lines)

    return pieces


def _tokenize(pieces: list[str]) -> Counter:
    counts: Counter = Counter()
    for piece in pieces:
        cleaned = _strip_scaffolding(piece).lower()
        for w in WORD_RE.findall(cleaned):
            if len(w) <= 2:
                continue
            if w in STOPWORDS:
                continue
            counts[w] += 1
    return counts


def find_cross_character_tics(
    asts: list[CharacterAST],
    *,
    min_share: float = 0.3,
    min_total: int = 3,
) -> list[TicFinding]:
    """Find tokens shared across many characters' voice surface.

    A token is flagged when it appears in at least ``min_share`` of the
    corpus AND has at least ``min_total`` occurrences across the corpus.

    Defaults are tuned for surfacing real authoring tics in mid-sized
    corpora (10-30 characters): ``min_share=0.3`` flags tokens shared by
    3+ characters in a 10-character corpus or 6+ in a 20-character one,
    and ``min_total=3`` filters out one-off coincidences.

    Returns a list sorted by spread × total (most pervasive tics first).
    """
    if len(asts) < 2:
        return []

    per_char: dict[str, Counter] = {}
    for ast in asts:
        cid = ast.char_id or ast.name or f"<unnamed-{id(ast)}>"
        if cid in per_char:
            raise ValueError(
                f"Duplicate character identifier in audit corpus: {cid!r}. "
                f"Each .effigy file must have a unique @id."
            )
        per_char[cid] = _tokenize(_extract_voice_text(ast))

    token_to_chars: dict[str, dict[str, int]] = defaultdict(dict)
    for cid, counts in per_char.items():
        for tok, n in counts.items():
            token_to_chars[tok][cid] = n

    n = len(asts)
    findings: list[TicFinding] = []
    for tok, char_counts in token_to_chars.items():
        if len(char_counts) / n < min_share:
            continue
        if sum(char_counts.values()) < min_total:
            continue
        findings.append(TicFinding(token=tok, counts_per_character=dict(char_counts)))

    # Sort: most-shared first, then highest-frequency, then lexicographic
    # (token asc) as a deterministic tiebreaker.
    findings.sort(key=lambda f: f.token)
    findings.sort(
        key=lambda f: (len(f.counts_per_character), f.total),
        reverse=True,
    )
    return findings


def format_findings_table(findings: list[TicFinding], corpus_size: int) -> str:
    """Format findings as a fixed-width text table for terminal output."""
    if not findings:
        return "No cross-character tics found."

    header = f"{'TOKEN':<20} {'SPREAD':<8} {'TOTAL':<6} CHARACTERS"
    lines = [header, "-" * len(header)]
    for f in findings:
        spread = f"{len(f.counts_per_character)}/{corpus_size}"
        chars_inline = ", ".join(
            f"{c}({f.counts_per_character[c]})" for c in f.characters
        )
        lines.append(f"{f.token:<20} {spread:<8} {f.total:<6} {chars_inline}")
    return "\n".join(lines)
