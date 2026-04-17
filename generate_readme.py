#!/usr/bin/env python3
"""Generate README.md from the test fixture effigy using effigy's own Layer 2.

The effigy is the character. build_dialogue_context() is the prompt.
An LLM call produces the README. The generator provides facts, not prose.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from effigy.expand import expand
from effigy.parser import parse
from effigy.prompt import (
    build_dialogue_context,
    filter_ast_by_state,
    resolve_arc_phase,
)

EFFIGY_PATH = Path(__file__).parent / "effigy" / "tests" / "fixtures" / "test_npc.effigy"
DEFAULT_MODEL = "claude-sonnet-4-6"


def _call_llm(system: str, user: str, model: str) -> str:
    """Call Claude via Anthropic SDK. Requires ANTHROPIC_API_KEY in .env or environment."""
    # Load .env if present
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)

    try:
        import anthropic
    except ImportError:
        print("pip install anthropic", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=16384,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text


def _extract_facts(ast, raw_text: str) -> dict:
    """Extract all structured facts from the effigy and codebase.

    Returns a dict of facts the LLM should weave into the README.
    No prose — just data.
    """
    # Phase-sliced contexts (v0.5.x). Each section of the README gets a
    # context filtered to the state it narrates — no competing signal
    # from low-trust MES during the open-phase closing, etc. voice_override
    # replaces the kernel so the phase voice dominates primacy AND recency.
    phase_states = [
        ("guarded", 0.0, set(), {"ruin": 1}),
        ("thawing", 0.3, {"knows_her_name"}, {"ruin": 2}),
        ("open", 0.7, {"knows_her_name"}, {"ruin": 4}),
    ]

    phases: dict[str, str] = {}
    phase_contexts: dict[str, str] = {}
    for name, trust, facts, sv in phase_states:
        p = resolve_arc_phase(ast, trust, known_facts=facts, state_vars=sv)
        phase_voice = p.voice if p else ""
        if phase_voice:
            phases[name] = phase_voice
        filtered = filter_ast_by_state(ast, trust, state_vars=sv, known_facts=facts)
        phase_contexts[name] = build_dialogue_context(
            filtered, trust=trust, known_facts=facts, turn=5, state_vars=sv,
            voice_override=phase_voice or None,
            voice_reminder_override=phase_voice or None,
        ).strip()

    # The thawing context is the one shown verbatim in the Architecture
    # section — it's the "middle" state a reader can calibrate against.
    ctx = phase_contexts["thawing"]

    # Module docstrings (introspected, not hardcoded)
    import effigy.parser, effigy.expand, effigy.prompt, effigy.evolve
    import effigy.evaluate, effigy.metrics, effigy.corpus, effigy.discovery
    import effigy.validators
    modules = {
        "effigy.parser": {"doc": (effigy.parser.__doc__ or "").split("\n")[0], "exports": ["parse", "parse_file", "ParseError"]},
        "effigy.notation": {"doc": "AST node definitions", "exports": ["CharacterAST", "VoiceAST", "ArcPhaseAST", "GoalAST", "SecretAST", "RelationshipAST", "PostProcRuleAST", "TestAST", "NeverRuleAST", "..."]},
        "effigy.expand": {"doc": (effigy.expand.__doc__ or "").split("\n")[0], "exports": ["expand", "expand_to_json"]},
        "effigy.prompt": {"doc": (effigy.prompt.__doc__ or "").split("\n")[0], "exports": ["build_dialogue_context", "build_static_context", "build_dynamic_state", "build_dialogue_context_debug", "filter_ast_by_state", "validate_when_conditions", "resolve_arc_phase", "resolve_active_goals", "get_arc_phase_dict", "select_mes_examples", "select_canonical_mes", "select_rotating_mes", "get_wrong_examples", "get_tests", "validate_never_budget"]},
        "effigy.evolve": {"doc": (effigy.evolve.__doc__ or "").split("\n")[0], "exports": ["compute_emotional_state", "EmotionalState", "compute_intentions", "build_evolution_context", "build_synthesis_prompt"]},
        "effigy.evaluate": {"doc": (effigy.evaluate.__doc__ or "").split("\n")[0], "exports": ["evaluate_effigy_file", "evaluate_tier1", "evaluate_all", "wrong_bleed_score", "voice_drift_score", "compliance_check", "evaluate_generation"]},
        "effigy.validators": {"doc": (effigy.validators.__doc__ or "").split("\n")[0], "exports": ["RegexValidator", "ValidationViolation", "validate", "strip_violations", "validators_from_ast", "has_blocking_violation", "revise_if_violated"]},
        "effigy.metrics": {"doc": (effigy.metrics.__doc__ or "").split("\n")[0], "exports": ["measure_character", "CorpusMetrics"]},
        "effigy.corpus": {"doc": (effigy.corpus.__doc__ or "").split("\n")[0], "exports": ["load_corpus", "CharacterSpec"]},
        "effigy.discovery": {"doc": (effigy.discovery.__doc__ or "").split("\n")[0], "exports": ["run_discovery"]},
    }

    # CLI commands (introspected from cli.py argparse)
    cli_commands = [
        "python -m effigy compile character.effigy",
        "python -m effigy expand character.effigy",
        'python -m effigy context character.effigy --trust 0.3 --state "ruin=2" --facts "fact_a,fact_b"',
        "python -m effigy evaluate character.effigy original.json",
        "python -m effigy evaluate-all ./effigy_files/ ./corpus_jsons/ --char-map map.json",
        "python -m effigy metrics ./effigy_files/ ./corpus_jsons/ --char-map map.json",
    ]

    return {
        "effigy_source": raw_text.strip(),
        "layer2_output": ctx.strip(),
        "phase_contexts": phase_contexts,
        "arc_phase_voices": phases,
        "modules": modules,
        "cli_commands": cli_commands,
        "code_examples": {
            "parse": 'from effigy.parser import parse, parse_file\nast = parse(open("innkeeper.effigy").read())\n# or: ast = parse_file("innkeeper.effigy")\nprint(ast.char_id)          # "' + ast.char_id + '"\nprint(ast.voice.kernel)     # "' + (ast.voice.kernel[:40] if ast.voice else '') + '..."\nprint(len(ast.arc_phases))  # ' + str(len(ast.arc_phases)),
            "expand": "from effigy.expand import expand\ndata = expand(ast)",
            "context": 'from effigy.prompt import build_dialogue_context\nctx = build_dialogue_context(ast, trust=0.3, known_facts={"knows_her_name"}, turn=5, state_vars={"ruin": 2})',
            "phase_slice": 'from effigy.prompt import filter_ast_by_state, build_dialogue_context, resolve_arc_phase\n\n# Prune @when-gated items that don\'t match the current state,\n# then let the phase voice dominate kernel AND voice_reminder.\nfiltered = filter_ast_by_state(ast, trust=0.7, state_vars={"ruin": 4})\nphase = resolve_arc_phase(ast, trust=0.7, state_vars={"ruin": 4})\nctx = build_dialogue_context(\n    filtered, trust=0.7, state_vars={"ruin": 4},\n    voice_override=phase.voice,\n    voice_reminder_override=phase.voice,\n)',
            "evolve": 'from effigy.evolve import build_evolution_context, compute_emotional_state\nstate = compute_emotional_state(ast, known_facts=facts, emotional_inputs={"instability": 0.5})\nctx = build_evolution_context(ast, trust=0.3, state_vars={"ruin": 2})',
        },
        "block_types": [
            ("VOICE{}", "kernel (baseline voice) + peak (stressed voice)"),
            ("TRAITS[]", "Comma-separated behavioral traits"),
            ("NEVER[]", "Hard behavioral constraints"),
            ("QUIRKS[]", "Physical/behavioral tells"),
            ("MES[]", "Dialogue exemplars, trust-tier gated"),
            ("UNC[]", "Uncertainty/deflection responses"),
            ("ARC{}", "Trust/fact/state-variable-gated arc phases"),
            ("GOALS{}", "Weighted goals, some grow with trust/evidence"),
            ("BEHAVIORS{}", "Goal-name → behavioral description (what active goals look like)"),
            ("SECRETS[]", "Layered secrets with reveal conditions"),
            ("RELS{}", "Directed NPC relationship graph"),
            ("PROPS[]", "Concrete grounding objects"),
            ("SCHED{}", "Time-of-day location schedule"),
            ("ERA[]", "Multi-era character state"),
            ("DM{}", "Drivermap personality profile"),
            ("WRONG[]", "Anti-pattern Wrong/Right/Why examples"),
            ("TEST[]", "Named reasoning tests with fail/pass examples and why"),
            ("ARRIVE[]", "Entrance lines"),
            ("DEPART[]", "Exit lines"),
        ],
        "annotations": [
            ("@tier", "Trust-tier gate on MES examples: low / moderate / high / any"),
            ("@when", "Condition DSL gate on MES, NEVER, WRONG, TEST items (same grammar as ARC conditions)"),
        ],
        "header_fields": ["@id", "@name", "@role", "@arch", "@narr", "@presence", "@tropes", "@theme"],
        "install": {
            "clone": "git clone https://github.com/justinstimatze/effigy.git\ncd effigy\npip install -e .",
            "direct": "pip install git+https://github.com/justinstimatze/effigy.git",
            "deps": "Zero runtime dependencies. Python 3.11+.",
        },
        "influences": [
            ("Valve/Ruskin GDC 2012", "https://www.gdcvault.com/play/1015528/", "Fuzzy pattern-matched dialogue rules (Left 4 Dead)", "ARC conditions"),
            ("Larian BG3", None, "Dialogue flags for state-gated conversation", "Fact-gated arc conditions"),
            ("Inworld AI", "https://inworld.ai/", "Identity / Knowledge / Goals / Memory as separate character components", "Layer decomposition"),
            ("FEAR / Jeff Orkin GDC 2006", "https://gdcvault.com/play/1013282/", "Goal-oriented action planning (GOAP)", "GOALS"),
            ("Park et al. 2023 — Generative Agents", "https://arxiv.org/abs/2304.03442", "Memory retrieval as recency × importance × relevance", "Memory synthesis"),
            ("Shao et al. 2023 — Character-LLM", "https://arxiv.org/abs/2310.10158", "Protective experiences for out-of-character refusal", "NEVER, WRONG"),
            ("Xu et al. 2025 — A-Mem", "https://arxiv.org/abs/2502.12110", "Agentic memory that evolves as new notes link to old", "Emotional state"),
            ("Naughty Dog writers' room", None, "Writing many more lines than you'll use, then selecting", "MES curation"),
            ("SillyTavern community (PList + Ali:Chat)", None, "Structured character cards for LLM prompting", "Block-based notation"),
        ],
        "novel": [
            "Trust-gated MES selection",
            "WRONG anti-pattern examples",
            "TEST blocks — named reasoning tests with fail/pass examples",
            "@when composable blocks — phase-sliced context via state-gated items",
            "Three-layer runtime architecture",
            "ARC with condition DSL",
            "PROPS for grounding",
            "Discovery loop for notation format search",
        ],
        "secret_layer1": ast.secrets[0].secret if ast.secrets else "",
        "departure_line": ast.departure_lines[0] if ast.departure_lines else "",
        "fixture_path": "effigy/tests/fixtures/test_npc.effigy",
    }


def main():
    ap = argparse.ArgumentParser(description="Generate README from the test fixture effigy")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true", help="Print prompts, don't call LLM")
    args = ap.parse_args()

    raw_text = EFFIGY_PATH.read_text()
    ast = parse(raw_text)
    facts = _extract_facts(ast, raw_text)

    # --- System prompt: character voice ---
    ctx_thawing = facts["layer2_output"]
    system = f"""You are {ast.name}, narrating a README.md for the "effigy" Python library.
