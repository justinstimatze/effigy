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
# Raw context injection (XML-tagged sections: <voice>, <never>, <quirks>,
# <traits>, <arc_phase>, <active_goals>, <voice_examples>, etc.)
if effigy_context:
    prompt += f"\n\n{effigy_context}"
```

(The arc phase voice shift is already embedded in `effigy_context` as
`<arc_phase name="..."><voice_shift>...</voice_shift></arc_phase>`. You
don't need to inject it separately. Effigy also appends a
`<voice_reminder>` at the end of the dynamic block — the kernel repeated
right before generation to counter lost-in-the-middle attention decay.)

## 2.5 Phase-Sliced Context (v0.5.x)

When a character reaches a late arc phase, the default voice kernel and
guarded-phase MES examples still sit at the top (primacy) and bottom
(recency) of the rendered prompt. The `<arc_phase><voice_shift>` block
is in the middle — the worst position for LLM attention. Even with a
clean phase voice definition, the model generates the default voice.

The library-supported fix: pre-filter the AST by state, and override the
voice kernel + voice_reminder with the active phase voice. One pattern,
applied in your bridge module:

```python
# yourgame/effigy_bridge.py — v0.5.x

def effigy_dialogue_context(char_id, trust, known_facts, turn=0, state_vars=None):
    """Phase-sliced prompt context string, or "" on any failure."""
    try:
        ast = _get_ast(char_id)
        if ast is None:
            return ""
        from effigy.prompt import (
            build_dialogue_context,
            filter_ast_by_state,
            resolve_arc_phase,
        )
        # Prune @when-gated items that don't match current state so
        # off-phase examples don't compete for attention.
        filtered = filter_ast_by_state(
            ast, trust=trust, state_vars=state_vars, known_facts=known_facts
        )
        # Let the active phase voice dominate kernel AND reminder so it
        # doesn't fight the baseline voice at primacy/recency.
        phase = resolve_arc_phase(
            ast, trust=trust, state_vars=state_vars, known_facts=known_facts
        )
        override = phase.voice if phase and phase.voice else None
        return build_dialogue_context(
            filtered, trust=trust, known_facts=known_facts,
            turn=turn, state_vars=state_vars,
            voice_override=override,
            voice_reminder_override=override,
        )
    except Exception:
        return ""
```

What this changes in the rendered prompt at resolved phase:

| Position | Section | Before | After |
|---|---|---|---|
| TOP | `<voice><kernel>` | default voice | **resolved phase voice** |
| NEAR TOP | `<voice_examples canonical>` | default MES | **phase-filtered MES** |
| MIDDLE | `<arc_phase><voice_shift>` | resolved | resolved |
| BOTTOM | `<voice_reminder>` | default voice | **resolved phase voice** |

All four positions now carry resolved-phase signal. Zero competing default-voice content.

### Authoring @when-gated blocks

Gate MES, NEVER, WRONG, and TEST items to game state with `@when`. Syntax
matches ARC phase conditions exactly:

```
MES[
# Always shown — baseline voice, regardless of phase.
{{char}}: Coffee's fresh if you want it. Cream's in the tin.
---

@when trust<0.3
{{user}}: What happened at the mine?
{{char}}: Fourteen men. *wipes counter* Sixty-two years ago, hon. Pie?
---

@when trust>=0.6 AND ruin>=4
{{user}}: Betty, the photo...
{{char}}: I served their sons breakfast. *not moving* Ray's father. Tom's father.
]

NEVER[
Never invents NPC names
---
@when trust<0.4
Never interrogates — deflect through physical action
---
@when trust>=0.6
Never uses displacement gestures — stillness only at this phase
]
```

The condition grammar supports `trust` and any named state variable
(`ruin`, `heat`, `tension`, …) with `>=`, `<=`, `>`, `<`, `==`, `!=`,
plus `fact:foo` checks and `AND` conjunction. Effigy evaluates this
standalone — no external DSL library required. For richer grammar
(`OR`, `NOT`, parentheses), effigy falls through to `stope.conditions`
when importable.

### Migration table

For teams with narrator-side phase adjustments, each row is a pattern to
move out of the narrator and into the effigy:

| Narrator-side workaround | Library-supported replacement |
|---|---|
| "IGNORE the MES examples above" override at resolved phase | `filter_ast_by_state` prunes @when-gated items |
| `voice_override` param in your narrator call | `build_dialogue_context(voice_override=phase.voice)` |
| MES-swapping by game state inside the narrator | `@when` gates inside the MES block |
| Phase-specific NEVER rules in the narrator prompt | `@when`-gated NEVER rules (frees NEVER budget) |
| Beat detection for MES selection | v0.6.0 `@group` + classifier (planned) |

### Pre-commit validation

Catch `@when` typos at author time instead of at runtime (where the
silent failure would be "item disappears"):

```python
# scripts/check_effigy.py
import sys
from pathlib import Path
from effigy.parser import parse
from effigy.prompt import validate_when_conditions, validate_never_budget

