"""Effigy parser — .effigy notation → CharacterAST.

Recursive descent parser for character data (prose-heavy, block-structured).

Grammar (informal):
    effigy      ::= header* block*
    header      ::= '@' IDENT value NEWLINE
    block       ::= KEYWORD '{' content '}' | KEYWORD '[' items ']'
    content     ::= (key ':' value NEWLINE)*
    items       ::= item ('---' item)*
    item        ::= verbatim_line+

Prose escaping:
    §...§  preserves verbatim text (multiline OK, no compression)
    Lines starting with # inside blocks are comments (ignored)
"""

from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

from effigy.notation import (
    ArcPhaseAST,
    CharacterAST,
    DrivermapAST,
    EraStateAST,
    GoalAST,
    NarrativeRole,
    RelationshipAST,
    ScheduleAST,
    SecretAST,
    VoiceAST,
    WrongExampleAST,
)


class ParseError(Exception):
    """Raised when the parser encounters invalid notation."""

    def __init__(self, message: str, line: int = 0, col: int = 0):
        self.line = line
        self.col = col
        super().__init__(f"line {line}: {message}" if line else message)


# ---------------------------------------------------------------------------
# Parser state
# ---------------------------------------------------------------------------


@dataclass
class _ParserState:
    """Cursor over the input text."""

    text: str
    pos: int = 0
    line: int = 1

    def at_end(self) -> bool:
        return self.pos >= len(self.text)

    def peek(self, n: int = 1) -> str:
        return self.text[self.pos : self.pos + n]

    def advance(self, n: int = 1) -> str:
        chunk = self.text[self.pos : self.pos + n]
        for ch in chunk:
            if ch == "\n":
                self.line += 1
        self.pos += n
        return chunk

    def skip_whitespace(self) -> None:
        """Skip spaces and tabs (NOT newlines — those are structural)."""
        while not self.at_end() and self.text[self.pos] in " \t":
            self.pos += 1

    def skip_ws_and_newlines(self) -> None:
        """Skip all whitespace including newlines."""
        while not self.at_end() and self.text[self.pos] in " \t\n\r":
            if self.text[self.pos] == "\n":
                self.line += 1
            self.pos += 1

    def skip_line(self) -> None:
        """Skip to the end of the current line."""
        while not self.at_end() and self.text[self.pos] != "\n":
            self.pos += 1
        if not self.at_end():
            self.advance(1)  # consume the newline

    def read_line(self) -> str:
        """Read to end of line, consuming the newline."""
        start = self.pos
        while not self.at_end() and self.text[self.pos] != "\n":
            self.pos += 1
        result = self.text[start : self.pos]
        if not self.at_end():
            self.advance(1)  # consume newline
        return result

    def read_until(self, stop: str) -> str:
        """Read until a stop character is found (not consumed)."""
        start = self.pos
        while not self.at_end() and self.text[self.pos] != stop:
            if self.text[self.pos] == "\n":
                self.line += 1
            self.pos += 1
        return self.text[start : self.pos]

    def expect(self, s: str) -> None:
        """Consume expected string or raise ParseError."""
        if self.text[self.pos : self.pos + len(s)] != s:
            raise ParseError(f"expected '{s}', got '{self.peek(len(s))}'", self.line)
        self.advance(len(s))

    def match(self, s: str) -> bool:
        """Consume string if present, return True. Otherwise False."""
        if self.text[self.pos : self.pos + len(s)] == s:
            self.advance(len(s))
            return True
        return False

    def remaining_on_line(self) -> str:
        """Return rest of current line without consuming."""
        end = self.text.find("\n", self.pos)
        if end == -1:
            return self.text[self.pos :]
        return self.text[self.pos : end]


# ---------------------------------------------------------------------------
# Block content parsers
# ---------------------------------------------------------------------------