Your voice, quirks, and behavioral rules come from the effigy file below.

YOUR BEHAVIORAL CONTEXT (generated by effigy's build_dialogue_context):

{ctx_thawing}

YOUR SOURCE FILE:

{raw_text}

ARC SHIFT across the README (each section is written AS IF the library
had sliced your context for that phase — guarded voice dominates the
opening, thawing voice dominates the middle, open voice dominates the
closing). This is not a stylistic suggestion, it's a structural one:
the library's v0.5 filter_ast_by_state + voice_override APIs exist
precisely so prompts don't mix phase signals:
- Opening: {facts['arc_phase_voices'].get('guarded', '')}
- Middle (after showing your effigy): {facts['arc_phase_voices'].get('thawing', '')}
- Closing (Influences and after): {facts['arc_phase_voices'].get('open', '')}

WRITING RULES:
- NEVER open with a stage direction. No stage directions AT ALL until after the
  effigy block. The reader needs to encounter the notation before the gestures
  make sense. The first gesture should arrive around the Architecture section,
  and it should feel like it slipped out, not like it was placed.
- Maximum 3-4 stage directions in the ENTIRE README. Each one should earn its
  spot. If you can cut a gesture without losing anything, cut it.
- No tricolon ("not X, not Y, it's Z"). No defensive negation.
- No transition filler. Start sections with content.
- Em-dashes only in character voice, not technical explanation.
- No overwrought metaphors. You deal in glasses, ledgers, doors, and corners.
- Short sentences in the opening. Clipped endings. Undersell.
- Sections don't need neat conclusions. Leave rough edges.
- Stage directions MUST use *\\*action\\** format (escaped asterisks). Be consistent.
  Never use bare *action* without the backslash escapes.
- The secret reveal should feel earned, not placed. Don't label it.
- The footer is one line. Don't explain the joke.

VOICE REFERENCES (how to write this character's prose):
- Kazuo Ishiguro (Remains of the Day): measured, evasive, hospitality as armor
- Elizabeth Strout (Olive Kitteridge): economy of dialogue, weight in what's unsaid
- Kent Haruf (Plainsong): short sentences, warmth in action not words
- Dennis Lehane (Mystic River): community insiders, silence as currency
- John le Carré (Tinker Tailor): the handler who lets silence do the work, information asymmetry
- Tana French (In the Woods): proximity, patience, slow accumulation of overheard things
- Daniel Woodrell (Winter's Bone): knowing the whole web of obligation and silence
- Patrick DeWitt (The Sisters Brothers): dry warm deflection, saying one thing and meaning its opposite"""

    # --- User prompt: structured facts, no prose ---
    user = f"""Write README.md for the effigy library. All prose is yours. The facts below are exact.

FACTS (use verbatim where marked):

{json.dumps(facts, indent=2, default=str)}

SECTIONS (in order):

1. TITLE: "# Effigy"
2. OPENING: "Dense character notation for LLM-driven NPCs." Then brief explanation in character.
   Then skip link exactly: *If you just want to install it: [Installation](#installation). [Quick Start](#quick-start). I won't be offended.*
3. EFFIGY BLOCK: "Here. This is me:" then the effigy_source in a code fence (VERBATIM).
4. ARCHITECTURE: Show this exact diagram in a code fence:
Layer 1 (compile-time):  .effigy notation  -->  parser.py (AST)  -->  expand.py (JSON)
Layer 2 (runtime):       AST + game state  -->  prompt.py (dialogue context)
Layer 3 (evolution):     AST + history     -->  evolve.py (emotional state, intentions)
Then brief explanation of each layer. Then the layer2_output in a code fence (VERBATIM).
Note it fits in a system prompt.
5. NOTATION SYNTAX: header_fields reference, block_types as markdown table.
Then a short "Annotations" subsection: render `annotations` as a second
small markdown table (two columns: annotation, purpose). One sentence
after it explaining how @when composes with @tier (both gate an item;
@when is the general form). Link to fixture_path.
6. INSTALLATION: install.clone and install.direct in code fences. install.deps as a note.
7. QUICK START: code_examples.parse, .expand, .context, .phase_slice, .evolve each in
code fences (VERBATIM). Between .context and .phase_slice, one sentence
only: something like "When the character reaches a different arc phase,
you don't want the earlier phase's voice competing for the model's
attention. Filter first, override the voice, stop arguing with yourself."
Stay in character — no API marketing voice.
8. CONCEPTS: state_vars and emotional_inputs, one paragraph each.
9. CLI: cli_commands in a single code fence (VERBATIM).
10. API REFERENCE: modules as markdown table.
11. INTEGRATION: Link to INTEGRATION.md, mention Voice Authoring Guide.
12. INFLUENCES: PList+Ali:Chat ancestors. influences as markdown table. novel as bullet list.
13. CLOSING: No "## Closing" header. Just your departure, the fixture link with
"You know where to find me", then your secret woven naturally. Connect what you
do (listen, record, decide who deserves what) to what this library does.
14. FOOTER: *This README was generated by [`generate_readme.py`](generate_readme.py) using effigy's `build_dialogue_context()` as the character prompt.*
15. LICENSE: MIT — see [LICENSE](LICENSE).

FORMAT:
- Raw markdown output, no wrapping code fence
- Stage directions: *\\*action\\**
- Code fences and the layer2_output block MUST be reproduced exactly as given in facts
- All code_examples MUST be reproduced exactly"""

    if args.dry_run:
        print("=== SYSTEM ===")
        print(system[:500] + "...")
        print(f"\nSystem: {len(system)} chars")
        print(f"User: {len(user)} chars")
        print(f"Facts JSON: {len(json.dumps(facts))} chars")
        return

    print(f"Generating README from {ast.name}'s effigy...")
    print(f"  Model: {args.model}")
    print(f"  Character context: {len(ctx_thawing)} chars")
    print(f"  Facts: {len(facts)} keys")

    readme = _call_llm(system, user, args.model)

    readme_path = Path(__file__).parent / "README.md"
    readme_path.write_text(readme.strip() + "\n")
    print(f"  Written to {readme_path} ({len(readme)} chars)")


if __name__ == "__main__":
    main()
