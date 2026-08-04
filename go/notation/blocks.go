package notation

import "sort"

// delim is the pair a block's body is wrapped in.
type delim struct{ open, close byte }

var (
	braces   = delim{'{', '}'}
	brackets = delim{'[', ']'}
)

// vocabulary is every block effigy defines and the delimiter each one uses.
//
// The names are BLOCK_KEYWORDS in effigy/notation.py, which the line above that
// declaration calls "used by parser to dispatch". The delimiter each name takes
// is the same fact seen from the other side: parser.py picks _read_braced_block
// or _read_bracketed_block per keyword. Both halves are notation, so both live
// here. blocks_test.go reads the Python declaration and fails if this map and
// that set stop agreeing.
var vocabulary = map[string]delim{
	"VOICE":     braces,
	"ARC":       braces,
	"GOALS":     braces,
	"RELS":      braces,
	"SCHED":     braces,
	"DM":        braces,
	"BEHAVIORS": braces,

	"TRAITS":   brackets,
	"NEVER":    brackets,
	"QUIRKS":   brackets,
	"MES":      brackets,
	"UNC":      brackets,
	"SECRETS":  brackets,
	"ERA":      brackets,
	"ARRIVE":   brackets,
	"DEPART":   brackets,
	"WRONG":    brackets,
	"TEST":     brackets,
	"PROPS":    brackets,
	"POSTPROC": brackets,
}

// Defined reports whether name is a block effigy's notation defines. A block
// that is not defined is still readable — see Scan — so this answers "does
// effigy know this one", not "is this allowed".
func Defined(name string) bool {
	_, ok := vocabulary[name]
	return ok
}

// Keywords returns every defined block name, sorted.
func Keywords() []string {
	out := make([]string, 0, len(vocabulary))
	for name := range vocabulary {
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}