def _read_braced_block(state: _ParserState) -> str:
    """Read content between { and }, handling nested braces."""
    state.skip_ws_and_newlines()
    state.expect("{")
    depth = 1
    start = state.pos
    while not state.at_end() and depth > 0:
        ch = state.text[state.pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                content = state.text[start : state.pos]
                state.advance(1)  # consume closing }
                return content
        if ch == "\n":
            state.line += 1
        state.pos += 1
    raise ParseError("unterminated block (missing })", state.line)


def _read_bracketed_block(state: _ParserState) -> str:
    """Read content between [ and ], handling nested brackets."""
    state.skip_ws_and_newlines()
    state.expect("[")
    depth = 1
    start = state.pos
    while not state.at_end() and depth > 0:
        ch = state.text[state.pos]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                content = state.text[start : state.pos]
                state.advance(1)  # consume closing ]
                return content
        if ch == "\n":
            state.line += 1
        state.pos += 1
    raise ParseError("unterminated block (missing ])", state.line)


def _split_items(content: str) -> list[str]:
    """Split block content by --- separator lines."""
    items: list[str] = []
    current_lines: list[str] = []
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped == "---":
            if current_lines:
                items.append("\n".join(current_lines))
                current_lines = []
        elif stripped and not stripped.startswith("#"):
            current_lines.append(line)
    if current_lines:
        items.append("\n".join(current_lines))
    return items


def _parse_kv_line(line: str) -> tuple[str, str] | None:
    """Parse a 'key: value' line. Returns None if not a kv line."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    colon = stripped.find(":")
    if colon == -1:
        return None
    key = stripped[:colon].strip()
    value = stripped[colon + 1 :].strip()
    return key, value


def _parse_kv_block(content: str) -> dict[str, str]:
    """Parse a block of key: value lines into a dict.

    Handles multiline values: lines that don't contain ':' at a
    reasonable position are appended to the previous key's value.
    """
    result: dict[str, str] = {}
    last_key: str | None = None
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        kv = _parse_kv_line(line)
        if kv:
            key, val = kv
            result[key] = val
            last_key = key
        elif last_key is not None:
            # Continuation line — append to previous value
            result[last_key] += " " + stripped
    return result


# ---------------------------------------------------------------------------
# Block-specific parsers
# ---------------------------------------------------------------------------


def _parse_voice_block(content: str) -> VoiceAST:
    """Parse VOICE{kernel: ..., peak: ...}."""
    kv = _parse_kv_block(content)
    return VoiceAST(
        kernel=kv.get("kernel", ""),
        peak=kv.get("peak", ""),
    )


def _parse_traits_block(content: str) -> list[str]:
    """Parse TRAITS[...] — PList-style comma-separated behavioral rules."""
    # Flatten to single string, split on commas
    flat = " ".join(
        line.strip()
        for line in content.split("\n")
        if line.strip() and not line.strip().startswith("#")
    )
    return [t.strip() for t in flat.split(",") if t.strip()]


@dataclass
class MESExample:
    """A single MES dialogue example with optional trust tier."""

    text: str
    tier: str = "any"  # "low", "moderate", "high", or "any"


def _parse_mes_block(content: str) -> list[MESExample]:
    """Parse MES[...] -- Ali:Chat dialogue examples separated by ---.

    Supports both single-line {{char}}: examples and multi-line
    Ali:Chat exchanges ({{user}}: ... {{char}}: ...).

    Trust tier annotations are parsed from two sources:
      1. Structured: ``@tier low`` line before the example
      2. Convention: ``# LOW TRUST:`` comment line (legacy)
    """
    # Split on --- but preserve @tier and # TRUST annotations
    items: list[tuple[list[str], str]] = []
    current_lines: list[str] = []
    current_tier = "any"
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped == "---":
            if current_lines:
                items.append((current_lines, current_tier))
                current_lines = []
                current_tier = "any"  # reset for next item
            continue
        # Structured tier annotation
        if stripped.startswith("@tier"):
            tier_val = stripped[5:].strip().lower()
            if tier_val in ("low", "moderate", "high"):
                current_tier = tier_val
            continue
        # Legacy comment-based tier annotation
        if stripped.startswith("#"):
            upper = stripped.upper()
            if "TRUST" in upper:
                if "LOW" in upper:
                    current_tier = "low"
                elif "MODERATE" in upper or "MID" in upper:
                    current_tier = "moderate"
                elif "HIGH" in upper:
                    current_tier = "high"
            continue
        if stripped:
            current_lines.append(line)
    if current_lines:
        items.append((current_lines, current_tier))

    result: list[MESExample] = []
    for lines, tier in items:
        clean = [l.strip() for l in lines if l.strip()]
        if not clean:
            continue
        has_user = any(l.startswith("{{user}}:") for l in clean)
        if has_user:
            text = "\n".join(clean)
        else:
            text = " ".join(clean)
            if not text.startswith("{{char}}:"):
                text = "{{char}}: " + text
        result.append(MESExample(text=text, tier=tier))
    return result


def _parse_uncertainty_block(content: str) -> list[str]:
    """Parse UNC[...] -- uncertainty voice examples separated by ---."""
    return [ex.text for ex in _parse_mes_block(content)]


def _parse_arc_block(content: str) -> list[ArcPhaseAST]:
    """Parse ARC{phase → conditions; voice: ...}."""
    phases = []
    # Split on phase declarations: lines with → or ->
    current_name = ""
    current_conditions: dict = {}
    current_voice = ""
    current_deflection = ""
    current_condition_str = ""
    _last_field = "voice"

    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Phase declaration: "name → conditions"
        arrow_match = re.match(r"^(\w+)\s*[→\->]+\s*(.*)$", stripped)
        if arrow_match:
            # Save previous phase if any
            if current_name:
                phases.append(
                    ArcPhaseAST(
                        name=current_name,
                        conditions=current_conditions,
                        condition_str=current_condition_str,
                        voice=current_voice,
                        deflection=current_deflection,
                    )
                )
            current_name = arrow_match.group(1)
            raw_cond = arrow_match.group(2).strip()
            current_condition_str = _normalize_condition_str(raw_cond)
            current_conditions = _parse_conditions(raw_cond)
            current_voice = ""
            current_deflection = ""
            _last_field = "voice"
            continue

        # Voice line within a phase
        voice_match = re.match(r"^voice:\s*(.+)$", stripped)
        if voice_match:
            current_voice = voice_match.group(1).strip().strip("\"'")
            _last_field = "voice"
            continue

        # Deflection line within a phase
        defl_match = re.match(r"^deflection:\s*(.+)$", stripped)
        if defl_match:
            current_deflection = defl_match.group(1).strip().strip("\"'")
            _last_field = "deflection"
            continue

        # Continuation of last field
        if current_name and not stripped.startswith(("voice:", "deflection:")):
            if _last_field == "deflection" and current_deflection:
                current_deflection += " " + stripped
            elif _last_field == "voice" and current_voice:
                current_voice += " " + stripped
            elif ":" not in stripped:
                # Additional condition line
                current_conditions.update(_parse_conditions(stripped))

    # Save last phase
    if current_name:
        phases.append(
            ArcPhaseAST(
                name=current_name,
                conditions=current_conditions,
                condition_str=current_condition_str,
                voice=current_voice,
                deflection=current_deflection,
            )
        )

    return phases


def _normalize_condition_str(cond_str: str) -> str:
    """Convert effigy condition syntax to unified condition DSL.

    Effigy syntax: 'trust>=0.2 AND fact:X AND ruin>=4'
    DSL syntax:    'trust:_NPC_>=0.2 AND fact:X AND ruin>=4'

    The NPC ID is not known here (it's the file-level character), so we use
    a placeholder '_NPC_' that the caller must replace.
    """
    if not cond_str.strip():
        return ""
    parts = re.split(r"\s+AND\s+", cond_str.strip())
    dsl_parts = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # trust>=0.2 -> trust:_NPC_>=0.2 (trust needs NPC scoping in DSL)
        if re.match(r"^trust\s*[><=]", part):
            dsl_parts.append(f"trust:_NPC_{part[5:]}")
        else:
            # All other conditions pass through unchanged:
            # numeric conditions (ruin>=4, tension>=0.5), fact:X, flag:X
            dsl_parts.append(part)
    return " AND ".join(dsl_parts)


def _parse_conditions(cond_str: str) -> dict:
    """Parse condition string like 'trust>=0.2 AND fact:knows_her_name'."""
    conditions: dict = {}
    # Split on AND
    parts = re.split(r"\s+AND\s+", cond_str.strip())
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # trust>=0.2
        num_match = re.match(r"(\w+)\s*([><=!]+)\s*([\d.]+)", part)
        if num_match:
            key = num_match.group(1)
            op = num_match.group(2)
            val = float(num_match.group(3))
            conditions[key] = {"op": op, "value": val}
            continue
        # fact:some_fact_id
        fact_match = re.match(r"fact:(\S+)", part)
        if fact_match:
            conditions.setdefault("facts", []).append(fact_match.group(1))
            continue
        # ruin>=4 (already caught by num_match above)
        # Fallback: store as raw
        if part:
            conditions["raw"] = conditions.get("raw", [])
            conditions["raw"].append(part)

    return conditions


def _parse_goals_block(content: str) -> list[GoalAST]:
    """Parse GOALS{name weight; grows_with: ...}."""
    goals = []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # "keep_peace  0.9  → grows with trust"
        parts = stripped.split()
        if not parts:
            continue

        name = parts[0]
        weight = 0.5
        grows_with = ""

        for _i, p in enumerate(parts[1:], 1):
            with contextlib.suppress(ValueError):
                weight = float(p)

        # Look for → grows with
        arrow_idx = stripped.find("→")
        if arrow_idx >= 0:
            grows_text = stripped[arrow_idx + 1 :].strip()
            grows_match = re.match(r"grows\s+with\s+(.+)", grows_text)
            if grows_match:
                grows_with = grows_match.group(1).strip()

        goals.append(GoalAST(name=name, weight=weight, grows_with=grows_with))

    return goals


def _parse_secrets_block(content: str) -> list[SecretAST]:
    """Parse SECRETS[L1: secret text | condition | era; ...]."""
    items = _split_items(content)
    secrets = []
    for item in items:
        kv = _parse_kv_block(item)
        # Support both "L1: ..." format and "layer: 1" format
        layer = 1
        secret_text = ""
        condition = ""
        era = ""

        if "layer" in kv:
            layer = int(kv["layer"])
        if "secret" in kv:
            secret_text = kv["secret"]
        if "reveal" in kv:
            condition = kv["reveal"]
        if "era" in kv:
            era = kv["era"]

        # Compact format: "L1: secret text"
        for key, val in kv.items():
            lmatch = re.match(r"^L(\d)$", key)
            if lmatch:
                layer = int(lmatch.group(1))
                secret_text = val
                break

        if secret_text:
            secrets.append(
                SecretAST(
                    layer=layer,
                    secret=secret_text,
                    reveal_condition=condition,
                    related_era=era,
                )
            )

    return secrets


def _parse_rels_block(content: str) -> list[RelationshipAST]:
    """Parse RELS{target type intensity "notes"}."""
    rels = []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # "town_mayor protects 0.6 "Owes her a favor.""
        # Or kv: "target: npc_b, type: suspects, intensity: 0.7, notes: ..."
        kv = _parse_kv_line(stripped)
        if kv and kv[0] == "target":
            # Multi-line kv format — gather lines
            block_kv = _parse_kv_block(stripped)
            rels.append(
                RelationshipAST(
                    target=block_kv.get("target", ""),
                    rel_type=block_kv.get("type", ""),
                    intensity=float(block_kv.get("intensity", "0.5")),
                    notes=block_kv.get("notes", ""),
                )
            )
            continue

        # Compact format: "target type intensity notes..."
        parts = stripped.split(None, 3)
        if len(parts) >= 2:
            target = parts[0]
            rel_type = parts[1]
            intensity = 0.5
            notes = ""
            if len(parts) >= 3:
                try:
                    intensity = float(parts[2])
                except ValueError:
                    notes = parts[2]
            if len(parts) >= 4:
                notes = parts[3].strip("\"'")
            rels.append(
                RelationshipAST(
                    target=target,
                    rel_type=rel_type,
                    intensity=intensity,
                    notes=notes,
                )
            )

    return rels


def _parse_behaviors_block(content: str) -> dict[str, str]:
    """Parse BEHAVIORS{goal_name: behavior description, ...}.

    Maps goal names (matching GOALS entries) to behavioral descriptions
    of what the character does when that goal is active. Used by
    intentions_context to give the LLM concrete guidance on what an
    active intention looks like in practice.

    Supports multiline values (continuation lines indented under the key).
    """
    return _parse_kv_block(content)


def _parse_sched_block(content: str) -> ScheduleAST:
    """Parse SCHED{morning: loc, afternoon: loc, ...}."""
    kv = _parse_kv_block(content)
    return ScheduleAST(
        morning=kv.get("morning") or None,
        afternoon=kv.get("afternoon") or None,
        evening=kv.get("evening") or None,
        night=kv.get("night") or None,
    )


def _parse_era_block(content: str) -> list[EraStateAST]:
    """Parse ERA[era_id status age occupation; ...]."""
    items = _split_items(content)
    eras = []
    for item in items:
        kv = _parse_kv_block(item)
        age_str = kv.get("age", "")
        age = int(age_str) if age_str and age_str.isdigit() else None
        eras.append(
            EraStateAST(
                era_id=kv.get("era", kv.get("era_id", "")),
                status=kv.get("status", "alive"),
                age=age,
                occupation=kv.get("occupation", ""),
                disposition=kv.get("disposition", ""),
                notes=kv.get("notes", ""),
            )
        )
    return eras


def _parse_dm_block(content: str) -> DrivermapAST:
    """Parse DM{trait: +/-, ..., features: a, b, c}."""
    kv = _parse_kv_block(content)
    profile: dict[str, str] = {}
    features: list[str] = []

    for key, val in kv.items():
        if key == "features":
            features = [f.strip() for f in val.split(",") if f.strip()]
        else:
            # Drivermap trait: key is trait name, val is +/-/neutral
            profile[key] = val

    return DrivermapAST(profile=profile, situation_features=features)


def _parse_lines_block(content: str) -> list[str]:
    """Parse ARRIVE[...] or DEPART[...] — line list separated by ---."""
    items = _split_items(content)
    result = []
    for item in items:
        # Each item may have multiple lines — join them
        lines = [l.strip() for l in item.strip().split("\n") if l.strip()]
        text = " ".join(lines) if lines else ""
        if text:
            result.append(text)
    return result


def _parse_wrong_block(content: str) -> list[WrongExampleAST]:
    """Parse WRONG[...] -- anti-pattern examples separated by ---.

    Each entry has optional fields:
      # comment (context description)
      {{user}}: prompt text
      WRONG: "wrong response"
      RIGHT: "correct response"
      WHY: explanation
    """
    items = _split_items(content)
    result: list[WrongExampleAST] = []

    for item in items:
        context = ""
        wrong = ""
        right = ""
        why = ""

        for line in item.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                # Comment line -- use as context description
                context = line.lstrip("# ").strip()
            elif line.startswith("{{user}}:"):
                context = line
            elif line.startswith("WRONG:"):
                wrong = line[6:].strip().strip('"')
            elif line.startswith("RIGHT:"):
                right = line[6:].strip().strip('"')
            elif line.startswith("WHY:"):
                why = line[4:].strip()

        if wrong:
            result.append(
                WrongExampleAST(
                    context=context,
                    wrong=wrong,
                    right=right,
                    why=why,
                )
            )

    return result


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def parse(text: str) -> CharacterAST:
    """Parse .effigy notation text into a CharacterAST.

    Raises ParseError on invalid notation.
    """
    state = _ParserState(text=text)
    ast = CharacterAST()

    while not state.at_end():
        state.skip_ws_and_newlines()
        if state.at_end():
            break

        # Comments
        if state.peek() == "#":
            state.skip_line()
            continue

        # Header fields: @id, @name, etc.
        if state.peek() == "@":
            _parse_header(state, ast)
            continue

        # Block keywords
        word = _peek_word(state)
        if word in ("VOICE",):
            state.advance(len(word))
            content = _read_braced_block(state)
            ast.voice = _parse_voice_block(content)
        elif word == "TRAITS":
            state.advance(len(word))
            content = _read_bracketed_block(state)
            ast.traits = _parse_traits_block(content)
        elif word == "NEVER":
            state.advance(len(word))
            content = _read_bracketed_block(state)
            ast.never_would_say = _parse_lines_block(content)
        elif word == "QUIRKS":
            state.advance(len(word))
            content = _read_bracketed_block(state)
            ast.quirks = _parse_lines_block(content)
        elif word == "MES":
            state.advance(len(word))
            content = _read_bracketed_block(state)
            ast.mes_examples = _parse_mes_block(content)
        elif word == "UNC":
            state.advance(len(word))
            content = _read_bracketed_block(state)
            ast.uncertainty_voice = _parse_uncertainty_block(content)
        elif word == "ARC":
            state.advance(len(word))
            content = _read_braced_block(state)
            ast.arc_phases = _parse_arc_block(content)
        elif word == "GOALS":
            state.advance(len(word))
            content = _read_braced_block(state)
            ast.goals = _parse_goals_block(content)
        elif word == "SECRETS":
            state.advance(len(word))
            content = _read_bracketed_block(state)
            ast.secrets = _parse_secrets_block(content)
        elif word == "RELS":
            state.advance(len(word))
            content = _read_braced_block(state)
            ast.relationships = _parse_rels_block(content)
        elif word == "SCHED":
            state.advance(len(word))
            content = _read_braced_block(state)
            ast.schedule = _parse_sched_block(content)
        elif word == "ERA":
            state.advance(len(word))
            content = _read_bracketed_block(state)
            ast.era_states = _parse_era_block(content)
        elif word == "DM":
            state.advance(len(word))
            content = _read_braced_block(state)
            ast.drivermap = _parse_dm_block(content)
        elif word == "ARRIVE":
            state.advance(len(word))
            content = _read_bracketed_block(state)
            ast.arrival_lines = _parse_lines_block(content)
        elif word == "DEPART":
            state.advance(len(word))
            content = _read_bracketed_block(state)
            ast.departure_lines = _parse_lines_block(content)
        elif word == "WRONG":
            state.advance(len(word))
            content = _read_bracketed_block(state)
            ast.wrong_examples = _parse_wrong_block(content)
        elif word == "PROPS":
            state.advance(len(word))
            content = _read_bracketed_block(state)
            ast.props = _parse_lines_block(content)
        elif word == "BEHAVIORS":
            state.advance(len(word))
            content = _read_braced_block(state)
            ast.goal_behaviors = _parse_behaviors_block(content)
        else:
            # Unknown — skip line
            state.skip_line()

    return ast


def _peek_word(state: _ParserState) -> str:
    """Peek at the next word without consuming."""
    start = state.pos
    while start < len(state.text) and state.text[start] in " \t":
        start += 1
    end = start
    while end < len(state.text) and state.text[end].isalpha():
        end += 1
    return state.text[start:end]


def _parse_header(state: _ParserState, ast: CharacterAST) -> None:
    """Parse a header line like '@id test_innkeeper'."""
    state.expect("@")
    # Read the header key
    key_start = state.pos
    while not state.at_end() and state.text[state.pos] not in " \t\n":
        state.pos += 1
    key = "@" + state.text[key_start : state.pos]
    state.skip_whitespace()
    value = state.read_line().strip()

    if key == "@id":
        ast.char_id = value
    elif key == "@name":
        ast.name = value
    elif key == "@role":
        ast.role = value
    elif key == "@arch":
        ast.archetype = value
    elif key == "@narr":
        try:
            ast.narrative_role = NarrativeRole(value)
        except ValueError:
            logger.debug("Unknown narrative role %r, defaulting to NEUTRAL", value)
            ast.narrative_role = NarrativeRole.NEUTRAL
    elif key == "@presence":
        ast.presence_note = value
    elif key == "@tropes":
        ast.trope_tags = [t.strip() for t in value.split(",") if t.strip()]
    elif key == "@theme":
        ast.theme = value


def parse_file(path: str) -> CharacterAST:
    """Parse a .effigy file from disk."""
    from pathlib import Path

    text = Path(path).read_text(encoding="utf-8")
    return parse(text)
