package character

import (
	"encoding/json"
	"regexp"
	"strings"
)

var secretRequires = regexp.MustCompile(`REQUIRES player knows\s+(.+?)(?:\s*[—.-]|$)`)

// Expand returns the character JSON as a map, matching effigy's expand().
//
// Fields divide into two kinds and the difference is load-bearing for anyone
// diffing this against the Python: the first group is always present, defaulting
// to empty, and the second is omitted entirely when empty. Which field is in
// which group is the reference's choice, not a convention, so it is reproduced
// rather than tidied.
//
// Note what does not appear: ARC phases, GOALS, PROPS, WRONG and POSTPROC are
// parsed into the AST and expand emits none of them. A consumer that needs those
// reads the AST, or reads the blocks through notation directly.
func (c *Character) Expand() map[string]any {
	out := map[string]any{}

	out["char_id"] = c.CharID
	out["name"] = c.Name
	out["presence_note"] = c.PresenceNote
	out["role"] = c.Role
	out["archetype"] = c.Archetype

	out["voice_kernel"] = ""
	if c.Voice != nil {
		out["voice_kernel"] = c.Voice.Kernel
	}

	mes := []string{}
	for _, e := range c.MesExamples {
		mes = append(mes, e.Text)
	}
	out["mes_examples"] = mes
	out["narrative_role"] = string(c.NarrativeRole)
	out["uncertainty_voice"] = emptyIfNil(c.UncertaintyVoice)
	out["trope_tags"] = emptyIfNil(c.TropeTags)

	eras := []map[string]any{}
	for _, e := range c.EraStates {
		era := map[string]any{"era_id": e.EraID, "status": e.Status}
		if e.Age != nil {
			era["age"] = *e.Age
		}
		if e.Occupation != "" {
			era["occupation"] = e.Occupation
		}
		if e.Disposition != "" {
			era["disposition"] = e.Disposition
		}
		if e.Notes != "" {
			era["notes"] = e.Notes
		}
		eras = append(eras, era)
	}
	out["era_states"] = eras

	secrets := []map[string]any{}
	for _, s := range c.Secrets {
		sec := map[string]any{"layer": s.Layer, "secret": s.Secret}
		if s.RevealCondition != "" {
			sec["reveal_condition"] = s.RevealCondition
			if m := secretRequires.FindStringSubmatch(s.RevealCondition); m != nil {
				var facts []string
				for _, f := range strings.Split(m[1], " or ") {
					facts = append(facts, strings.TrimSpace(f))
				}
				sec["requires_fact"] = facts
			}
		}
		if s.RelatedEra != "" {
			sec["related_era"] = s.RelatedEra
		}
		secrets = append(secrets, sec)
	}
	out["secrets"] = secrets

	rels := []map[string]any{}
	for _, r := range c.Relationships {
		rel := map[string]any{"target": r.Target, "type": r.Type, "intensity": r.Intensity}
		if r.Notes != "" {
			rel["notes"] = r.Notes
		}
		rels = append(rels, rel)
	}
	out["relationships"] = rels

	sched := c.Schedule
	if sched == nil {
		sched = &Schedule{}
	}
	out["schedule"] = map[string]any{
		"morning": deref(sched.Morning), "afternoon": deref(sched.Afternoon),
		"evening": deref(sched.Evening), "night": deref(sched.Night),
	}

	out["arrival_lines"] = emptyIfNil(c.ArrivalLines)
	out["departure_lines"] = emptyIfNil(c.DepartureLines)

	if c.Drivermap != nil {
		if len(c.Drivermap.Profile) > 0 {
			out["drivermap_profile"] = c.Drivermap.Profile
		}
		if len(c.Drivermap.SituationFeatures) > 0 {
			out["npc_situation_features"] = c.Drivermap.SituationFeatures
		}
	}

	if c.Voice != nil && c.Voice.Peak != "" {
		out["peak_voice"] = c.Voice.Peak
	}

	if len(c.Traits) > 0 {
		out["traits"] = c.Traits
	}
	if len(c.NeverWouldSay) > 0 {
		// When any rule carries a gate, every rule is emitted as a
		// {text, when} object so a consumer has one code path. With no gates
		// anywhere they stay plain strings, which is what readers older than
		// @when expect.
		gated := false
		for _, n := range c.NeverWouldSay {
			if n.When != "" {
				gated = true
				break
			}
		}
		if gated {
			rules := []map[string]any{}
			for _, n := range c.NeverWouldSay {
				rules = append(rules, map[string]any{"text": n.Text, "when": n.When})
			}
			out["never_would_say"] = rules
		} else {
			texts := []string{}
			for _, n := range c.NeverWouldSay {
				texts = append(texts, n.Text)
			}
			out["never_would_say"] = texts
		}
	}
	if len(c.Quirks) > 0 {
		out["quirks"] = c.Quirks
	}
	if c.Theme != "" {
		out["theme"] = c.Theme
	}
	if len(c.GoalBehaviors) > 0 {
		out["goal_behaviors"] = c.GoalBehaviors
	}

	if len(c.Tests) > 0 {
		tests := []map[string]any{}
		for _, t := range c.Tests {
			td := map[string]any{"name": t.Name, "question": t.Question}
			if t.Dimension != "" {
				td["dimension"] = t.Dimension
			}
			if len(t.FailExamples) > 0 {
				td["fail_examples"] = t.FailExamples
			}
			if len(t.PassExamples) > 0 {
				td["pass_examples"] = t.PassExamples
			}
			if t.Why != "" {
				td["why"] = t.Why
			}
			if t.Beat != "" {
				td["beat"] = t.Beat
			}
			tests = append(tests, td)
		}
		out["tests"] = tests
	}

	return out
}

// ExpandJSON is Expand marshalled. Go sorts object keys and Python emits them
// in insertion order, so the bytes differ from `effigy expand` while the values
// do not — compare these as JSON, never as text.
func (c *Character) ExpandJSON(indent bool) ([]byte, error) {
	if indent {
		return json.MarshalIndent(c.Expand(), "", "  ")
	}
	return json.Marshal(c.Expand())
}

func emptyIfNil(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

// deref keeps a nil slot as JSON null rather than "", which is the difference
// between a schedule the card left out and one it filled with nothing.
func deref(s *string) any {
	if s == nil {
		return nil
	}
	return *s
}
