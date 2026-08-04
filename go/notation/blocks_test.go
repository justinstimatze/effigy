package notation

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// TestVocabularyMatchesPython is the reason the block list lives in this
// package at all.
//
// The names were a hand-copy in a downstream repo until this module existed,
// which meant effigy could define a block and a consumer would not see it until
// somebody noticed. Moving the copy up one level does not fix that on its own —
// it just moves the copy. This is the part that fixes it: the Go map and
// BLOCK_KEYWORDS in effigy/notation.py are compared, and adding a block to one
// and not the other fails here rather than in a consumer months later.
func TestVocabularyMatchesPython(t *testing.T) {
	path := filepath.Join("..", "..", "effigy", "notation.py")
	src, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading the notation's own declaration: %v", err)
	}

	want := pythonBlockKeywords(t, string(src))
	if len(want) == 0 {
		t.Fatalf("%s: found no BLOCK_KEYWORDS; the declaration moved or changed shape", path)
	}

	got := map[string]bool{}
	for _, name := range Keywords() {
		got[name] = true
	}

	for name := range want {
		if !got[name] {
			t.Errorf("%s defines %s and this package does not know it", path, name)
		}
	}
	for name := range got {
		if !want[name] {
			t.Errorf("this package knows %s and %s does not define it", name, path)
		}
	}
}

// pythonBlockKeywords pulls the names out of the BLOCK_KEYWORDS set literal.
// Deliberately literal-minded: it reads one declaration by name and fails loudly
// if that declaration is not there, rather than guessing at Python.
func pythonBlockKeywords(t *testing.T, src string) map[string]bool {
	t.Helper()
	const decl = "BLOCK_KEYWORDS = {"
	i := strings.Index(src, decl)
	if i < 0 {
		return nil
	}
	rest := src[i+len(decl):]
	j := strings.Index(rest, "}")
	if j < 0 {
		t.Fatalf("BLOCK_KEYWORDS is unterminated")
	}
	out := map[string]bool{}
	for _, m := range regexp.MustCompile(`"([A-Z]+)"`).FindAllStringSubmatch(rest[:j], -1) {
		out[m[1]] = true
	}
	return out
}

// TestDelimitersMatchPython checks the other half of the same fact. parser.py
// dispatches each keyword to _read_braced_block or _read_bracketed_block, and
// which one it picks is notation, not preference.
func TestDelimitersMatchPython(t *testing.T) {
	path := filepath.Join("..", "..", "effigy", "parser.py")
	src, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading the reference parser: %v", err)
	}

	// Each dispatch arm names its keyword and then calls one of the two
	// readers; pair them up in source order.
	arms := regexp.MustCompile(`"([A-Z]+)"[\s\S]{0,400}?_read_(braced|bracketed)_block`).
		FindAllStringSubmatch(string(src), -1)

	seen := 0
	for _, m := range arms {
		name, kind := m[1], m[2]
		d, known := vocabulary[name]
		if !known {
			continue // covered by TestVocabularyMatchesPython
		}
		seen++
		wantOpen := byte('[')
		if kind == "braced" {
			wantOpen = '{'
		}
		if d.open != wantOpen {
			t.Errorf("%s: parser.py reads it as %s, this package opens it with %q",
				name, kind, string(d.open))
		}
	}
	if seen < len(vocabulary) {
		t.Errorf("only matched %d of %d keywords against parser.py's dispatch; the arms changed shape",
			seen, len(vocabulary))
	}
}
