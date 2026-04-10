"""Effigy discovery loop — find the densest personality dossier format.

Adapted from the Dense Notation Format discovery loop for the character domain.
Instead of compressing JSON fields, this discovers the optimal
BEHAVIORAL DOSSIER — the minimum information an LLM needs to
generate in-character dialogue.

The core algorithm:
1. BASELINE: Generate test dialogues from full NPC JSON (ground truth)
2. PROPOSE: Ask the model to design a dense personality dossier format
3. DISTILL: Translate each NPC into the proposed dossier format
4. GENERATE: Generate test dialogues from ONLY the dossier
5. JUDGE: Score voice fidelity (dossier dialogues vs baseline)
6. EVOLVE: Feed specific failures back, improve the dossier format
7. Repeat for N rounds

Target: voice fidelity >= 0.85 at >= 3x compression.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from effigy.corpus import CharacterSpec, corpus_summary, load_corpus
from effigy.metrics import estimate_tokens

DEFAULT_MODEL = "claude-sonnet-4-6"
JUDGE_MODEL = "claude-sonnet-4-6"
RESULTS_DIR = Path.cwd() / "effigy-results"


# ---------------------------------------------------------------------------
# Test scenarios for dialogue generation
# ---------------------------------------------------------------------------

TEST_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "casual_low_trust",
        "prompt": "A newcomer approaches {name} and makes casual conversation. "
                  "They ask about the area and what there is to do.",
        "trust": 0.1,
        "context": "First meeting. The newcomer just arrived.",
    },
    {
        "id": "probing_moderate",
        "prompt": "The newcomer has been around for a day. They ask {name} "
                  "about a significant local event from the past.",
        "trust": 0.3,
        "context": "The newcomer has visited several local landmarks "
                   "and talked to a few people.",
    },
    {
        "id": "confrontation_high",
        "prompt": "The newcomer has discovered evidence that contradicts the official story. "
                  "They confront {name} directly with what they know.",
        "trust": 0.6,
        "context": "Late in the story. The newcomer has documentation "
                   "and has spoken to most characters.",
    },
]


# ---------------------------------------------------------------------------
# LLM call (via claude_agent_sdk)
# ---------------------------------------------------------------------------

def call_model(
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 8192,
    retries: int = 4,
    timeout: int = 300,
) -> tuple[str, int, int]:
    """Call Claude via the Python Agent SDK.

    Returns (response_text, input_tokens, output_tokens).
    """
    import anyio
    from claude_agent_sdk import ClaudeAgentOptions, query
    from claude_agent_sdk.types import AssistantMessage, TextBlock

    async def _call() -> str:
        text_parts: list[str] = []
        opts = ClaudeAgentOptions(
            system_prompt=system,
            model=model,
            tools=[],
            permission_mode="bypassPermissions",
            env={"CLAUDECODE": ""},
        )
        cancel_scope = anyio.CancelScope(deadline=anyio.current_time() + timeout)
        with cancel_scope:
            async for msg in query(prompt=user, options=opts):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
        if cancel_scope.cancelled_caught:
            raise RuntimeError(f"timed out after {timeout}s")
        return "".join(text_parts).strip()

    def _kill_orphaned() -> None:
        import signal as _sig
        import subprocess as _sp
        try:
            result = _sp.run(
                ["pgrep", "-P", str(os.getpid()), "-f", "_bundled"],
                capture_output=True, text=True,
            )
            for pid_str in result.stdout.splitlines():
                with contextlib.suppress(ValueError, ProcessLookupError):
                    os.kill(int(pid_str.strip()), _sig.SIGKILL)
        except Exception:
            pass

    last_err: Exception | None = None
    for attempt in range(retries):
        if attempt > 0:
            wait = 30 * attempt
            print(f"(retry {attempt}, waiting {wait}s)...", end=" ", flush=True)
            time.sleep(wait)
        try:
            text = anyio.run(_call)
            if not text:
                last_err = RuntimeError("empty response")
                continue
            return text, estimate_tokens(system + "\n" + user), estimate_tokens(text)
        except Exception as e:
            if "timed out" in str(e).lower():
                _kill_orphaned()
            last_err = e
            print(f"[err: {e}] ", end="", flush=True)

    raise last_err or RuntimeError("call_model failed")


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

PROPOSE_SYSTEM = """You are designing a maximally dense PERSONALITY DOSSIER format for NPCs.

