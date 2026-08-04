package character

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"testing"
)

// TestASTParityWithPython covers what TestParityWithPython cannot.
//
// expand() emits a subset of the AST: ARC phases, GOALS, PROPS, WRONG examples
// and POSTPROC rules are parsed and then never reach the JSON. Comparing
// expanded output therefore says nothing about five of the block parsers, and a
// port could get all of them wrong while the expand test stayed green. This one
// dumps the AST itself from both sides and diffs that, so every parser has
// something holding it to the reference.
func TestASTParityWithPython(t *testing.T) {
	repo, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("no python3; the reference is unavailable")
	}
	if _, err := os.Stat(filepath.Join(repo, "effigy", "parser.py")); err != nil {
		t.Skipf("no effigy package at %s; the reference is unavailable", repo)
	}

	cards := findCards(t, repo)
	if len(cards) == 0 {
		t.Fatal("no cards to compare")
	}

	for _, path := range cards {
		t.Run(filepath.Base(filepath.Dir(path))+"/"+filepath.Base(path), func(t *testing.T) {
			src, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			c, err := Parse(src)
			if err != nil {
				t.Fatalf("Parse: %v", err)
			}

			gotJSON, err := json.Marshal(astDump(c))
			if err != nil {
				t.Fatal(err)
			}
			wantJSON := pythonAST(t, repo, path)

			var got, want any
			if err := json.Unmarshal(gotJSON, &got); err != nil {
				t.Fatalf("unmarshal ours: %v", err)
			}
			if err := json.Unmarshal(wantJSON, &want); err != nil {
				t.Fatalf("unmarshal the reference's: %v\n%s", err, wantJSON)
			}
			if !reflect.DeepEqual(got, want) {
				t.Errorf("AST differs from the reference%s", diffKeys(got, want))
			}
		})
	}
}

// astDump is the Go side of the comparison, shaped to match pythonAST below.
// Written out by hand rather than reflected so that adding a field to the AST
// without adding it here is visible in review.
func astDump(c *Character) map[string]any {
	arcs := []map[string]any{}
	for _, p := range c.ArcPhases {
		arcs = append(arcs, map[string]any{
			"name": p.Name, "condition_str": p.ConditionStr, "voice": p.Voice,
			"deflection": p.Deflection, "beats": p.Beats, "conditions": p.Conditions,
		})
	}
	goals := []map[string]any{}
	for _, g := range c.Goals {
		goals = append(goals, map[string]any{
			"name": g.Name, "weight": g.Weight, "grows_with": g.GrowsWith,
		})
	}
	wrongs := []map[string]any{}
	for _, w := range c.WrongExamples {
		wrongs = append(wrongs, map[string]any{
			"context": w.Context, "wrong": w.Wrong, "right": w.Right,
			"why": w.Why, "when": w.When, "beat": w.Beat,
		})
	}
	rules := []map[string]any{}
	for _, r := range c.PostProcessors {
		rules = append(rules, map[string]any{
			"action": r.Action, "pattern": r.Pattern, "why": r.Why, "rule_id": r.RuleID,
		})
	}
	mes := []map[string]any{}
	for _, e := range c.MesExamples {
		mes = append(mes, map[string]any{
			"text": e.Text, "tier": e.Tier, "when": e.When, "beat": e.Beat,
		})
	}
	nevers := []map[string]any{}
	for _, n := range c.NeverWouldSay {
		nevers = append(nevers, map[string]any{"text": n.Text, "when": n.When})
	}
	tests := []map[string]any{}
	for _, tc := range c.Tests {
		tests = append(tests, map[string]any{
			"name": tc.Name, "question": tc.Question, "why": tc.Why,
			"dimension": tc.Dimension, "when": tc.When, "beat": tc.Beat,
			"fail_examples": emptyIfNil(tc.FailExamples),
			"pass_examples": emptyIfNil(tc.PassExamples),
		})
	}
	secrets := []map[string]any{}
	for _, s := range c.Secrets {
		secrets = append(secrets, map[string]any{
			"layer": s.Layer, "secret": s.Secret,
			"reveal_condition": s.RevealCondition, "related_era": s.RelatedEra,
		})
	}
	rels := []map[string]any{}
	for _, r := range c.Relationships {
		rels = append(rels, map[string]any{
			"target": r.Target, "rel_type": r.Type,
			"intensity": r.Intensity, "notes": r.Notes,
		})
	}
	eras := []map[string]any{}
	for _, e := range c.EraStates {
		era := map[string]any{
			"era_id": e.EraID, "status": e.Status, "occupation": e.Occupation,
			"disposition": e.Disposition, "notes": e.Notes, "age": nil,
		}
		if e.Age != nil {
			era["age"] = *e.Age
		}
		eras = append(eras, era)
	}

	var sched any
	if c.Schedule != nil {
		sched = map[string]any{
			"morning": deref(c.Schedule.Morning), "afternoon": deref(c.Schedule.Afternoon),
			"evening": deref(c.Schedule.Evening), "night": deref(c.Schedule.Night),
		}
	}
	var dm any
	if c.Drivermap != nil {
		dm = map[string]any{
			"profile":            c.Drivermap.Profile,
			"situation_features": emptyIfNil(c.Drivermap.SituationFeatures),
		}
	}
	var voice any
	if c.Voice != nil {
		voice = map[string]any{
			"kernel": c.Voice.Kernel, "peak": c.Voice.Peak, "peak_when": c.Voice.PeakWhen,
		}
	}

	return map[string]any{
		"char_id": c.CharID, "name": c.Name, "role": c.Role,
		"archetype": c.Archetype, "narrative_role": string(c.NarrativeRole),
		"presence_note": c.PresenceNote, "theme": c.Theme,
		"trope_tags": emptyIfNil(c.TropeTags),
		"extra":      c.ExtraHeaders,
		"voice":      voice,
		"traits":     emptyIfNil(c.Traits), "quirks": emptyIfNil(c.Quirks),
		"props": emptyIfNil(c.Props), "uncertainty_voice": emptyIfNil(c.UncertaintyVoice),
		"arrival_lines":   emptyIfNil(c.ArrivalLines),
		"departure_lines": emptyIfNil(c.DepartureLines),
		"never_would_say": nevers, "mes_examples": mes, "arc_phases": arcs,
		"goals": goals, "goal_behaviors": mapOrEmpty(c.GoalBehaviors),
		"secrets": secrets, "relationships": rels, "era_states": eras,
		"schedule": sched, "drivermap": dm, "wrong_examples": wrongs,
		"tests": tests, "post_processors": rules,
	}
}

