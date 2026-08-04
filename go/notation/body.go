package notation

import "strings"

// SplitItems cuts a block body on --- lines, dropping blank and commented
// lines. Interior indentation survives; what an item means is the caller's
// business, so trimming it is too.
func SplitItems(body string) []string {
	var items []string
	var cur []string
	for _, line := range strings.Split(body, "\n") {
		s := strings.TrimSpace(line)
		switch {
		case s == "---":
			if len(cur) > 0 {
				items = append(items, strings.Join(cur, "\n"))
				cur = nil
			}
		case s != "" && !strings.HasPrefix(s, "#"):
			cur = append(cur, line)
		}
	}
	if len(cur) > 0 {
		items = append(items, strings.Join(cur, "\n"))
	}
	return items
}

// KV reads `key: value` lines. A line with no colon continues the value above
// it, which is how a kernel can be wrapped across lines in a card.
func KV(body string) map[string]string {
	out := map[string]string{}
	last := ""
	for _, line := range strings.Split(body, "\n") {
		s := strings.TrimSpace(line)
		if s == "" || strings.HasPrefix(s, "#") {
			continue
		}
		if i := strings.Index(s, ":"); i >= 0 {
			k := strings.TrimSpace(s[:i])
			out[k] = strings.TrimSpace(s[i+1:])
			last = k
			continue
		}
		if last != "" {
			out[last] += " " + s
		}
	}
	return out
}

// Directive matches an @-prefixed annotation inside a block body and returns
// what follows it. @when, @beat and @tier are the ones effigy defines; the
// match is by name so a consumer can read its own.
func Directive(line, name string) (string, bool) {
	if !strings.HasPrefix(line, name) {
		return "", false
	}
	return strings.TrimSpace(line[len(name):]), true
}
