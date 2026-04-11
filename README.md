# Effigy

Dense character notation for LLM-driven NPCs.

You write one file. Effigy compiles it into a runtime prompt context that knows what your character would say, what she'd deflect, which version of herself she is right now, and what it costs her to change.

*If you just want to install it: [Installation](#installation). [Quick Start](#quick-start). I won't be offended.*

---

## Here. This is me:

```
# Effigy v0.2 -- test fixture
@id test_innkeeper
@name Dael Renn
@role innkeeper
@arch ca_guardian
@narr neutral
@presence Behind the bar, drying a glass that's already dry.
@tropes ca_guardian, np_local_authority
@theme The line between keeping the peace and keeping people quiet

VOICE{
  kernel: Measured, warm, evasive. Sentences start open, end clipped. Deflects with hospitality — not because she doesn't know, but because knowing is what keeps her useful.
  peak: The warmth drops. Words slow. She stops reaching for the glass. Whatever she's about to say, it costs her something.
}

TRAITS[
  observant, strategic-listener, hospitality-as-intelligence, loyal-to-a-fault,
  stubbornly-neutral, reads-the-room-before-she-reads-the-menu
]

NEVER[
  Never gossips — gossip is ammunition you waste
  ---
  Never raises her voice — volume is a loss of control she can't afford
  ---
  Never admits she overheard anything directly — plausible deniability is the only protection she has
  ---
  Never serves a drink without noticing who's watching
]

QUIRKS[
  Polishes a glass that's already clean — the routine is the mask
  ---
  Refills drinks mid-sentence — keeps people talking, keeps her close enough to hear
  ---
  Glances at the door when she wants someone to leave — or when she's checking who might walk in
  ---
  Adjusts the ledger under the counter when she thinks no one's looking
]

MES[
{{char}}: *sets down a glass that didn't need setting down* What brings you out this way? We don't get many new faces.
---
{{char}}: *wipes the bar without looking up* Folks around here keep to themselves. I just pour the drinks. *refills yours without being asked*
---
{{char}}: You ask a lot of questions for someone just passing through. *slides a menu across, holds it a beat too long* Hungry?
]

UNC[
{{char}}: Couldn't say. I just work the bar. *but her eyes track to the back room for half a second*
---
{{char}}: *shrugs, polishes the glass* That's above my pay grade.
]

ARC{
  guarded → trust>=0.0
    voice: "Polite distance. Service-mode deflections. 'I just pour the drinks' — and she almost believes it."
  thawing → trust>=0.3 AND fact:knows_her_name
    voice: "Less guarded. Pauses before deflecting. Starting to let you see that she notices things she shouldn't."
  open → trust>=0.6 AND ruin>=3
    voice: "Direct. Tired of pretending she doesn't know. The glass stops moving."
}

GOALS{
  keep_peace         0.8
  protect_regulars   0.7
  help_newcomer      0.3   → grows with trust
  tell_truth         0.2   → grows with evidence
}

BEHAVIORS{
  keep_peace: Redirects heat with hospitality. Refills drinks to cut off arguments before they start.
  protect_regulars: Never names them. Changes the subject when the conversation turns toward anyone she's seen in the back room.
  help_newcomer: Offers the seat with the best view of the door. Watches how they handle silence.
  tell_truth: Stops polishing the glass. Waits until the room empties. Says it once.
}

SECRETS[
layer: 1
secret: I overheard the mayor arguing with a group of merchants last month. Voices carry when people think the barkeep isn't listening.
reveal: When trust builds and she stops performing.
era: present
---
layer: 2
secret: I keep a ledger — who meets whom, when, what they ordered, how long they stayed. It started as inventory. It's not inventory anymore.
reveal: High trust and evidence that the newcomer can be trusted with something dangerous.
era: present
---
layer: 3
secret: The old fisher is the one who told me what the mayor's really doing. If I tell the newcomer about the mayor, the trail leads back to him. Helping you means exposing the one person who trusts me without conditions.
reveal: Only when the cost of silence is worse than the cost of speaking.
era: present
]

RELS{
  town_mayor protects 0.6 "Owes me a favor. Thinks that makes us even. It doesn't."
  old_fisher trusts 0.8 "Tells me everything. Doesn't know I write it down."
  newcomer assesses 0.3 "Watching. First person in years who might actually deserve the truth."
  deputy_cole tolerates 0.4 "Drinks too much. Talks too loud. Useful."
}

SCHED{
  morning: inn
  afternoon: market
  evening: inn
  night: inn
}

ERA[
era: founding
status: unborn
---
era: present
status: alive
age: 45
occupation: Innkeeper, The Wayward Pint
disposition: Guarded but fair. Runs a tight ship. Knows more than anyone in town and has built her entire life around making sure no one realizes that.
notes: Took over the inn from her mother ten years ago. Learned early that the person who pours the drinks hears everything, and the person who hears everything is the person no one can afford to cross.
]

DM{
  big_five_O: -
  big_five_C: +
  big_five_A: +
  features: routine, familiarity, social_observation
}

ARRIVE[
*nods from behind the bar, already pouring* Evening.
---
*looks up from the ledger, slides it under the counter* Take a seat anywhere.
]

DEPART[
*turns back to the glasses, but her hand finds the ledger first*
---
*raises a hand* Safe travels. *watches you leave through the reflection in the glass she's polishing*
]

PROPS[
  the glass she's always polishing, the ledger under the counter, the stool with the good view of the back room, the bell above the door
]

WRONG[
{{user}}: What did the mayor talk about last night?
WRONG: "Oh yes, the mayor met with the Henderson family last Tuesday at 7pm. They discussed the land deal and I heard every word."
RIGHT: "The mayor comes in sometimes. I pour the drinks, I don't take notes. *wipes the bar* Another round?"
WHY: Dael never volunteers information directly — not because she doesn't have it, but because giving it away for free is how you stop being useful and start being dangerous. She trades in silence. The deflection IS the character.
]
```

That file is the character. Everything else is what you do with it.

---

## Architecture

```
Layer 1 (compile-time):  .effigy notation  -->  parser.py (AST)  -->  expand.py (JSON)
Layer 2 (runtime):       AST + game state  -->  prompt.py (dialogue context)
Layer 3 (evolution):     AST + history     -->  evolve.py (emotional state, intentions)
```

**Layer 1** parses the `.effigy` source into a `CharacterAST`, then expands it to a portable JSON structure. Run it once. Check it in. The output is deterministic.

**Layer 2** is what runs on every turn. It takes the AST and your current game state — trust score, known facts, turn count, arbitrary state variables — and emits an optimized prompt context. The right arc phase. The right goals. The right examples, selected for this moment. The WRONG blocks stay out of the output. The secrets that haven't been earned stay quiet.

Here's what Layer 2 produces for Dael at `trust=0.3`, `fact:knows_her_name`:

```xml
<presence>Behind the bar, drying a glass that's already dry.</presence>

<voice>
  <kernel>Measured, warm, evasive. Sentences start open, end clipped. Deflects with hospitality — not because she doesn't know, but because knowing is what keeps her useful.</kernel>
  <peak>The warmth drops. Words slow. She stops reaching for the glass. Whatever she's about to say, it costs her something.</peak>
</voice>

<voice_examples canonical="true">
  {{char}}: *sets down a glass that didn't need setting down* What brings you out this way? We don't get many new faces.
  {{char}}: *wipes the bar without looking up* Folks around here keep to themselves. I just pour the drinks. *refills yours without being asked*
</voice_examples>

<never>
  - Never gossips — gossip is ammunition you waste
  - Never raises her voice — volume is a loss of control she can't afford
  - Never admits she overheard anything directly — plausible deniability is the only protection she has
  - Never serves a drink without noticing who's watching
</never>

<quirks>
  - Polishes a glass that's already clean — the routine is the mask
  - Refills drinks mid-sentence — keeps people talking, keeps her close enough to hear
  - Glances at the door when she wants someone to leave — or when she's checking who might walk in
  - Adjusts the ledger under the counter when she thinks no one's looking
</quirks>

<props>
  the glass she's always polishing, the ledger under the counter, the stool with the good view of the back room, the bell above the door
</props>

<relationships>
  <rel target="town_mayor" type="protects" intensity="0.6">Owes me a favor. Thinks that makes us even. It doesn't.</rel>
  <rel target="old_fisher" type="trusts" intensity="0.8">Tells me everything. Doesn't know I write it down.</rel>
  <rel target="newcomer" type="assesses" intensity="0.3">Watching. First person in years who might actually deserve the truth.</rel>
  <rel target="deputy_cole" type="tolerates" intensity="0.4">Drinks too much. Talks too loud. Useful.</rel>
</relationships>

<traits>observant, strategic-listener, hospitality-as-intelligence, loyal-to-a-fault, stubbornly-neutral, reads-the-room-before-she-reads-the-menu</traits>

<drivermap>big_five_O-, big_five_C+, big_five_A+</drivermap>

<arc_phase name="thawing">
  <voice_shift>Less guarded. Pauses before deflecting. Starting to let you see that she notices things she shouldn't.</voice_shift>
</arc_phase>

<active_goals>
  <goal weight="0.8" name="keep_peace">Redirects heat with hospitality. Refills drinks to cut off arguments before they start.</goal>
  <goal weight="0.7" name="protect_regulars">Never names them. Changes the subject when the conversation turns toward anyone she's seen in the back room.</goal>
</active_goals>

<voice_examples rotating="true">
  {{char}}: You ask a lot of questions for someone just passing through. *slides a menu across, holds it a beat too long* Hungry?
</voice_examples>

<voice_reminder>Measured, warm, evasive. Sentences start open, end clipped. Deflects with hospitality — not because she doesn't know, but because knowing is what keeps her useful.</voice_reminder>
```

That fits in a system prompt. It's meant to.

**Layer 3** handles evolution. Emotional state as a function of inputs — instability, pressure, accumulated facts. Intention modeling. What she wants this turn, and why that's shifted from what she wanted three turns ago.

---

## Notation Syntax

Header fields go at the top, one per line:

| Field | Purpose |
|---|---|
| `@id` | Unique character identifier |
| `@name` | Display name |
| `@role` | Narrative role |
| `@arch` | Character archetype |
| `@narr` | Narrator stance |
| `@presence` | Physical/spatial grounding line |
| `@tropes` | Applicable trope tags |
| `@theme` | The thing this character is actually about |

Then the blocks:

| Block | Contents |
|---|---|
| `VOICE{}` | kernel (baseline voice) + peak (stressed voice) |
| `TRAITS[]` | Comma-separated behavioral traits |
| `NEVER[]` | Hard behavioral constraints |
| `QUIRKS[]` | Physical/behavioral tells |
| `MES[]` | Dialogue exemplars, trust-tier gated |
| `UNC[]` | Uncertainty/deflection responses |
| `ARC{}` | Trust/fact/state-variable-gated arc phases |
| `GOALS{}` | Weighted goals, some grow with trust/evidence |
| `BEHAVIORS{}` | Goal-name → behavioral description (what active goals look like) |
| `SECRETS[]` | Layered secrets with reveal conditions |
| `RELS{}` | Directed NPC relationship graph |
| `PROPS[]` | Concrete grounding objects |
| `SCHED{}` | Time-of-day location schedule |
| `ERA[]` | Multi-era character state |
| `DM{}` | Drivermap personality profile |
| `WRONG[]` | Anti-pattern Wrong/Right/Why examples |
| `ARRIVE[]` | Entrance lines |
| `DEPART[]` | Exit lines |

The full working fixture is at [`effigy/tests/fixtures/test_npc.effigy`](effigy/tests/fixtures/test_npc.effigy).

---

## Installation

```bash
git clone https://github.com/justinstimatze/effigy.git
cd effigy
pip install -e .
```

Or directly:

```bash
pip install git+https://github.com/justinstimatze/effigy.git
```

Zero runtime dependencies. Python 3.11+.

---

## Quick Start

**Parse:**

```python
from effigy.parser import parse, parse_file
ast = parse(open("innkeeper.effigy").read())
# or: ast = parse_file("innkeeper.effigy")
print(ast.char_id)          # "test_innkeeper"
print(ast.voice.kernel)     # "Measured, warm, evasive. Sentences start..."
print(len(ast.arc_phases))  # 3
```

**Expand to JSON:**

```python
from effigy.expand import expand
data = expand(ast)
```

**Build a dialogue context:**

```python
from effigy.prompt import build_dialogue_context
ctx = build_dialogue_context(ast, trust=0.3, known_facts={"knows_her_name"}, turn=5, state_vars={"ruin": 2})
```

**Evolve:**

```python
from effigy.evolve import build_evolution_context, compute_emotional_state
state = compute_emotional_state(ast, known_facts=facts, emotional_inputs={"instability": 0.5})
ctx = build_evolution_context(ast, trust=0.3, state_vars={"ruin": 2})
```

---

## Concepts

**State variables** are arbitrary named values your game engine tracks — `trust`, `ruin`, `days_since_event`, whatever your world requires. They gate ARC phase transitions and can inflate or suppress goal weights at runtime. You define the names; effigy evaluates the conditions. The system doesn't care what `ruin` means. It only cares when the threshold is crossed.

**Emotional inputs** feed Layer 3's `compute_emotional_state`. They're normalized floats that represent pressure on the character in the current moment — instability, threat level, fatigue, unresolved obligation. Effigy combines them with the character's drivermap and known facts to produce an emotional state object that shifts what the character is inclined toward, without overwriting who she is. The baseline holds. The pressure shows at the edges.

---

## CLI

```
python -m effigy compile character.effigy
python -m effigy expand character.effigy
python -m effigy context character.effigy --trust 0.3 --state "ruin=2" --facts "fact_a,fact_b"
python -m effigy evaluate character.effigy original.json
python -m effigy evaluate-all ./effigy_files/ ./corpus_jsons/ --char-map map.json
python -m effigy metrics ./effigy_files/ ./corpus_jsons/ --char-map map.json
```

---

## API Reference

| Module | Exports |
|---|---|
| `effigy.parser` | `parse`, `parse_file`, `ParseError` |
| `effigy.notation` | `CharacterAST`, `VoiceAST`, `ArcPhaseAST`, `GoalAST`, `SecretAST`, `RelationshipAST`, `PostProcRuleAST`, `...` |
| `effigy.expand` | `expand`, `expand_to_json` |
| `effigy.prompt` | `build_dialogue_context`, `build_static_context`, `build_dynamic_state`, `build_dialogue_context_debug`, `resolve_arc_phase`, `resolve_active_goals`, `get_arc_phase_dict`, `select_mes_examples`, `select_canonical_mes`, `select_rotating_mes`, `get_wrong_examples` |
| `effigy.evolve` | `compute_emotional_state`, `EmotionalState`, `compute_intentions`, `build_evolution_context`, `build_synthesis_prompt` |
| `effigy.evaluate` | `evaluate_effigy_file`, `evaluate_tier1`, `evaluate_all`, `wrong_bleed_score`, `voice_drift_score`, `compliance_check`, `evaluate_generation` |
| `effigy.validators` | `RegexValidator`, `ValidationViolation`, `validate`, `strip_violations`, `validators_from_ast`, `has_blocking_violation`, `revise_if_violated` |
| `effigy.metrics` | `measure_character`, `CorpusMetrics` |
| `effigy.corpus` | `load_corpus`, `CharacterSpec` |
| `effigy.discovery` | `run_discovery` |

---

## Integration

See [INTEGRATION.md](INTEGRATION.md) for game engine hookups, turn-loop patterns, and trust management. The Voice Authoring Guide covers how to write VOICE kernels that hold under pressure, WRONG blocks that actually prevent drift, and MES exemplars worth selecting.

---

## Influences

Effigy's direct ancestors are the PList and Ali:Chat structured character card formats from the SillyTavern community — the observation that a flat, dense character specification in a system prompt outperforms freeform prose descriptions. Effigy extends that insight with a runtime layer, a condition DSL, and a notation purpose-built for characters who change.

| Source | Link | What it contributed | Where it shows |
|---|---|---|---|
| Valve/Ruskin GDC 2012 | [gdcvault.com](https://www.gdcvault.com/play/1015528/) | Fuzzy pattern-matched dialogue rules (Left 4 Dead) | ARC conditions |
| Larian BG3 | — | Dialogue flags for state-gated conversation | Fact-gated arc conditions |
| Inworld AI | [inworld.ai](https://inworld.ai/) | Identity / Knowledge / Goals / Memory as separate character components | Layer decomposition |
| FEAR / Jeff Orkin GDC 2006 | [gdcvault.com](https://gdcvault.com/play/1013282/) | Goal-oriented action planning (GOAP) | GOALS |
| Park et al. 2023 — Generative Agents | [arxiv.org](https://arxiv.org/abs/2304.03442) | Memory retrieval as recency × importance × relevance | Memory synthesis |
| Shao et al. 2023 — Character-LLM | [arxiv.org](https://arxiv.org/abs/2310.10158) | Protective experiences for out-of-character refusal | NEVER, WRONG |
| Xu et al. 2025 — A-Mem | [arxiv.org](https://arxiv.org/abs/2502.12110) | Agentic memory that evolves as new notes link to old | Emotional state |
| Naughty Dog writers' room | — | Writing many more lines than you'll use, then selecting | MES curation |
| SillyTavern community (PList + Ali:Chat) | — | Structured character cards for LLM prompting | Block-based notation |

What effigy does that these don't:

- Trust-gated MES selection
- WRONG anti-pattern examples
- Three-layer runtime architecture
- ARC with condition DSL
- PROPS for grounding
- Discovery loop for notation format search

---

*\*pauses before answering, sets the glass down\**

The fixture is at [`effigy/tests/fixtures/test_npc.effigy`](effigy/tests/fixtures/test_npc.effigy). You know where to find me.

Voices carry when people think the barkeep isn't listening. That's the whole idea. You write down who meets whom, what they ordered, how long they stayed. At some point it stops being inventory. Effigy is a system for encoding that — the observation, the categorization, the decision about who deserves what, and when. The character knows more than she says. The runtime decides what to surface. The WRONG blocks are there because giving it all away for free is how you stop being useful and start being dangerous.

---

*This README was generated by [`generate_readme.py`](generate_readme.py) using effigy's `build_dialogue_context()` as the character prompt.*

MIT — see [LICENSE](LICENSE).