func mapOrEmpty(m map[string]string) map[string]string {
	if m == nil {
		return map[string]string{}
	}
	return m
}

// pythonAST dumps the reference AST in the same shape as astDump.
func pythonAST(t *testing.T, repo, card string) []byte {
	t.Helper()
	const script = `
import json, sys
sys.path.insert(0, sys.argv[1])
from effigy import parse

with open(sys.argv[2], encoding="utf-8") as fh:
    ast = parse(fh.read())

def sched(s):
    if s is None:
        return None
    return {"morning": s.morning, "afternoon": s.afternoon,
            "evening": s.evening, "night": s.night}

def dm(d):
    if d is None:
        return None
    return {"profile": dict(d.profile), "situation_features": list(d.situation_features)}

def voice(v):
    if v is None:
        return None
    return {"kernel": v.kernel, "peak": v.peak, "peak_when": v.peak_when}

out = {
  "char_id": ast.char_id, "name": ast.name, "role": ast.role,
  "archetype": ast.archetype, "narrative_role": ast.narrative_role.value,
  "presence_note": ast.presence_note, "theme": ast.theme,
  "trope_tags": list(ast.trope_tags),
  "extra": {k: list(v) for k, v in ast.extra.items()},
  "voice": voice(ast.voice),
  "traits": list(ast.traits), "quirks": list(ast.quirks),
  "props": list(ast.props), "uncertainty_voice": list(ast.uncertainty_voice),
  "arrival_lines": list(ast.arrival_lines),
  "departure_lines": list(ast.departure_lines),
  "never_would_say": [{"text": n.text, "when": n.when} for n in ast.never_would_say],
  "mes_examples": [{"text": e.text, "tier": e.tier, "when": e.when, "beat": e.beat}
                   for e in ast.mes_examples],
  "arc_phases": [{"name": p.name, "condition_str": p.condition_str, "voice": p.voice,
                  "deflection": p.deflection, "beats": p.beats,
                  "conditions": p.conditions} for p in ast.arc_phases],
  "goals": [{"name": g.name, "weight": g.weight, "grows_with": g.grows_with}
            for g in ast.goals],
  "goal_behaviors": dict(ast.goal_behaviors),
  "secrets": [{"layer": s.layer, "secret": s.secret,
               "reveal_condition": s.reveal_condition,
               "related_era": s.related_era} for s in ast.secrets],
  "relationships": [{"target": r.target, "rel_type": r.rel_type,
                     "intensity": r.intensity, "notes": r.notes}
                    for r in ast.relationships],
  "era_states": [{"era_id": e.era_id, "status": e.status, "age": e.age,
                  "occupation": e.occupation, "disposition": e.disposition,
                  "notes": e.notes} for e in ast.era_states],
  "schedule": sched(ast.schedule),
  "drivermap": dm(ast.drivermap),
  "wrong_examples": [{"context": w.context, "wrong": w.wrong, "right": w.right,
                      "why": w.why, "when": w.when, "beat": w.beat}
                     for w in ast.wrong_examples],
  "tests": [{"name": t.name, "question": t.question, "why": t.why,
             "dimension": t.dimension, "when": t.when, "beat": t.beat,
             "fail_examples": list(t.fail_examples),
             "pass_examples": list(t.pass_examples)} for t in ast.tests],
  "post_processors": [{"action": r.action, "pattern": r.pattern, "why": r.why,
                       "rule_id": r.rule_id} for r in ast.post_processors],
}
print(json.dumps(out, ensure_ascii=False))
`
	cmd := exec.Command("python3", "-c", script, repo, card)
	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("reference failed on %s: %v\n%s", card, err, ee.Stderr)
		}
		t.Fatalf("reference failed on %s: %v", card, err)
	}
	return out
}
