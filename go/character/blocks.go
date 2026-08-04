package character

import (
	"regexp"
	"strconv"
	"strings"

	"github.com/justinstimatze/effigy/go/notation"
)

// keptLines drops blank and commented lines and trims what is left.
func keptLines(body string) []string {
	var out []string
	for _, line := range strings.Split(body, "\n") {
		if s := strings.TrimSpace(line); s != "" && !strings.HasPrefix(s, "#") {
			out = append(out, s)
		}
	}
	return out
}

func parseVoice(body string) *Voice {
	kv := notation.KV(body)
	return &Voice{Kernel: kv["kernel"], Peak: kv["peak"], PeakWhen: kv["peak_when"]}
}

// parseTraits flattens the block to one string and splits on commas, so a trait
// list can wrap across as many lines as it needs to.
func parseTraits(body string) []string {
	var out []string
	for _, t := range strings.Split(strings.Join(keptLines(body), " "), ",") {
		if t = strings.TrimSpace(t); t != "" {
			out = append(out, t)
		}
	}
	return out
}

// parseLines is QUIRKS, PROPS, ARRIVE and DEPART: one entry per item, its lines
// joined.
func parseLines(body string) []string {
	var out []string
	for _, item := range notation.SplitItems(body) {
		if text := strings.Join(keptLinesNoComment(item), " "); text != "" {
			out = append(out, text)
		}
	}
	return out
}

// keptLinesNoComment differs from keptLines by keeping # lines, which
// SplitItems has already removed at this point.
func keptLinesNoComment(item string) []string {
	var out []string
	for _, line := range strings.Split(strings.TrimSpace(item), "\n") {
		if s := strings.TrimSpace(line); s != "" {
			out = append(out, s)
		}
	}
	return out
}

func parseNever(body string) []NeverRule {
	var out []NeverRule
	for _, item := range notation.SplitItems(body) {
		when := ""
		var text []string
		for _, raw := range strings.Split(strings.TrimSpace(item), "\n") {
			line := strings.TrimSpace(raw)
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			if rest, ok := notation.Directive(line, "@when"); ok {
				when = rest
				continue
			}
			text = append(text, line)
		}
		if t := strings.Join(text, " "); t != "" {
			out = append(out, NeverRule{Text: t, When: when})
		}
	}
	return out
}

func parseExamples(body string) []Example {
	var out []Example
	for _, e := range notation.MES(body) {
		out = append(out, Example{Text: e.Text, Tier: e.Tier, When: e.When, Beat: e.Beat})
	}
	return out
}

// parseUncertainty is MES with the annotations dropped: UNC entries are not
// trust-gated.
func parseUncertainty(body string) []string {
	var out []string
	for _, e := range notation.MES(body) {
		out = append(out, e.Text)
	}
	return out
}

var (
	arcPhaseDecl = regexp.MustCompile(`^(\w+)\s*[→>-]+\s*(.*)$`)
	beatSep      = regexp.MustCompile(`\s*(?:→|->)\s*`)
)

// parseArc reads the one block with two levels. A phase opens with
// "name → condition"; the lines under it are its fields, and a field's value
// continues onto any line that does not open a new one.
func parseArc(body string) []ArcPhase {
	var out []ArcPhase
	var cur ArcPhase
	open := false
	last := "voice"

	flush := func() {
		if open {
			out = append(out, cur)
		}
	}

	for _, raw := range strings.Split(body, "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}

		if m := arcPhaseDecl.FindStringSubmatch(line); m != nil {
			flush()
			cond := strings.TrimSpace(m[2])
			cur = ArcPhase{
				Name:         m[1],
				Conditions:   parseConditions(cond),
				ConditionStr: normalizeCondition(cond),
			}
			open, last = true, "voice"
			continue
		}

		if v, ok := arcField(line, "voice:"); ok {
			cur.Voice, last = v, "voice"
			continue
		}
		if v, ok := arcField(line, "deflection:"); ok {
			cur.Deflection, last = v, "deflection"
			continue
		}
		if v, ok := arcField(line, "beats:"); ok {
			for _, b := range beatSep.Split(v, -1) {
				if b = strings.TrimSpace(b); b != "" {
					cur.Beats = append(cur.Beats, b)
				}
			}
			last = "beats"
			continue
		}

		if !open {
			continue
		}
		switch {
		case last == "deflection" && cur.Deflection != "":
			cur.Deflection += " " + line
		case last == "voice" && cur.Voice != "":
			cur.Voice += " " + line
		case last == "beats":
			// beats is single-line by design; a stray follow-on is an authoring
			// error and is dropped rather than being read as a condition.
		case !strings.Contains(line, ":"):
			for k, v := range parseConditions(line) {
				cur.Conditions[k] = v
			}
		}
	}
	flush()
	return out
}

