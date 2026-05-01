"""Effigy CLI — compile, expand, evaluate, metrics.

Usage:
    python -m effigy.cli compile <file.effigy>     # parse and validate
    python -m effigy.cli validate <file.effigy>    # check NEVER budget + warnings
    python -m effigy.cli expand <file.effigy>      # expand to JSON
    python -m effigy.cli evaluate <file.effigy> <original.json>  # roundtrip fidelity
    python -m effigy.cli metrics <effigy_dir> <corpus_dir>       # compression metrics
    python -m effigy.cli evaluate-all <effigy_dir> <corpus_dir>  # evaluate all pairs
    python -m effigy.cli audit <effigy_dir>        # cross-character tic detection
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _check_mes_never_contradictions(ast) -> list[str]:
    """Check if MES examples contain text that contradicts NEVER rules.

    LLMs weight few-shot examples more heavily than rules. If a MES example
    contains phrasing banned by a NEVER rule, the LLM will follow the example.
    """
    if not ast.never_would_say or not ast.mes_examples:
        return []

    warnings: list[str] = []
    for never_rule in ast.never_would_say:
        # Extract key phrases from the NEVER rule (words > 4 chars)
        key_phrases = [w.lower() for w in never_rule.text.split() if len(w) > 4]
        for i, ex in enumerate(ast.mes_examples):
            ex_text = (ex.text if hasattr(ex, "text") else ex).lower()
            for phrase in key_phrases:
                if phrase in ex_text:
                    warnings.append(
                        f"MES example {i + 1} contains '{phrase}' "
                        f"which may contradict NEVER rule: {never_rule.text[:60]}"
                    )
                    break  # one warning per MES/NEVER pair
    return warnings


def cmd_compile(args: argparse.Namespace) -> None:
    """Parse and validate a .effigy file."""
    from effigy.parser import ParseError, parse

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        ast = parse(path.read_text(encoding="utf-8"))
    except ParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsed: {ast.char_id} ({ast.name})")
    print(f"  Role: {ast.role}")
    print(f"  Archetype: {ast.archetype}")
    print(f"  Narrative role: {ast.narrative_role.value}")
    print(f"  Voice: {'yes' if ast.voice else 'no'}")
    print(f"  MES examples: {len(ast.mes_examples)}")
    print(f"  Uncertainty: {len(ast.uncertainty_voice)}")
    print(f"  Arc phases: {len(ast.arc_phases)}")
    if ast.arc_phases:
        for phase in ast.arc_phases:
            print(f"    {phase.name}")
    print(f"  Goals: {len(ast.goals)}")
    print(f"  Secrets: {len(ast.secrets)}")
    print(f"  Relationships: {len(ast.relationships)}")
    print(f"  Era states: {len(ast.era_states)}")
    print(f"  Drivermap: {'yes' if ast.drivermap and ast.drivermap.profile else 'no'}")
    print(f"  Traits: {len(ast.traits)}")
    print(f"  Never-would-say: {len(ast.never_would_say)}")
    print(f"  Quirks: {len(ast.quirks)}")
    if ast.theme:
        print(f"  Theme: {ast.theme[:80]}")
    print(f"  Arrival lines: {len(ast.arrival_lines)}")
    print(f"  Departure lines: {len(ast.departure_lines)}")
    print(f"  Wrong examples: {len(ast.wrong_examples)}")
    print(f"  Tests: {len(ast.tests)}")
    print(f"  Props: {len(ast.props)}")
    print(f"  Goal behaviors: {len(ast.goal_behaviors)}")

    # Validate: MES examples should not contradict NEVER rules
    warnings = _check_mes_never_contradictions(ast)
    if warnings:
        print(f"\n  WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"    ! {w}")


def cmd_validate(args: argparse.Namespace) -> None:
    """Validate a .effigy file: NEVER budget + MES/NEVER contradictions +
    @when parse errors + @beat references."""
    from effigy.parser import ParseError, parse
    from effigy.prompt import (
        validate_beat_references,
        validate_never_budget,
        validate_when_conditions,
    )

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        ast = parse(path.read_text(encoding="utf-8"))
    except ParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        sys.exit(1)

    had_warning = False
    had_error = False

    for w in validate_never_budget(ast):
        had_warning = True
        n_dropped = len(w["dropped"])
        print(
            f"WARNING: {w['char_id']} has {w['total']} NEVER rules "
            f"(cap is {w['cap']})."
        )
        print(f"  {n_dropped} rule{'s' if n_dropped != 1 else ''} will be dropped at generation time.")
        print("  Dropped rules (by priority order):")
        for i, rule in enumerate(w["dropped"], start=w["cap"] + 1):
            preview = rule if len(rule) <= 80 else rule[:77] + "..."
            print(f"    [{i}] {preview}")
        print("  Consider: consolidate related rules, or promote critical ones with CRITICAL: prefix.")

    for err in validate_when_conditions(ast):
        had_error = True
        print(f"ERROR (@when): {err}")

    for msg in validate_beat_references(ast):
        if msg.startswith("ERROR"):
            had_error = True
        else:
            had_warning = True
        print(msg)

    contradictions = _check_mes_never_contradictions(ast)
    for warning in contradictions:
        had_warning = True
        print(f"WARNING: {warning}")

    if not had_warning and not had_error:
        print(f"OK: {ast.char_id} ({len(ast.never_would_say)} NEVER rules, within cap)")
        return

    if had_error or args.strict:
        sys.exit(1)


def cmd_expand(args: argparse.Namespace) -> None:
    """Expand a .effigy file to JSON."""
    from effigy.expand import expand_to_json
    from effigy.parser import ParseError, parse

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        ast = parse(path.read_text(encoding="utf-8"))
    except ParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        sys.exit(1)

    print(expand_to_json(ast, indent=2))


def cmd_evaluate(args: argparse.Namespace) -> None:
    """Evaluate roundtrip fidelity of a .effigy file against original JSON."""
    from effigy.evaluate import evaluate_effigy_file

    effigy_path = Path(args.effigy_file)
    json_path = Path(args.json_file)
    if not effigy_path.exists() or not json_path.exists():
        print("File not found", file=sys.stderr)
        sys.exit(1)

    result = evaluate_effigy_file(effigy_path, json_path)
    print(result.summary())
    print(f"\nTier 1 score: {result.tier1_score:.2%}")


def cmd_evaluate_all(args: argparse.Namespace) -> None:
    """Evaluate all .effigy files against their corpus counterparts."""
    import json as _json
    from effigy.evaluate import evaluate_all

    char_map: dict[str, str] = {}
    if args.char_map:
        char_map = _json.loads(Path(args.char_map).read_text())

    results = evaluate_all(args.effigy_dir, args.corpus_dir, char_map)
    if not results:
        print("No matching .effigy / .json pairs found.")
        return

    for result in results:
        print(result.summary())
        print()

    mean_score = sum(r.tier1_score for r in results) / len(results)
    print(f"Overall Tier 1: {mean_score:.2%} ({len(results)} characters)")


def cmd_context(args: argparse.Namespace) -> None:
    """Preview runtime dialogue context for a .effigy file."""
    from effigy.parser import ParseError, parse
    from effigy.prompt import build_dialogue_context, get_arc_phase_dict

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        ast = parse(path.read_text(encoding="utf-8"))
    except ParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        sys.exit(1)

    trust = args.trust
    facts = set(args.facts.split(",")) if args.facts else set()
    turn = args.turn
    state_vars: dict[str, float] = {}
    if args.state:
        for part in args.state.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                state_vars[k.strip()] = float(v.strip())

    ctx = build_dialogue_context(ast, trust=trust,
                                  known_facts=facts, turn=turn,
                                  state_vars=state_vars)
    phase = get_arc_phase_dict(ast, trust=trust,
                                known_facts=facts,
                                state_vars=state_vars)

    print(f"=== {ast.name} ({ast.char_id}) ===")
    print(f"State: trust={trust}, state_vars={state_vars}, facts={facts or '{}'}, turn={turn}")
    if phase:
        print(f"Arc phase: {phase['name']}")
        if phase.get("voice"):
            print(f"Voice shift: {phase['voice']}")
    print()
    if ctx:
        print(ctx)
    else:
        print("(no context generated — check arc phases and goals)")


def cmd_audit(args: argparse.Namespace) -> None:
    """Static cross-character tic detection over an effigy corpus."""
    import json as _json
    from effigy.audit import find_cross_character_tics, format_findings_table
    from effigy.parser import ParseError, parse

    paths: list[Path] = []
    for arg in args.paths:
        p = Path(arg)
        if p.is_dir():
            paths.extend(sorted(p.glob("*.effigy")))
        elif p.is_file():
            paths.append(p)
        else:
            print(f"Not found: {p}", file=sys.stderr)
            sys.exit(1)

    if not paths:
        print("No .effigy files found.", file=sys.stderr)
        sys.exit(1)

    asts = []
    seen_ids: dict[str, Path] = {}
    for path in paths:
        try:
            ast = parse(path.read_text(encoding="utf-8"))
        except ParseError as e:
            print(f"Parse error in {path}: {e}", file=sys.stderr)
            sys.exit(1)
        cid = ast.char_id or ast.name
        if cid and cid in seen_ids:
            print(
                f"Duplicate @id {cid!r} in {path} (first seen in {seen_ids[cid]}). "
                f"Each .effigy must have a unique @id.",
                file=sys.stderr,
            )
            sys.exit(1)
        if cid:
            seen_ids[cid] = path
        asts.append(ast)

    if len(asts) < 2:
        if args.json:
            print(_json.dumps({"corpus_size": len(asts), "findings": []}))
        else:
            print(f"Need at least 2 characters to audit (got {len(asts)}).")
        return

    try:
        findings = find_cross_character_tics(
            asts,
            min_share=args.min_share,
            min_total=args.min_total,
        )
    except ValueError as e:
        print(f"Audit error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        n = len(asts)
        payload = {
            "corpus_size": n,
            "min_share": args.min_share,
            "min_total": args.min_total,
            "findings": [
                {
                    "token": f.token,
                    "spread": f.spread(n),
                    "total": f.total,
                    "characters": f.characters,
                    "counts_per_character": f.counts_per_character,
                }
                for f in findings
            ],
        }
        print(_json.dumps(payload))
        return

    print(f"Audited {len(asts)} characters.")
    print(format_findings_table(findings, corpus_size=len(asts)))


def cmd_metrics(args: argparse.Namespace) -> None:
    """Measure compression metrics across effigy/corpus pairs."""
    import json as _json
    from effigy.metrics import CorpusMetrics, measure_character

    effigy_dir = Path(args.effigy_dir)
    corpus_dir = Path(args.corpus_dir)

    char_map: dict[str, str] = {}
    if args.char_map:
        char_map = _json.loads(Path(args.char_map).read_text())

    chars = []
    for effigy_file in sorted(effigy_dir.glob("*.effigy")):
        char_id = effigy_file.stem
        json_name = char_map.get(char_id, f"{char_id}.json")
        json_file = corpus_dir / json_name
        if not json_file.exists():
            continue
        chars.append(measure_character(
            char_id,
            json_file.read_text(),
            effigy_file.read_text(),
        ))

    if not chars:
        print("No matching pairs found.")
        return

    corpus = CorpusMetrics(characters=chars)
    print(corpus.summary())


def main():
    parser = argparse.ArgumentParser(
        description="Effigy — Dense Character Notation",
        prog="effigy",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # compile
    p_compile = subparsers.add_parser("compile", help="Parse and validate")
    p_compile.add_argument("file", help=".effigy file path")

    # validate
    p_validate = subparsers.add_parser(
        "validate", help="Validate authoring constraints (NEVER budget, MES/NEVER conflicts)"
    )
    p_validate.add_argument("file", help=".effigy file path")
    p_validate.add_argument(
        "--strict", action="store_true",
        help="Exit with code 1 if any warnings are reported",
    )

    # expand
    p_expand = subparsers.add_parser("expand", help="Expand to JSON")
    p_expand.add_argument("file", help=".effigy file path")

    # context
    p_ctx = subparsers.add_parser("context", help="Preview runtime dialogue context")
    p_ctx.add_argument("file", help=".effigy file path")
    p_ctx.add_argument("--trust", type=float, default=0.0, help="Trust level (0.0-1.0)")
    p_ctx.add_argument("--state", default="", help="State vars as key=val,... (e.g. ruin=4,tension=0.5)")
    p_ctx.add_argument("--facts", default="", help="Comma-separated known facts")
    p_ctx.add_argument("--turn", type=int, default=0, help="Turn number")

    # evaluate
    p_eval = subparsers.add_parser("evaluate", help="Evaluate roundtrip fidelity")
    p_eval.add_argument("effigy_file", help=".effigy file path")
    p_eval.add_argument("json_file", help="Original JSON file path")

    # evaluate-all
    p_eval_all = subparsers.add_parser("evaluate-all", help="Evaluate all pairs")
    p_eval_all.add_argument("effigy_dir", help="Directory of .effigy files")
    p_eval_all.add_argument("corpus_dir", help="Directory of corpus JSON files")
    p_eval_all.add_argument("--char-map", help="JSON file mapping char_id → filename")

    # metrics
    p_metrics = subparsers.add_parser("metrics", help="Compression metrics")
    p_metrics.add_argument("effigy_dir", help="Directory of .effigy files")
    p_metrics.add_argument("corpus_dir", help="Directory of corpus JSON files")
    p_metrics.add_argument("--char-map", help="JSON file mapping char_id → filename")

    # audit
    p_audit = subparsers.add_parser(
        "audit",
        help="Detect cross-character voice tics in an effigy corpus",
    )
    p_audit.add_argument(
        "paths", nargs="+",
        help="One or more .effigy files or directories",
    )
    p_audit.add_argument(
        "--min-share", type=float, default=0.3,
        help="Minimum corpus fraction sharing a token (default: 0.3)",
    )
    p_audit.add_argument(
        "--min-total", type=int, default=3,
        help="Minimum total occurrences across the corpus (default: 3)",
    )
    p_audit.add_argument(
        "--json", action="store_true",
        help="Emit findings as JSON instead of a text table",
    )

    args = parser.parse_args()

    commands = {
        "compile": cmd_compile,
        "validate": cmd_validate,
        "expand": cmd_expand,
        "context": cmd_context,
        "evaluate": cmd_evaluate,
        "evaluate-all": cmd_evaluate_all,
        "metrics": cmd_metrics,
        "audit": cmd_audit,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