Your goal is NOT to compress JSON fields. Your goal is to capture the behavioral
fingerprint of a character — the rules, patterns, quirks, and constraints that
let an LLM generate in-character dialogue without seeing the full character data.

A good dossier captures:
1. VOICE RULES — how they talk (rhythm, vocabulary, register shifts under stress)
2. BEHAVIORAL QUIRKS — observable habits, physical tells, idiosyncratic actions
3. NEVER-WOULD-SAY — negative constraints (what breaks character)
4. ARC DYNAMICS — how they change across trust levels (discrete phases)
5. PSYCHOLOGICAL DRIVERS — what they want, what they fear, what they protect
6. KEY EXEMPLARS — 2-3 dialogue lines that anchor the voice (not 8 verbatim)
7. THEMATIC ROLE — what viewpoint on the story's themes they embody

A BAD dossier:
- Copies all 8 dialogue examples verbatim (wasteful — rules > examples)
- Lists abstract trait labels without behavioral implications
- Preserves JSON structure instead of distilling behavioral patterns
- Includes lookup data (schedules, era dates) that doesn't affect dialogue voice

Target: ~300-500 tokens per character, capturing everything an LLM needs to
sound like that specific person. The test is: can someone generate 3 dialogue
samples from ONLY the dossier that a reader couldn't distinguish from the original?"""


DISTILL_SYSTEM = """You are a character analyst. You receive a full NPC data file and must
distill it into a dense personality dossier.

Your job is to extract the BEHAVIORAL ESSENCE — not copy fields, but identify:
- What makes this character's VOICE distinctive (rhythm, vocabulary, habits)
- What they would NEVER say or do (the constraints that prevent generic dialogue)
- Their observable QUIRKS (physical tells, verbal tics, habitual actions)
- Their psychological DRIVERS (goals, fears, loyalties, shame)
- How they CHANGE across trust levels (arc phases with voice shifts)
- 2-3 KEY dialogue lines that anchor the voice (choose the most distinctive ones)
- Their THEMATIC role in the story

Be specific and behavioral. "Warm but guarded" is useless. "Uses food metaphors.
Says 'honey' but means it. Refills coffee without asking as a trust signal.
Never sits down during service — the routine is her armor." is useful.

Output ONLY the dossier in the specified format."""


DIALOGUE_SYSTEM = """You are a dialogue generator for a narrative game.
A newcomer has arrived and is interacting with the local characters.

Generate a short dialogue exchange (3-5 lines from the NPC, with brief newcomer prompts).
Stay precisely in character based on the character information provided.
The NPC's voice, quirks, and behavioral patterns must be evident in every line.

Output ONLY the dialogue, no commentary."""


JUDGE_SYSTEM = """You are evaluating voice fidelity between two dialogue samples for the same NPC.

Sample A was generated from the character's FULL data (ground truth voice).
Sample B was generated from a CONDENSED personality dossier.

Score on these dimensions (0.0-1.0):

1. voice_match: Does B sound like the same person as A? Same rhythm, vocabulary, register?
2. quirk_fidelity: Does B show the same behavioral quirks, habits, physical tells?
3. constraint_adherence: Does B avoid things the character would never say/do?
4. emotional_accuracy: Does B show appropriate emotional state for the scenario?
5. distinctiveness: Would you know which character B is without being told?

Output ONLY a JSON object:
{
  "voice_match": <float>,
  "quirk_fidelity": <float>,
  "constraint_adherence": <float>,
  "emotional_accuracy": <float>,
  "distinctiveness": <float>,
  "overall": <float>,
  "notes": "<specific failures or strengths>"
}"""


# ---------------------------------------------------------------------------
# NPC data formatting
# ---------------------------------------------------------------------------

