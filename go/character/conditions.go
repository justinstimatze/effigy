package character

import (
	"regexp"
	"strconv"
	"strings"
)

var (
	condAnd   = regexp.MustCompile(`\s+AND\s+`)
	condNum   = regexp.MustCompile(`^(\w+)\s*([><=!]+)\s*([\d.]+)`)
	condFact  = regexp.MustCompile(`^fact:(\S+)`)
	condTrust = regexp.MustCompile(`^trust\s*[><=]`)
)

// parseConditions reads a condition string like "trust>=0.2 AND fact:knows" into
// the loosely typed map the reference AST carries: a numeric comparison keyed by
// its name, facts collected under "facts", and anything unrecognised under
// "raw".
func parseConditions(cond string) map[string]any {
	out := map[string]any{}
	for _, part := range condAnd.Split(strings.TrimSpace(cond), -1) {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if m := condNum.FindStringSubmatch(part); m != nil {
			v, err := strconv.ParseFloat(m[3], 64)
			if err != nil {
				continue
			}
			out[m[1]] = map[string]any{"op": m[2], "value": v}
			continue
		}
		if m := condFact.FindStringSubmatch(part); m != nil {
			facts, _ := out["facts"].([]string)
			out["facts"] = append(facts, m[1])
			continue
		}
		raw, _ := out["raw"].([]string)
		out["raw"] = append(raw, part)
	}
	return out
}

// normalizeCondition converts effigy's condition syntax to the unified DSL,
// which differs in one respect: a bare trust comparison is scoped to a
// character, and the id is not known at parse time. The reference leaves _NPC_
// for the caller to substitute and so does this.
func normalizeCondition(cond string) string {
	cond = strings.TrimSpace(cond)
	if cond == "" {
		return ""
	}
	var parts []string
	for _, part := range condAnd.Split(cond, -1) {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if condTrust.MatchString(part) {
			parts = append(parts, "trust:_NPC_"+part[len("trust"):])
			continue
		}
		parts = append(parts, part)
	}
	return strings.Join(parts, " AND ")
}
