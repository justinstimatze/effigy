# Effigy Integration Guide

How to wire effigy behavioral dossiers into a game's dialogue system.

Effigy pairs well with [drivermap](https://github.com/justinstimatze/drivermap) — effigy handles voice and behavioral constraints, drivermap handles psychological drivers and situation-based behavior prediction. The `DM{}` block in effigy notation stores the drivermap profile directly, and `build_dialogue_context()` output is designed for injection alongside drivermap's `behavioral_style_ctx` and `player_model_ctx`.

## Architecture

```
.effigy files  →  parser.py (AST)  →  prompt.py (runtime context)
                                    →  expand.py (corpus JSON)
```

Three integration points:

1. **Bridge module** — loads/caches ASTs, exposes functions, graceful degradation
2. **Engine wiring** — calls bridge at dialogue time with current game state
3. **Narrator system prompt** — teaches the LLM what the effigy sections mean

## 1. Bridge Module

Follow the lazy-load + graceful degradation pattern. The game must be fully
playable without effigy installed.

```python
# yourgame/effigy_bridge.py

from pathlib import Path

_CORPUS_DIR = Path(__file__).parent / "corpus" / "chars"
_ast_cache: dict[str, object] = {}
_load_failed: bool = False

def _get_ast(char_id: str):
    global _load_failed
    if char_id in _ast_cache:
        return _ast_cache[char_id]
    if _load_failed:
        return None
    path = _CORPUS_DIR / f"{char_id}.effigy"
    if not path.exists():
        return None
    try:
        from effigy.parser import parse
        ast = parse(path.read_text())
        _ast_cache[char_id] = ast
        return ast
    except Exception:
        _load_failed = True
        return None

def effigy_dialogue_context(char_id, trust, known_facts, turn=0, state_vars=None):
    """Returns prompt context string, or "" on any failure."""
    try:
        ast = _get_ast(char_id)
        if ast is None:
            return ""
        from effigy.prompt import build_dialogue_context
        return build_dialogue_context(ast, trust=trust, known_facts=known_facts,
                                       turn=turn, state_vars=state_vars)
    except Exception:
        return ""

def effigy_arc_phase(char_id, trust, known_facts, state_vars=None):
    """Returns {name, voice, conditions} dict, or None."""
    try:
        ast = _get_ast(char_id)
        if ast is None:
            return None
        from effigy.prompt import get_arc_phase_dict
        return get_arc_phase_dict(ast, trust=trust, known_facts=known_facts,
                                   state_vars=state_vars)
    except Exception:
        return None
```

Key rules:
- Every public function returns `""` or `None` on failure. Zero exceptions escape.
- ASTs are cached per session (one parse per NPC per process lifetime).
- `_load_failed` flag prevents repeated import attempts if effigy isn't installed.
- Imports are inside functions (lazy) so the bridge module itself always imports.

## 2. Engine Wiring

At dialogue time, call the bridge with current game state:

```python
# In your dialogue handler:
effigy_context = ""
effigy_phase = None
try:
    from yourgame.effigy_bridge import effigy_dialogue_context, effigy_arc_phase
    effigy_context = effigy_dialogue_context(
        npc_id, trust, known_facts, turn, state_vars=state_vars
    )
    effigy_phase = effigy_arc_phase(
        npc_id, trust, known_facts, state_vars=state_vars
    )
except Exception:
    pass

# Pass both to your narrator/dialogue generation function
response = narrate_dialogue(
    ...,
    effigy_context=effigy_context,    # str, may be ""
    arc_phase=effigy_phase,            # dict or None
)
```

In the prompt assembly, inject the context and arc voice separately:

```python
# Raw context injection (BEHAVIORAL TRAITS, NEVER, QUIRKS, GOALS, etc.)
if effigy_context:
    prompt += f"\n\n{effigy_context}"

# Arc phase voice augments the static voice_kernel
if arc_phase and arc_phase.get("voice"):
    prompt += f"\n\nARC VOICE SHIFT ({arc_phase['name'].upper()} phase): {arc_phase['voice']}"
```

## 3. Narrator System Prompt

**This is the critical step most integrations miss.** The LLM receives effigy
sections as injected text, but without system-level guidance it doesn't know
how to weight them. Add this to your dialogue system prompt:

```
BEHAVIORAL DOSSIER (if provided below):
- NEVER constraints are HARD RULES. The character never does these things,
  period. Not even under pressure. Violating a NEVER is a voice break.
- CHARACTER ARC PHASE describes where the character is emotionally. The voice
  shift AUGMENTS the static voice — the base voice stays, colored by the phase.
- BEHAVIORAL TRAITS inform how the character acts, not what they say. Let
  traits shape choices and subtext.
- BEHAVIORAL QUIRKS are physical habits. Weave one in naturally per response —
  don't force all of them every time.
- ACTIVE GOALS drive what the character wants from this conversation. They
  create subtext and motivation, not explicit dialogue.
- VOICE REINFORCEMENT repeats the core voice. Anchor to it.
```

Why each instruction matters:

| Section | Without guidance | With guidance |
|---------|-----------------|---------------|
| NEVER | LLM treats as soft suggestion | Hard constraint, enforced |
| ARC PHASE | LLM may ignore or override base voice | Augments base voice (both active) |
| TRAITS | LLM narrates traits literally | Traits inform behavior subtly |
| QUIRKS | All quirks every response, or ignored | One quirk per response, natural |
| GOALS | Stated as dialogue content | Drive subtext and motivation |

## What `build_dialogue_context()` Produces

The runtime context function selects only currently-relevant data. Example
output for a character in their "thawing" arc phase:

```
CHARACTER ARC PHASE: THAWING
Voice shift: Less guarded. Pauses before deflecting.

ACTIVE GOALS (what this character is trying to accomplish):
  - keep_peace (priority: 0.8)
  - protect_regulars (priority: 0.7)

BEHAVIORAL TRAITS: observant, private, loyal, deflects-with-hospitality,
stubborn, perceptive

VOICE REINFORCEMENT: Measured, warm, evasive. Sentences start open, end
clipped. Deflects with hospitality.

NEVER (this character would NEVER):
  - Never gossips about regulars
  - Never raises her voice

BEHAVIORAL QUIRKS:
  - Polishes the same glass when nervous
  - Refills drinks mid-sentence as a redirect
  - Glances at the door when she wants someone to leave

THEMATIC ROLE: The cost of knowing everyone's secrets
```

This is typically 500-650 tokens — about 0.3-0.4x the size of the full
character JSON, because it only includes what's relevant to the current
game state.

## File Layout

```
yourgame/
  corpus/chars/
    npc_one.effigy       # canonical .effigy source files
    npc_two.effigy
    npc_one.json          # existing JSON (effigy supplements, doesn't replace)
  effigy_bridge.py        # bridge module

effigy/                   # the library (separate package)
  parser.py
  prompt.py
  expand.py
  notation.py
  test-notations/         # dev/test .effigy files (fallback location)
```

The bridge checks the game's corpus directory first, falling back to
effigy's test-notations for library development.

## Testing

Run effigy's own tests to verify parsing and expansion:

```bash
cd effigy && python -m pytest tests/ -v
```

Roundtrip tests verify that every `.effigy` file parses and expands to
match the corresponding JSON on key fields (char_id, name, role,
relationships, secrets, schedule).

To verify bridge integration:

```python
from yourgame.effigy_bridge import effigy_dialogue_context, effigy_arc_phase

ctx = effigy_dialogue_context("test_innkeeper", trust=0.3,
                               known_facts={"knows_her_name"}, turn=5,
                               state_vars={"ruin": 2})
assert "BEHAVIORAL TRAITS" in ctx
assert "NEVER" in ctx

phase = effigy_arc_phase("test_innkeeper", trust=0.3,
                          known_facts={"knows_her_name"},
                          state_vars={"ruin": 2})
assert phase["name"] == "thawing"
```

## Voice Authoring Guide

Hard-won lessons from production use of effigy dossiers for LLM dialogue generation.

### 1. WRONG examples are more powerful than rules

LLMs weight few-shot examples more heavily than declarative constraints. Adding 4-5 WRONG/RIGHT/WHY triples to a character can produce dramatic voice score improvements. Each WRONG example must correctly identify the failure mode — misdiagnosing *why* an example is wrong teaches the LLM the wrong lesson.

### 2. MES examples must not contradict NEVER rules

If a MES dialogue example contains phrasing banned by a NEVER rule, the LLM will follow the example and violate the rule. Run `python -m effigy compile character.effigy` to check for contradictions. This is the most common source of "the character keeps doing the thing I told it not to do."

### 3. Trust-gated MES examples are essential, not optional

Showing the LLM low-trust dialogue examples (deflection, assessment) when generating high-trust responses (vulnerability, confession) causes register confusion that voice rules cannot fix. Use `@tier` annotations on MES examples and let `select_mes_examples()` filter by trust level.

### 4. Voice drifts over long conversations

Voice scores degrade significantly after 5-7 exchanges. Mitigations:
- Re-inject the VOICE REINFORCEMENT section in uncached prompt blocks at longer conversations
- Taper older conversation snippets to topic-only summaries (reduces copyable pattern signal)
- Re-extract NEVER rules from the effigy into later prompt blocks as a "VOICE DISCIPLINE" section

### 5. Arc voice should replace the static kernel, not augment it

When the LLM sees both the static voice kernel AND an arc phase voice shift, the static one wins (it's usually in a cached, high-priority prompt block). Your narrator should use the arc phase voice as the ONLY voice when a phase is active. The effigy library emits both sections — your engine decides which to use.

### 6. NEVER rules can suppress arc phase behavior

NEVER rules in high-priority prompt blocks override arc phase voice in lower-priority blocks. The LLM resolves the conflict by deflecting ("Documentation of what?") — technically obeying NEVER but violating the arc's intent. For high-trust arc phases (resolved, deciding), consider which NEVER rules should be relaxed, and inject override directives.

### 7. Quirks become tics without cycling

A character with 3 quirks will repeat the same one every exchange. Mitigations:
- Provide 5+ quirk variants per character
- Organize quirks by category (displacement, offering, observation, stillness)
- Gate signature quirks to specific arc phases where they carry emotional weight
- Your narrator should track and suppress recently-used quirks

### 8. PROPS ground characters without info-dumping

Adding concrete domain objects (equipment, supplies, personal items) as a PROPS section with "use naturally, do NOT info-dump" gives the LLM physical vocabulary without triggering inventory-listing behavior. This is one of the highest-impact additions for voice quality.

### 9. Validate your wiring

It is extremely easy for effigy context to be silently absent from the dialogue prompt. The output is plausible without it — just lower quality. Always verify that the prompt sent to the LLM actually contains the expected effigy sections (ARC PHASE, NEVER, TRAITS, etc.) before assuming the system is working.

### 10. The effigy should be the single source of truth

Voice rules, behavioral constraints, and arc phase logic should live in the `.effigy` file, not spread across your engine code. Splitting voice logic between the effigy and the consuming application creates contradiction bugs that are extremely hard to diagnose.