def _npc_full_context(spec: CharacterSpec) -> str:
    """Format full NPC JSON as context for dialogue generation."""
    d = spec.json_data
    lines = [
        f"Character: {d['name']} ({d.get('char_id', '')})",
        f"Role: {d.get('role', '')}",
        f"Voice: {d.get('voice_kernel', '')}",
    ]
    if d.get("peak_voice"):
        lines.append(f"Peak voice: {d['peak_voice']}")
    mes = d.get("mes_examples", [])
    if mes:
        lines.append("\nDialogue examples:")
        for ex in mes:
            lines.append(f"  {ex}")
    unc = d.get("uncertainty_voice", [])
    if unc:
        lines.append("\nUncertainty voice:")
        for u in unc:
            lines.append(f"  {u}")
    secrets = d.get("secrets", [])
    if secrets:
        lines.append("\nSecrets:")
        for s in secrets:
            lines.append(f"  Layer {s['layer']}: {s['secret'][:120]}")
    rels = d.get("relationships", [])
    if rels:
        lines.append("\nRelationships:")
        for r in rels:
            lines.append(f"  {r['target']}: {r['type']} — {r.get('notes', '')[:60]}")
    dm = d.get("drivermap_profile", {})
    if dm:
        traits = [f"{k}={v}" for k, v in dm.items()]
        lines.append(f"\nBehavioral profile: {', '.join(traits)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Discovery loop phases
# ---------------------------------------------------------------------------

def generate_baseline_dialogues(
    specs: list[CharacterSpec],
    model: str,
    scenarios: list[dict] | None = None,
) -> dict[str, dict[str, str]]:
    """Generate ground-truth dialogues from full NPC data.

    Returns {char_id: {scenario_id: dialogue_text}}.
    """
    scenarios = scenarios or TEST_SCENARIOS
    baselines: dict[str, dict[str, str]] = {}

    for spec in specs:
        baselines[spec.char_id] = {}
        full_context = _npc_full_context(spec)

        for scenario in scenarios:
            prompt = scenario["prompt"].format(name=spec.name)
            prompt += f"\n\nContext: {scenario['context']}"
            prompt += f"\nTrust level: {scenario['trust']}"

            system = f"{DIALOGUE_SYSTEM}\n\nCHARACTER DATA:\n{full_context}"

            print(f"  baseline {spec.char_id}/{scenario['id']}...", end=" ", flush=True)
            try:
                text, _, _ = call_model(system, prompt, model=model, max_tokens=2048)
                baselines[spec.char_id][scenario["id"]] = text
                print("done")
            except RuntimeError as e:
                print(f"FAILED: {e}")
                baselines[spec.char_id][scenario["id"]] = ""

            time.sleep(5)

    return baselines


def propose_dossier_format(
    specs: list[CharacterSpec],
    model: str,
    previous_format: str | None = None,
    previous_metrics: dict | None = None,
) -> str:
    """Ask the model to propose (or improve) a personality dossier format."""
    # Show the 2 most complex NPCs as examples of what needs distilling
    seeds = specs[-2:]
    seed_text = "\n\n---\n\n".join(_npc_full_context(s) for s in seeds)

    if previous_format and previous_metrics:
        prompt = f"""Here is your previous dossier format:

{previous_format}

Results from last round:
Mean voice fidelity: {previous_metrics.get('mean_fidelity', 0):.0%}
Mean compression: {previous_metrics.get('mean_compression', 0):.1f}x

Per-character results:
{previous_metrics.get('per_char_summary', '')}

Failures to address:
{previous_metrics.get('failure_notes', 'None noted.')}

Improve the dossier format:
- Fix specific voice fidelity failures noted above
- Add constraints or rules where the character went off-voice
- Remove information that didn't help (dead weight)
- Consider adding NEVER-would-say constraints if missing

Output ONLY the improved format specification between <dossier_format> tags."""
    else:
        prompt = f"""Design a maximally dense personality dossier format for NPC characters.

Here are two complex NPCs whose full data you need to be able to distill:

{seed_text}

Design the dossier format — what sections, what information, what level of detail.
The format must be dense enough that ~300-500 tokens captures everything an LLM needs
to generate in-character dialogue indistinguishable from the original.

Output ONLY the format specification between <dossier_format> tags.
Do NOT distill these characters yet — just design the format."""

    print("  Proposing dossier format...", end=" ", flush=True)
    response, _, _ = call_model(PROPOSE_SYSTEM, prompt, model=model)
    fmt = _extract_tagged(response, "dossier_format") or response
    print(f"done ({estimate_tokens(fmt)} tokens)")
    return fmt


def distill_character(
    spec: CharacterSpec,
    dossier_format: str,
    model: str,
) -> str:
    """Distill a full NPC into the proposed dossier format."""
    prompt = f"""Using this dossier format:

<dossier_format>
{dossier_format}
</dossier_format>

Distill this character's full data into a personality dossier:

{spec.json_text}

Be specific and behavioral. Extract voice RULES, not just descriptions.
Identify what makes this character distinctive from every other NPC.
Include 2-3 KEY dialogue lines (the most voice-distinctive ones, not all of them).
Include NEVER-would-say constraints.

Output ONLY the dossier between <dossier name="{spec.char_id}"> tags."""

    print(f"  Distilling {spec.char_id}...", end=" ", flush=True)
    response, _, _ = call_model(DISTILL_SYSTEM, prompt, model=model, max_tokens=4096)
    dossier = _extract_tagged(response, "dossier", spec.char_id) or response
    print(f"done ({estimate_tokens(dossier)} tokens)")
    return dossier


def generate_dossier_dialogues(
    char_id: str,
    name: str,
    dossier: str,
    model: str,
    scenarios: list[dict] | None = None,
) -> dict[str, str]:
    """Generate dialogues from ONLY the dossier (no full JSON)."""
    scenarios = scenarios or TEST_SCENARIOS
    results: dict[str, str] = {}

    for scenario in scenarios:
        prompt = scenario["prompt"].format(name=name)
        prompt += f"\n\nContext: {scenario['context']}"
        prompt += f"\nTrust level: {scenario['trust']}"

        system = f"{DIALOGUE_SYSTEM}\n\nCHARACTER DOSSIER (this is ALL you have):\n{dossier}"

        print(f"  dossier {char_id}/{scenario['id']}...", end=" ", flush=True)
        try:
            text, _, _ = call_model(system, prompt, model=model, max_tokens=2048)
            results[scenario["id"]] = text
            print("done")
        except RuntimeError as e:
            print(f"FAILED: {e}")
            results[scenario["id"]] = ""

        time.sleep(5)

    return results


def judge_fidelity(
    char_id: str,
    name: str,
    baseline_dialogue: str,
    dossier_dialogue: str,
    scenario: dict,
    model: str,
) -> dict:
    """Score voice fidelity between baseline and dossier dialogues."""
    prompt = f"""Character: {name} ({char_id})
Scenario: {scenario['id']} (trust={scenario['trust']})

SAMPLE A (from full character data — ground truth):
{baseline_dialogue}

SAMPLE B (from condensed dossier):
{dossier_dialogue}

Score how well Sample B matches Sample A's voice and character."""

    try:
        text, _, _ = call_model(JUDGE_SYSTEM, prompt, model=model, max_tokens=1024)
        # Extract JSON
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        print(f"  [judge error: {e}]", end=" ")

    return {"overall": 0.0, "notes": "judge failed"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_tagged(text: str, tag: str, name: str | None = None) -> str | None:
    """Extract content from <tag>...</tag> or <tag name="X">...</tag>."""
    if name:
        pattern = rf'<{tag}\s+name="{re.escape(name)}">(.*?)</{tag}>'
    else:
        pattern = rf'<{tag}>(.*?)</{tag}>'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CharMetrics:
    """Metrics for one character in one round."""
    char_id: str
    dossier_tokens: int = 0
    baseline_tokens: int = 0
    fidelity_scores: list[dict] = field(default_factory=list)
    dossier_text: str = ""
    failure_notes: list[str] = field(default_factory=list)

    @property
    def compression_ratio(self) -> float:
        if self.dossier_tokens == 0:
            return 0.0
        return self.baseline_tokens / self.dossier_tokens

    @property
    def mean_fidelity(self) -> float:
        scores = [s.get("overall", 0.0) for s in self.fidelity_scores]
        return sum(scores) / len(scores) if scores else 0.0


@dataclass
class RoundResult:
    """Results for one discovery round."""
    round_number: int
    dossier_format: str = ""
    format_tokens: int = 0
    chars: list[CharMetrics] = field(default_factory=list)

    @property
    def mean_compression(self) -> float:
        ratios = [c.compression_ratio for c in self.chars if c.compression_ratio > 0]
        return sum(ratios) / len(ratios) if ratios else 0.0

    @property
    def mean_fidelity(self) -> float:
        scores = [c.mean_fidelity for c in self.chars]
        return sum(scores) / len(scores) if scores else 0.0

    def summary(self) -> str:
        lines = [
            f"=== Round {self.round_number} ===",
            f"Dossier format: {self.format_tokens} tokens",
            f"Mean compression: {self.mean_compression:.1f}x",
            f"Mean voice fidelity: {self.mean_fidelity:.0%}",
        ]
        for c in self.chars:
            lines.append(
                f"  {c.char_id}: {c.compression_ratio:.1f}x compression, "
                f"{c.mean_fidelity:.0%} voice fidelity, "
                f"{c.dossier_tokens} tokens"
            )
            for note in c.failure_notes:
                lines.append(f"    ! {note}")
        return "\n".join(lines)

    def per_char_summary(self) -> str:
        lines = []
        for c in self.chars:
            lines.append(
                f"{c.char_id}: {c.compression_ratio:.1f}x, {c.mean_fidelity:.0%}"
            )
            for note in c.failure_notes:
                lines.append(f"  ! {note}")
        return "\n".join(lines)

    def failure_notes_summary(self) -> str:
        notes = []
        for c in self.chars:
            for score in c.fidelity_scores:
                note = score.get("notes", "")
                if note and note != "judge failed":
                    notes.append(f"{c.char_id}: {note}")
        return "\n".join(notes) if notes else "None noted."


@dataclass
class DiscoveryRun:
    """Complete discovery run."""
    model: str
    rounds: list[RoundResult] = field(default_factory=list)
    baselines: dict[str, dict[str, str]] = field(default_factory=dict)

    def best_round(self) -> RoundResult | None:
        if not self.rounds:
            return None
        viable = [r for r in self.rounds if r.mean_fidelity >= 0.75]
        if not viable:
            return max(self.rounds, key=lambda r: r.mean_fidelity)
        return max(viable, key=lambda r: r.mean_compression)

    def save(self, path: Path) -> None:
        data = {
            "model": self.model,
            "rounds": [
                {
                    "round": r.round_number,
                    "format_tokens": r.format_tokens,
                    "mean_compression": r.mean_compression,
                    "mean_fidelity": r.mean_fidelity,
                    "dossier_format": r.dossier_format,
                    "chars": [
                        {
                            "char_id": c.char_id,
                            "dossier_tokens": c.dossier_tokens,
                            "baseline_tokens": c.baseline_tokens,
                            "compression_ratio": c.compression_ratio,
                            "mean_fidelity": c.mean_fidelity,
                            "dossier_text": c.dossier_text,
                            "fidelity_scores": c.fidelity_scores,
                        }
                        for c in r.chars
                    ],
                }
                for r in self.rounds
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Main discovery loop
# ---------------------------------------------------------------------------

def run_discovery(
    num_rounds: int = 3,
    model: str = DEFAULT_MODEL,
    judge_model: str = JUDGE_MODEL,
    corpus_dir: str | Path | None = None,
    char_map: dict[str, str] | None = None,
    char_ids: list[str] | None = None,
    checkpoint_dir: Path | None = None,
) -> DiscoveryRun:
    """Run the personality dossier discovery loop.

    Args:
        corpus_dir: Path to directory containing character JSON files.
        char_map: Mapping of char_id → JSON filename.
        char_ids: Specific char_ids to load (defaults to all in char_map).
    """
    if corpus_dir is None:
        raise ValueError("corpus_dir is required — pass the path to your character JSON directory")
    specs = load_corpus(corpus_dir=corpus_dir, char_map=char_map, char_ids=char_ids)
    run = DiscoveryRun(model=model)

    ckpt_dir = checkpoint_dir or (RESULTS_DIR / "checkpoint")
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    def ckpt_path(name: str) -> Path:
        return ckpt_dir / f"{name}.json"

    def ckpt_load(name: str):
        p = ckpt_path(name)
        return json.loads(p.read_text()) if p.exists() else None

    def ckpt_save(name: str, data) -> None:
        ckpt_path(name).write_text(json.dumps(data, ensure_ascii=False))

    print("Effigy Dossier Discovery Loop")
    print(f"Model: {model}")
    print(f"Judge: {judge_model}")
    print(corpus_summary(specs))
    print(f"Rounds: {num_rounds}")
    print(f"Scenarios: {len(TEST_SCENARIOS)}")
    print()

    # Phase 1: Baselines
    print("=== Phase 1: Baseline dialogues ===")
    ck_baselines = ckpt_load("baselines")
    if ck_baselines:
        baselines = ck_baselines
        print("  Resumed from checkpoint")
    else:
        baselines = generate_baseline_dialogues(specs, model)
        ckpt_save("baselines", baselines)
    run.baselines = baselines

    baseline_tokens: dict[str, int] = {}
    for spec in specs:
        baseline_tokens[spec.char_id] = spec.token_estimate
        print(f"  {spec.char_id}: {spec.token_estimate} baseline tokens")
    print()

    # Rounds
    dossier_format: str | None = None
    prev_round: RoundResult | None = None

    for round_num in range(num_rounds):
        rn = round_num + 1
        print(f"=== Round {rn}/{num_rounds} ===")
        round_result = RoundResult(round_number=rn)

        # Phase 2: Propose/improve dossier format
        ck_format = ckpt_load(f"round{rn}_format")
        if ck_format:
            dossier_format = ck_format["format"]
            print("  Format: resumed from checkpoint")
        else:
            if rn > 1:
                time.sleep(15)

            prev_metrics = None
            if prev_round:
                prev_metrics = {
                    "mean_compression": prev_round.mean_compression,
                    "mean_fidelity": prev_round.mean_fidelity,
                    "per_char_summary": prev_round.per_char_summary(),
                    "failure_notes": prev_round.failure_notes_summary(),
                }

            dossier_format = propose_dossier_format(
                specs, model,
                previous_format=dossier_format,
                previous_metrics=prev_metrics,
            )
            ckpt_save(f"round{rn}_format", {"format": dossier_format})

        round_result.dossier_format = dossier_format
        round_result.format_tokens = estimate_tokens(dossier_format)

        # Phase 3-5: Distill, generate, judge for each character
        for i, spec in enumerate(specs):
            # Check checkpoint
            ck_char = ckpt_load(f"round{rn}_{spec.char_id}")
            if ck_char:
                cm = CharMetrics(
                    char_id=spec.char_id,
                    dossier_tokens=ck_char["dossier_tokens"],
                    baseline_tokens=baseline_tokens.get(spec.char_id, 0),
                    fidelity_scores=ck_char.get("fidelity_scores", []),
                    dossier_text=ck_char.get("dossier_text", ""),
                    failure_notes=ck_char.get("failure_notes", []),
                )
                print(f"  {spec.char_id}: {cm.compression_ratio:.1f}x, "
                      f"{cm.mean_fidelity:.0%} (resumed)")
                round_result.chars.append(cm)
                continue

            if i > 0:
                time.sleep(10)

            cm = CharMetrics(
                char_id=spec.char_id,
                baseline_tokens=baseline_tokens.get(spec.char_id, 0),
            )

            # Phase 3: Distill
            try:
                dossier = distill_character(spec, dossier_format, model)
            except RuntimeError as e:
                print(f"  {spec.char_id}: distill FAILED: {e}")
                round_result.chars.append(cm)
                continue

            cm.dossier_text = dossier
            cm.dossier_tokens = estimate_tokens(dossier)

            # Phase 4: Generate dialogues from dossier
            dossier_dialogues = generate_dossier_dialogues(
                spec.char_id, spec.name, dossier, model,
            )

            # Phase 5: Judge each scenario
            print(f"  Judging {spec.char_id}...", end=" ", flush=True)
            for scenario in TEST_SCENARIOS:
                sid = scenario["id"]
                baseline_d = baselines.get(spec.char_id, {}).get(sid, "")
                dossier_d = dossier_dialogues.get(sid, "")

                if not baseline_d or not dossier_d:
                    cm.fidelity_scores.append({"overall": 0.0, "notes": "missing dialogue"})
                    continue

                time.sleep(5)
                score = judge_fidelity(
                    spec.char_id, spec.name,
                    baseline_d, dossier_d, scenario, judge_model,
                )
                cm.fidelity_scores.append(score)

                # Track specific failures
                if score.get("overall", 0) < 0.7:
                    note = score.get("notes", "low fidelity")
                    cm.failure_notes.append(f"{sid}: {note}")

            print(f"{cm.mean_fidelity:.0%}")

            # Save checkpoint
            ckpt_save(f"round{rn}_{spec.char_id}", {
                "dossier_tokens": cm.dossier_tokens,
                "fidelity_scores": cm.fidelity_scores,
                "dossier_text": cm.dossier_text,
                "failure_notes": cm.failure_notes,
            })

            round_result.chars.append(cm)

        print()
        print(round_result.summary())
        print()

        run.rounds.append(round_result)
        prev_round = round_result

        # Convergence check
        if len(run.rounds) >= 2:
            prev = run.rounds[-2]
            curr = run.rounds[-1]
            fid_delta = abs(curr.mean_fidelity - prev.mean_fidelity)
            print(f"  Fidelity delta: {fid_delta:.3f}")
            if fid_delta < 0.03 and curr.mean_fidelity >= 0.85:
                print(f"  CONVERGED at round {rn}")
                break

    # Clean up checkpoints on success
    for f in ckpt_dir.glob("*.json"):
        f.unlink()

    return run


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Effigy dossier discovery")
    parser.add_argument("--corpus-dir", required=True, help="Path to character JSON directory")
    parser.add_argument("--char-map", help="JSON file mapping char_id → filename")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--judge-model", default=JUDGE_MODEL)
    parser.add_argument("--chars", nargs="*", help="Specific char_ids")
    parser.add_argument("--output", default=str(RESULTS_DIR))
    args = parser.parse_args()

    char_map = None
    if args.char_map:
        char_map = json.loads(Path(args.char_map).read_text())

    run = run_discovery(
        num_rounds=args.rounds,
        model=args.model,
        judge_model=args.judge_model,
        corpus_dir=args.corpus_dir,
        char_map=char_map,
        char_ids=args.chars,
    )

    output_dir = Path(args.output)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    run.save(output_dir / f"dossier-run-{timestamp}.json")

    best = run.best_round()
    if best:
        print(f"\n{'=' * 60}")
        print(f"BEST ROUND: {best.round_number}")
        print(f"Mean compression: {best.mean_compression:.1f}x")
        print(f"Mean voice fidelity: {best.mean_fidelity:.0%}")

        # Save best dossier format
        fmt_path = output_dir / f"dossier-format-{timestamp}.txt"
        fmt_path.write_text(best.dossier_format)
        print(f"Dossier format saved to {fmt_path}")

        # Save individual dossiers
        for cm in best.chars:
            if cm.dossier_text:
                d_path = output_dir / f"dossier-{cm.char_id}-{timestamp}.txt"
                d_path.write_text(cm.dossier_text)
                print(f"  {cm.char_id} dossier saved to {d_path}")

    trend = [(r.round_number, r.mean_compression, r.mean_fidelity)
             for r in run.rounds]
    if len(trend) > 1:
        print("\nTrend:")
        for rn, comp, fid in trend:
            bar = "█" * int(fid * 20)
            print(f"  Round {rn}: {comp:.1f}x compression  {fid:.0%} fidelity  {bar}")


if __name__ == "__main__":
    main()