errors: list[str] = []
for path in Path("corpus/chars").glob("*.effigy"):
    ast = parse(path.read_text())
    for err in validate_when_conditions(ast):
        errors.append(f"{path}: {err}")
    for warn in validate_never_budget(ast):
        errors.append(
            f"{path}: {warn['total']} NEVER rules exceeds cap of {warn['cap']}"
        )

if errors:
    for e in errors:
        print(e)
    sys.exit(1)
```

Wire into CI or a pre-commit hook. `validate_when_conditions` flags both
unparseable conditions and conditions with unrecognized state-variable
tokens that fell through to the `raw` bucket.

## 3. Narrator System Prompt

**This is the critical step most integrations miss.** The LLM receives effigy
sections as injected text, but without system-level guidance it doesn't know
how to weight them. Add this to your dialogue system prompt:

```
BEHAVIORAL DOSSIER (XML tags in the effigy block below):
- <voice> contains the character's core voice. <kernel> is the
  non-negotiable baseline; <peak> (when present) is how the voice shifts
  at emotional peaks. A <voice_reminder> tag appears at the END of the
  block — treat it as the final authority on voice immediately before
  generating.
- <never> rules are HARD RULES. The character never does these things,
  period. Not even under pressure. Violating a <never> is a voice break.
- <arc_phase name="..."> tells you where the character is emotionally.
  Its <voice_shift> AUGMENTS the static voice — the base voice stays,
  colored by the phase.
- <traits> inform how the character acts, not what they literally say.
  Let traits shape choices and subtext.
- <quirks> are physical habits. Weave ONE in naturally per response —
  don't force all of them every time.
- <active_goals> drive what the character wants from this conversation.
  The <goal> body (when present) describes what pursuing that goal
  actually LOOKS LIKE in practice — subtext and motivation, not
  explicit dialogue.
- <voice_examples> tags (canonical and rotating) contain curated sample
  lines from this character. These are the strongest voice anchor in
  the whole block — match their cadence, word choice, sentence length.
- <presence> (when present) is a one-line opener about the character's
  physical/mood presence. Lightly inform your first gesture.
- <drivermap> (when present) is a compressed motivation profile.
```

Why each instruction matters:

| Tag | Without guidance | With guidance |
|---|---|---|
| `<never>` | LLM treats as soft suggestion | Hard constraint, enforced |
| `<arc_phase>` | LLM may ignore or override base voice | Augments base voice (both active) |
| `<traits>` | LLM narrates traits literally | Traits inform behavior subtly |
| `<quirks>` | All quirks every response, or ignored | One quirk per response, natural |
| `<active_goals>` | Stated as dialogue content | Drive subtext and motivation |
| `<voice_examples>` | LLM may ignore in favor of abstract rules | Strongest voice anchor, matched |
| `<voice_reminder>` | Voice drifts over long block | Re-anchored before generation |

## What `build_dialogue_context()` Produces

The runtime context function selects only currently-relevant data and emits
it as XML-tagged sections. The block splits into two parts with different
caching properties:

- **Static prefix** (`build_static_context`): byte-stable for a given
  `.effigy` file — cache-eligible as a prompt prefix.
- **Dynamic tail** (`build_dynamic_state`): depends on trust/turn/state —
  rebuilt every call.

Example output for a character in their "thawing" arc phase:

```xml
<presence>Behind the bar, drying a glass that's already dry.</presence>

<voice>
  <kernel>Measured, warm, evasive. Sentences start open, end clipped. Deflects with hospitality.</kernel>
  <peak>The warmth drops. Words slow. Whatever she's about to say, it costs her something.</peak>
</voice>

<voice_examples canonical="true">
  {{char}}: *sets down a glass that didn't need setting down* What brings you out this way?
  {{char}}: *wipes the bar without looking up* I just pour the drinks.
</voice_examples>

<never>
  - Never gossips about regulars
  - Never raises her voice
</never>

<quirks>
  - Polishes the same glass when nervous
  - Refills drinks mid-sentence as a redirect
  - Glances at the door when she wants someone to leave
</quirks>

