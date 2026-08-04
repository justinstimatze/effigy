package character

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"testing"
)

// TestParityOnSyntheticCards covers the branches no real card reaches.
//
// The corpus tests are the stronger evidence where they apply, but mutation
// testing showed three parser branches survive being broken because no card
// anybody has written exercises them: a beats list separated by → rather than
// ->, a TRAITS list that wraps in the middle of a trait, and a SCHED block with
// a slot left out. A parser branch with no card behind it is a branch that will
// be wrong the first time somebody writes that card.
//
// These are still diffed against the reference rather than against an expected
// value written here, so they assert what effigy does and not what this file's
// author believed it does.
func TestParityOnSyntheticCards(t *testing.T) {
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

	for _, tc := range []struct{ name, card string }{
		{
			// -> is what every card in the corpus uses; the reference accepts
			// both and only this one proves it.
			name: "beats separated by the arrow glyph",
			card: "@id t\nARC{\n  quiet → heat<=1\n    voice: \"v\"\n    beats: ONE → TWO → THREE\n}\n",
		},
		{
			name: "traits wrapping mid-trait",
			card: "@id t\nTRAITS[\n  first-trait, second-trait that\n  continues on the next line, third\n]\n",
		},
		{
			name: "schedule with slots left out",
			card: "@id t\nSCHED{\n  morning: the bar\n  night:\n}\n",
		},
		{
			name: "era with and without an age",
			card: "@id t\nERA[\n  era: past\n  age: 34\n  occupation: cooper\n  ---\n  era: present\n  status: dead\n]\n",
		},
		{
			name: "relationships both ways round",
			card: "@id t\nRELS{\n  town_mayor protects 0.6 \"Owes her a favour.\"\n  smith suspects\n  target: npc_b\n  cooper distrusts not_a_number\n}\n",
		},
		{
			name: "secrets compact and long form",
			card: "@id t\nSECRETS[\n  L2: she was there that night\n  ---\n  layer: 3\n  secret: the ledger is hers\n  reveal: REQUIRES player knows ledger_found or cellar_seen — then she folds\n  era: past\n]\n",
		},
		{
			name: "drivermap profile and features",
			card: "@id t\nDM{\n  openness: +\n  caution: -\n  features: crowded, after_hours, raining\n}\n",
		},
		{
			name: "goals with weights and growth",
			card: "@id t\nGOALS{\n  keep_peace 0.8 → grows with trust\n  protect_regulars 0.7\n  survive\n}\n",
		},
		{
			name: "unknown narrative role falls back",
			card: "@id t\n@narr not_a_role\n@tropes  one , two ,, three \n",
		},
		{
			name: "peak and peak_when",
			card: "@id t\nVOICE{\n  kernel: k\n  peak: p\n  peak_when: trust>=0.6 AND fact:x\n}\n",
		},
		{
			name: "never rules some gated",
			card: "@id t\nNEVER[\n  @when trust>=0.6\n  says the quiet part\n  ---\n  explains the joke\n]\n",
		},
		{
			name: "arc condition continued on its own line",
			card: "@id t\nARC{\n  open → trust>=0.6\n    ruin>=4\n    voice: \"first part\"\n    and the rest of it\n}\n",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "c.effigy")
			if err := os.WriteFile(path, []byte(tc.card), 0o644); err != nil {
				t.Fatal(err)
			}

			c, err := Parse([]byte(tc.card))
			if err != nil {
				t.Fatalf("Parse: %v", err)
			}
			gotJSON, err := json.Marshal(astDump(c))
			if err != nil {
				t.Fatal(err)
			}

			var got, want any
			if err := json.Unmarshal(gotJSON, &got); err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(pythonAST(t, repo, path), &want); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, want) {
				t.Errorf("AST differs from the reference%s", diffKeys(got, want))
			}
		})
	}
}