// arcField matches "name: value" and returns the value with surrounding quotes
// removed. A field with nothing after the colon does not match, which lets it
// fall through to the continuation rules the way the reference does.
func arcField(line, name string) (string, bool) {
	if !strings.HasPrefix(line, name) {
		return "", false
	}
	rest := strings.TrimLeft(line[len(name):], " \t")
	if rest == "" {
		return "", false
	}
	if name == "beats:" {
		return strings.TrimSpace(rest), true
	}
	return strings.Trim(strings.TrimSpace(rest), `"'`), true
}

var goalGrows = regexp.MustCompile(`^grows\s+with\s+(.+)`)

// parseGoals reads "name 0.8 → grows with trust". The weight is whichever
// field after the name parses as a number, last one winning.
func parseGoals(body string) []Goal {
	var out []Goal
	for _, line := range keptLines(body) {
		parts := strings.Fields(line)
		if len(parts) == 0 {
			continue
		}
		g := Goal{Name: parts[0], Weight: 0.5}
		for _, p := range parts[1:] {
			if w, err := strconv.ParseFloat(p, 64); err == nil {
				g.Weight = w
			}
		}
		if i := strings.Index(line, "→"); i >= 0 {
			rest := strings.TrimSpace(line[i+len("→"):])
			if m := goalGrows.FindStringSubmatch(rest); m != nil {
				g.GrowsWith = strings.TrimSpace(m[1])
			}
		}
		out = append(out, g)
	}
	return out
}

var secretLayer = regexp.MustCompile(`^L(\d)$`)

func parseSecrets(body string) []Secret {
	var out []Secret
	for _, item := range notation.SplitItems(body) {
		kv := notation.KV(item)
		s := Secret{Layer: 1}
		if v, ok := kv["layer"]; ok {
			if n, err := strconv.Atoi(strings.TrimSpace(v)); err == nil {
				s.Layer = n
			}
		}
		s.Secret = kv["secret"]
		s.RevealCondition = kv["reveal"]
		s.RelatedEra = kv["era"]

		// Compact form: "L1: the secret". Ranging a map is unordered, and the
		// reference takes the first match it happens to see; with one L-key per
		// item, which is how they are written, the two agree.
		for key, val := range kv {
			if m := secretLayer.FindStringSubmatch(key); m != nil {
				n, _ := strconv.Atoi(m[1])
				s.Layer, s.Secret = n, val
				break
			}
		}
		if s.Secret != "" {
			out = append(out, s)
		}
	}
	return out
}

func parseRels(body string) []Relationship {
	var out []Relationship
	for _, line := range keptLines(body) {
		if k, _, ok := kvLine(line); ok && k == "target" {
			kv := notation.KV(line)
			intensity := 0.5
			if v, ok := kv["intensity"]; ok {
				if f, err := strconv.ParseFloat(v, 64); err == nil {
					intensity = f
				}
			}
			out = append(out, Relationship{
				Target: kv["target"], Type: kv["type"],
				Intensity: intensity, Notes: kv["notes"],
			})
			continue
		}

		// Compact form: "target type intensity notes...".
		parts := compactFields(line, 4)
		if len(parts) < 2 {
			continue
		}
		r := Relationship{Target: parts[0], Type: parts[1], Intensity: 0.5}
		if len(parts) >= 3 {
			if f, err := strconv.ParseFloat(parts[2], 64); err == nil {
				r.Intensity = f
			} else {
				r.Notes = parts[2]
			}
		}
		if len(parts) >= 4 {
			r.Notes = strings.Trim(parts[3], `"'`)
		}
		out = append(out, r)
	}
	return out
}

// compactFields splits on runs of whitespace at most n-1 times, so the last
// field keeps its own spacing. Python's str.split(None, maxsplit) in Go terms.
func compactFields(s string, n int) []string {
	var out []string
	rest := strings.TrimLeft(s, " \t")
	for len(out) < n-1 && rest != "" {
		i := strings.IndexAny(rest, " \t")
		if i < 0 {
			break
		}
		out = append(out, rest[:i])
		rest = strings.TrimLeft(rest[i:], " \t")
	}
	if rest != "" {
		out = append(out, rest)
	}
	return out
}

// kvLine reports whether a line is "key: value", which is how a relationship
// written long-hand is told from one written compactly.
func kvLine(line string) (string, string, bool) {
	s := strings.TrimSpace(line)
	if s == "" || strings.HasPrefix(s, "#") {
		return "", "", false
	}
	i := strings.Index(s, ":")
	if i < 0 {
		return "", "", false
	}
	return strings.TrimSpace(s[:i]), strings.TrimSpace(s[i+1:]), true
}

func parseSched(body string) *Schedule {
	kv := notation.KV(body)
	slot := func(k string) *string {
		if v := kv[k]; v != "" {
			return &v
		}
		return nil
	}
	return &Schedule{
		Morning: slot("morning"), Afternoon: slot("afternoon"),
		Evening: slot("evening"), Night: slot("night"),
	}
}