<traits>observant, private, loyal, deflects-with-hospitality, stubborn, perceptive</traits>

<arc_phase name="thawing">
  <voice_shift>Less guarded. Pauses before deflecting.</voice_shift>
</arc_phase>

<active_goals>
  <goal weight="0.8" name="keep_peace">Redirects heat with hospitality.</goal>
  <goal weight="0.7" name="protect_regulars">Never names them. Changes subject.</goal>
</active_goals>

<voice_examples rotating="true">
  {{char}}: You ask a lot of questions for someone just passing through.
</voice_examples>

<voice_reminder>Measured, warm, evasive. Sentences start open, end clipped. Deflects with hospitality.</voice_reminder>
```

This is typically 500-900 tokens — about 0.3-0.5x the size of the full
character JSON, because it only includes what's relevant to the current
game state. The static prefix is roughly 60-80% of the total and stays
byte-identical across turns, making it cache-eligible.

### Getting observability for free

Use `build_dialogue_context_debug` instead to get a debug dict alongside
the context string:

```python
from effigy.prompt import build_dialogue_context_debug

ctx, debug = build_dialogue_context_debug(
    ast, trust=0.3, known_facts={"knows_her_name"},
    turn=5, state_vars={"ruin": 2},
)
# debug["static"]["sections"] -> emitted section names
# debug["static"]["never_total"], debug["static"]["never_rendered"],
#   debug["static"]["never_dropped"], debug["static"]["never_critical_count"]
# debug["dynamic"]["arc_phase"], debug["dynamic"]["active_goals"]
# debug["total_chars"], debug["static_chars"], debug["dynamic_chars"]
logger.info("effigy char=%s debug=%s", char_id, debug)
```

Log the debug dict alongside each generation call to enable post-hoc
correlation of context configurations with voice-adherence scores.

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
assert "<traits>" in ctx
assert "<never>" in ctx

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
- Effigy already appends a `<voice_reminder>` at the end of the dynamic block every turn — it's the kernel repeated immediately before generation. For characters with a `voice.peak_when` condition, the reminder auto-swaps to `<peak>` voice when the condition is true.
- Taper older conversation snippets to topic-only summaries (reduces copyable pattern signal)
- For hard rules that MUST be enforced, don't rely on prompt instructions — add a `POSTPROC[...]` block to the `.effigy` file and use `effigy.validators.validate` + `strip_violations` (or `revise_if_violated`) after generation. Stochastic output deserves a deterministic filter.

### 5. Use voice_override to replace the static kernel at late arc phases

When the LLM sees both the static voice kernel AND an arc phase voice shift, the static one wins — it's at primacy (top of prompt) AND usually echoed at recency (voice_reminder) while the phase shift sits in the middle. Starting in v0.4.1, `build_dialogue_context()` accepts `voice_override` and `voice_reminder_override` so the phase voice dominates both positions. See "Phase-Sliced Context" above for the code pattern. Don't try to solve this in the narrator prompt — the signal math doesn't shift unless you remove the competing content from the prompt itself.

### 6. Gate phase-scoped NEVER rules with @when, not with narrator overrides

NEVER rules in high-priority prompt blocks override arc phase voice in lower-priority blocks. The LLM resolves the conflict by deflecting ("Documentation of what?") — technically obeying NEVER but violating the arc's intent. Starting in v0.5.1, mark phase-specific NEVER rules with `@when` gates so they only appear in prompts where they apply: `@when trust<0.4\nNever interrogates`. `filter_ast_by_state` prunes them before rendering. This frees your NEVER budget and removes the conflict at the source, without adding narrator-side override directives.

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

### 11. @when-gated blocks beat prompt-level workarounds

If your narrator has code that says "at resolved phase, use MES set B instead of MES set A" or "inject this override string when trust >= 0.6" — move it into the effigy. Author the phase-specific content as `@when`-gated MES/NEVER/WRONG/TEST items and let `filter_ast_by_state` produce a pre-filtered AST. The result is less narrator complexity, more testable authoring (the effigy IS the source of truth), and strictly less competing signal in the prompt.

### 12. TEST blocks give the LLM a reasoning framework, not just rules

A `TEST[...]` block — `name:`, `question:`, `fail:` examples, `pass:` examples, `why:` — outperforms equivalent NEVER rules because it teaches the model *how to think about* the failure mode rather than pattern-match it. Use TEST blocks for voice qualities that live in the gap between "always" and "never" — metaphor technique, deflection style, information control, composure. Keep `MAX_TESTS = 5` per character; they're higher per-line attention cost than NEVER rules.
