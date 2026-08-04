package character

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"testing"
)

// TestParityWithPython is the specification for this whole package.
//
// effigy/parser.py and effigy/expand.py are the notation's authority. Anywhere
// this port and they disagree about a card, this port is wrong by definition,
// so the test does not check a hand-written expectation — it runs both and
// diffs. That is what makes deleting a Python call site safe rather than
// hopeful: the reader replacing it has been shown to agree on real cards.
//
// It sweeps every card it can find, starting with effigy's own fixture and
// adding the sibling repos when they are checked out next door. CI has only the
// fixture, which is enough to catch a port that has broken outright; a local run
// has every card anybody has written, which is what catches the rest.
func TestParityWithPython(t *testing.T) {
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
	t.Logf("comparing %d cards against the reference", len(cards))

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
			gotJSON, err := c.ExpandJSON(false)
			if err != nil {
				t.Fatal(err)
			}

			wantJSON := pythonExpand(t, repo, path)

			// Compared as values rather than bytes: Go sorts object keys and
			// Python writes them in insertion order, and neither is a fact
			// about the card.
			var got, want any
			if err := json.Unmarshal(gotJSON, &got); err != nil {
				t.Fatalf("unmarshal ours: %v", err)
			}
			if err := json.Unmarshal(wantJSON, &want); err != nil {
				t.Fatalf("unmarshal the reference's: %v\n%s", err, wantJSON)
			}
			if !reflect.DeepEqual(got, want) {
				t.Errorf("expanded JSON differs from the reference\n%s", diffKeys(got, want))
			}
		})
	}
}

// pythonExpand runs the reference over one card and returns its JSON.
func pythonExpand(t *testing.T, repo, card string) []byte {
	t.Helper()
	const script = `
import json, sys
sys.path.insert(0, sys.argv[1])
from effigy import parse
from effigy.expand import expand
with open(sys.argv[2], encoding="utf-8") as fh:
    print(json.dumps(expand(parse(fh.read())), ensure_ascii=False))
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

// findCards collects effigy's own fixtures plus any sibling repo's cards that
// happen to be checked out. A missing sibling is not a failure — most machines
// running this have one repo.
func findCards(t *testing.T, repo string) []string {
	t.Helper()
	globs := []string{
		filepath.Join(repo, "effigy", "tests", "fixtures", "*.effigy"),
	}
	parent := filepath.Dir(repo)
	for _, rel := range []string{
		filepath.Join("cope", "card", "*.effigy"),
		filepath.Join("cope", "card", "demo", "*.effigy"),
		filepath.Join("costean", "voices", "*.effigy"),
		filepath.Join("drag", "card", "*.effigy"),
	} {
		globs = append(globs, filepath.Join(parent, rel))
	}

	var cards []string
	for _, g := range globs {
		found, err := filepath.Glob(g)
		if err != nil {
			t.Fatal(err)
		}
		cards = append(cards, found...)
	}
	return cards
}

// diffKeys reports which top-level keys differ, so a failure names the field
// rather than printing two whole characters.
func diffKeys(got, want any) string {
	g, gok := got.(map[string]any)
	w, wok := want.(map[string]any)
	if !gok || !wok {
		return "one side is not an object"
	}
	out := ""
	for k, wv := range w {
		gv, ok := g[k]
		if !ok {
			out += "\n  missing from ours: " + k
			continue
		}
		if !reflect.DeepEqual(gv, wv) {
			gj, _ := json.Marshal(gv)
			wj, _ := json.Marshal(wv)
			out += "\n  " + k + ":\n    ours: " + string(gj) + "\n    ref:  " + string(wj)
		}
	}
	for k := range g {
		if _, ok := w[k]; !ok {
			out += "\n  only in ours: " + k
		}
	}
	return out
}
