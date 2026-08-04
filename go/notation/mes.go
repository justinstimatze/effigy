package notation

import "strings"

// Example is one MES entry: a dialogue example with the annotations that gate
// it. It is the one place this package produces a rendered string rather than
// handing back what the card said, because how a MES item becomes text is a
// rule of the notation and not a choice a consumer gets to make — see MES.
type Example struct {
	// Text is the example, joined per the notation's rules.
	//
	// Its shape is not uniform, and a consumer that prints it has to care: an
	// exchange arrives as multiple lines carrying literal "{{user}}:" and
	// "{{char}}:" prefixes, while an utterance is one line with a "{{char}}:"
	// prefix. A renderer that strips neither and expects the second will print
	// a user turn into a frame meant for the character speaking alone.
	Text string
	// Tier is the trust level the example is gated to: "low", "moderate",
	// "high", or "any" when ungated.
	Tier string
	// When is the raw @when condition. Empty and "*" both mean always active;
	// the condition DSL belongs to whoever evaluates it.
	When string
	// Beat is the @beat label, categorical rather than conditional. Empty
	// means universal for the phase.
	Beat string
}

// MES cuts a MES block body into examples.
//
// This is not SplitItems with a join on the end, and the difference is the
// whole reason it lives here. A MES item is either one utterance or a whole
// exchange, and the notation renders those two differently: an item carrying a
// {{user}}: line keeps its line breaks, because they are what make it an
// exchange, while an item without one is joined into a single line and given a
// {{char}}: prefix if it does not have one. That branch is effigy/parser.py:339-345.
//
// Consumers were reimplementing it. Two had, which is two chances to drift on
// the branch that decides whether a card teaches the model an exchange or an
// interjection — and a consumer that renders the result verbatim shows the
// {{user}}: prefix to a reader when it gets that wrong.
//
// Annotations are consumed rather than returned as text: @tier, @when and
// @beat, plus the older `# LOW TRUST:` comment form. A line beginning with any
// other @key is content, which is what the reference parser does with it.
func MES(body string) []Example {
	var out []Example

	lines := []string{}
	tier, when, beat := "any", "", ""
	flush := func() {
		if len(lines) == 0 {
			return
		}
		out = append(out, Example{Text: joinExample(lines), Tier: tier, When: when, Beat: beat})
		lines, tier, when, beat = []string{}, "any", "", ""
	}

	for _, raw := range strings.Split(body, "\n") {
		line := strings.TrimSpace(raw)
		switch {
		case line == "---":
			flush()
		case strings.HasPrefix(line, "@tier"):
			if v, _ := Directive(line, "@tier"); isTier(strings.ToLower(v)) {
				tier = strings.ToLower(v)
			}
		case strings.HasPrefix(line, "@when"):
			when, _ = Directive(line, "@when")
		case strings.HasPrefix(line, "@beat"):
			beat, _ = Directive(line, "@beat")
		case strings.HasPrefix(line, "#"):
			if t, ok := legacyTier(line); ok {
				tier = t
			}
		case line != "":
			lines = append(lines, line)
		}
	}
	flush()
	return out
}

// joinExample is the branch itself: an exchange keeps its lines, an utterance
// becomes one.
func joinExample(lines []string) string {
	for _, l := range lines {
		if strings.HasPrefix(l, "{{user}}:") {
			return strings.Join(lines, "\n")
		}
	}
	text := strings.Join(lines, " ")
	if !strings.HasPrefix(text, "{{char}}:") {
		text = "{{char}}: " + text
	}
	return text
}

func isTier(v string) bool {
	return v == "low" || v == "moderate" || v == "high"
}

// legacyTier reads the `# LOW TRUST:` comment form that predates @tier.
func legacyTier(line string) (string, bool) {
	up := strings.ToUpper(line)
	if !strings.Contains(up, "TRUST") {
		return "", false
	}
	switch {
	case strings.Contains(up, "LOW"):
		return "low", true
	case strings.Contains(up, "MODERATE"), strings.Contains(up, "MID"):
		return "moderate", true
	case strings.Contains(up, "HIGH"):
		return "high", true
	}
	return "", false
}