func parseEra(body string) []EraState {
	var out []EraState
	for _, item := range notation.SplitItems(body) {
		kv := notation.KV(item)
		e := EraState{
			EraID:       kv["era"],
			Status:      "alive",
			Occupation:  kv["occupation"],
			Disposition: kv["disposition"],
			Notes:       kv["notes"],
		}
		if e.EraID == "" {
			e.EraID = kv["era_id"]
		}
		if v, ok := kv["status"]; ok {
			e.Status = v
		}
		if v := kv["age"]; v != "" && isDigits(v) {
			if n, err := strconv.Atoi(v); err == nil {
				e.Age = &n
			}
		}
		out = append(out, e)
	}
	return out
}

func isDigits(s string) bool {
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return s != ""
}

func parseDrivermap(body string) *Drivermap {
	dm := &Drivermap{Profile: map[string]string{}}
	for k, v := range notation.KV(body) {
		if k == "features" {
			for _, f := range strings.Split(v, ",") {
				if f = strings.TrimSpace(f); f != "" {
					dm.SituationFeatures = append(dm.SituationFeatures, f)
				}
			}
			continue
		}
		dm.Profile[k] = v
	}
	return dm
}

func parseWrong(body string) []WrongExample {
	var out []WrongExample
	for _, item := range notation.SplitItems(body) {
		var w WrongExample
		for _, raw := range strings.Split(strings.TrimSpace(item), "\n") {
			line := strings.TrimSpace(raw)
			if line == "" {
				continue
			}
			switch {
			case strings.HasPrefix(line, "@when"):
				w.When, _ = notation.Directive(line, "@when")
			case strings.HasPrefix(line, "@beat"):
				w.Beat, _ = notation.Directive(line, "@beat")
			case strings.HasPrefix(line, "#"):
				// Unreachable: SplitItems drops comment lines before this sees
				// them, in both implementations. Kept because the reference
				// keeps it, so the two read the same when compared side by
				// side — a comment as an entry's context is a documented
				// intent that the item splitter has never let through.
				w.Context = strings.TrimSpace(strings.TrimLeft(line, "# "))
			case strings.HasPrefix(line, "{{user}}:"):
				w.Context = line
			case strings.HasPrefix(line, "WRONG:"):
				// The two examples lose their surrounding quotes and the why
				// does not, so a card can quote inside its reasoning.
				w.Wrong = strings.Trim(strings.TrimSpace(line[6:]), `"`)
			case strings.HasPrefix(line, "RIGHT:"):
				w.Right = strings.Trim(strings.TrimSpace(line[6:]), `"`)
			case strings.HasPrefix(line, "WHY:"):
				w.Why = strings.TrimSpace(line[4:])
			}
		}
		if w.Wrong != "" {
			out = append(out, w)
		}
	}
	return out
}

func parseTests(body string) []Test {
	var out []Test
	for _, item := range notation.SplitItems(body) {
		var t Test
		for _, raw := range strings.Split(strings.TrimSpace(item), "\n") {
			line := strings.TrimSpace(raw)
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			if rest, ok := notation.Directive(line, "@when"); ok {
				t.When = rest
				continue
			}
			if rest, ok := notation.Directive(line, "@beat"); ok {
				t.Beat = rest
				continue
			}
			low := strings.ToLower(line)
			switch {
			case strings.HasPrefix(low, "name:"):
				t.Name = strings.TrimSpace(line[5:])
			case strings.HasPrefix(low, "dimension:"):
				t.Dimension = strings.TrimSpace(line[10:])
			case strings.HasPrefix(low, "question:"):
				t.Question = strings.TrimSpace(line[9:])
			case strings.HasPrefix(low, "fail:"):
				t.FailExamples = append(t.FailExamples, strings.TrimSpace(line[5:]))
			case strings.HasPrefix(low, "pass:"):
				t.PassExamples = append(t.PassExamples, strings.TrimSpace(line[5:]))
			case strings.HasPrefix(low, "why:"):
				t.Why = strings.TrimSpace(line[4:])
			}
		}
		// A test with no question asks nothing, so it is dropped rather than
		// rendered as a heading with nothing under it.
		if t.Name != "" && t.Question != "" {
			out = append(out, t)
		}
	}
	return out
}

func parsePostproc(body string) []PostProcRule {
	var out []PostProcRule
	for i, item := range notation.SplitItems(body) {
		kv := notation.KV(item)
		action := strings.ToLower(strings.TrimSpace(kv["action"]))
		pattern := strings.TrimSpace(kv["pattern"])
		if pattern == "" || (action != "reject" && action != "strip" && action != "warn") {
			// An authoring error in one rule should not cost the card.
			continue
		}
		id := strings.TrimSpace(kv["id"])
		if id == "" {
			id = "postproc_" + strconv.Itoa(i)
		}
		out = append(out, PostProcRule{
			Action: action, Pattern: pattern,
			Why: strings.TrimSpace(kv["why"]), RuleID: id,
		})
	}
	return out
}
