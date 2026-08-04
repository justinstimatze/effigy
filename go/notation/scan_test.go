package notation

import (
	"reflect"
	"testing"
)

const card = `# a comment at the top
@id demo
@theme a theme
@gate one
@gate two

VOICE {
  kernel: says the thing
  peak: and then stops
}

TRAITS [
  dry, exact
]

POSTPROC [
  action: reject
  pattern: [Ff]lourish{1,2}
]

LEDGER [
  a private block nobody upstream defines
]

not a block at all
`

func TestScanReadsHeadersAndBlocks(t *testing.T) {
	doc, err := Scan([]byte(card))
	if err != nil {
		t.Fatal(err)
	}

	wantHeaders := []Header{
		{"id", "demo"}, {"theme", "a theme"}, {"gate", "one"}, {"gate", "two"},
	}
	if !reflect.DeepEqual(doc.Headers, wantHeaders) {
		t.Errorf("headers = %v, want %v", doc.Headers, wantHeaders)
	}

	var names []string
	for _, b := range doc.Blocks {
		names = append(names, b.Name)
	}
	want := []string{"VOICE", "TRAITS", "POSTPROC", "LEDGER"}
	if !reflect.DeepEqual(names, want) {
		t.Errorf("blocks = %v, want %v", names, want)
	}
}

// An undeclared block comes back whole. effigy keeps unrecognised @keys rather
// than dropping them, and a consumer's private block gets the same treatment —
// otherwise the first one to need it writes its own reader.
func TestScanKeepsUndeclaredBlocks(t *testing.T) {
	doc, err := Scan([]byte(card))
	if err != nil {
		t.Fatal(err)
	}
	body, ok := doc.Block("LEDGER")
	if !ok {
		t.Fatal("LEDGER was dropped")
	}
	if Defined("LEDGER") {
		t.Error("LEDGER should not be in the vocabulary")
	}
	if got := SplitItems(body); len(got) != 1 {
		t.Errorf("LEDGER body = %q, want one item", got)
	}
}

// The nested-delimiter count is why readBlock is not a substring search: a
// regex carrying a character class or a repeat count closes nothing.
func TestScanCountsNestedDelimiters(t *testing.T) {
	doc, err := Scan([]byte(card))
	if err != nil {
		t.Fatal(err)
	}
	body, ok := doc.Block("POSTPROC")
	if !ok {
		t.Fatal("POSTPROC missing")
	}
	if got := KV(body)["pattern"]; got != "[Ff]lourish{1,2}" {
		t.Errorf("pattern = %q, want the whole regex", got)
	}
}

// A block's delimiter may be written on the next line, or after a space. cope
// read both and drag's hand-rolled VOICE reader read neither, which is the
// divergence this package exists to stop.
func TestScanAcceptsDelimiterOffTheKeywordLine(t *testing.T) {
	for _, src := range []string{
		"VOICE{kernel: k}",
		"VOICE {kernel: k}",
		"VOICE\n{kernel: k}",
		"VOICE  \n\n  {kernel: k}",
	} {
		doc, err := Scan([]byte(src))
		if err != nil {
			t.Fatalf("%q: %v", src, err)
		}
		body, ok := doc.Block("VOICE")
		if !ok {
			t.Fatalf("%q: no VOICE block", src)
		}
		if got := KV(body)["kernel"]; got != "k" {
			t.Errorf("%q: kernel = %q", src, got)
		}
	}
}

func TestScanRejectsUnterminatedBlock(t *testing.T) {
	_, err := Scan([]byte("TRAITS [\n  dry, exact\n"))
	if err == nil {
		t.Fatal("expected an error for a block that never closes")
	}
	if got := err.Error(); got != `TRAITS: unterminated block, no closing "]"` {
		t.Errorf("err = %q", got)
	}
}

func TestScanRejectsWrongDelimiter(t *testing.T) {
	_, err := Scan([]byte("VOICE [kernel: k]"))
	if err == nil {
		t.Fatal("expected an error: VOICE is a braced block")
	}
	if got := err.Error(); got != `VOICE: expected "{"` {
		t.Errorf("err = %q", got)
	}
}

func TestValuesCollectsRepeatedHeaders(t *testing.T) {
	doc, err := Scan([]byte(card))
	if err != nil {
		t.Fatal(err)
	}
	if got, want := doc.Values("gate"), []string{"one", "two"}; !reflect.DeepEqual(got, want) {
		t.Errorf("gate = %v, want %v", got, want)
	}
	if got := doc.Values("nothing"); got != nil {
		t.Errorf("absent key = %v, want nil", got)
	}
}

func TestKVContinuesValueAcrossLines(t *testing.T) {
	kv := KV("kernel: first part\n  and the rest\npeak: p")
	if got, want := kv["kernel"], "first part and the rest"; got != want {
		t.Errorf("kernel = %q, want %q", got, want)
	}
	if got := kv["peak"]; got != "p" {
		t.Errorf("peak = %q", got)
	}
}

func TestSplitItemsCutsOnRules(t *testing.T) {
	got := SplitItems("one\n---\ntwo\n# dropped\nthree\n---\n")
	want := []string{"one", "two\nthree"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("items = %q, want %q", got, want)
	}
}

func TestDirective(t *testing.T) {
	if got, ok := Directive("@when scene", "@when"); !ok || got != "scene" {
		t.Errorf("Directive = %q, %v", got, ok)
	}
	if _, ok := Directive("text", "@when"); ok {
		t.Error("matched a line that is not a directive")
	}
}
