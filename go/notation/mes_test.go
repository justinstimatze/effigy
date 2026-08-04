package notation

import (
	"reflect"
	"testing"
)

// The branch two consumers had each reimplemented. An item with a user turn is
// an exchange and keeps its lines; an item without one is an utterance and
// becomes a single {{char}}: line.
func TestMESJoinsExchangesAndUtterancesDifferently(t *testing.T) {
	got := MES(`
  {{user}}: what now
  {{char}}: nothing
  ---
  speaks up
  without being asked
  ---
  {{char}}: already prefixed
`)
	want := []Example{
		{Text: "{{user}}: what now\n{{char}}: nothing", Tier: "any"},
		{Text: "{{char}}: speaks up without being asked", Tier: "any"},
		{Text: "{{char}}: already prefixed", Tier: "any"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("MES = %#v\nwant %#v", got, want)
	}
}

func TestMESReadsAnnotations(t *testing.T) {
	got := MES(`
  @tier low
  @when scene.tense
  @beat arrival
  says a thing
  ---
  @tier nonsense
  says another
`)
	want := []Example{
		{Text: "{{char}}: says a thing", Tier: "low", When: "scene.tense", Beat: "arrival"},
		{Text: "{{char}}: says another", Tier: "any"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("MES = %#v\nwant %#v", got, want)
	}
}

// Annotations reset at the item boundary rather than leaking down the block.
func TestMESResetsAnnotationsPerItem(t *testing.T) {
	got := MES("@tier high\nfirst\n---\nsecond\n")
	if got[0].Tier != "high" {
		t.Errorf("first tier = %q", got[0].Tier)
	}
	if got[1].Tier != "any" {
		t.Errorf("second tier = %q, want it reset", got[1].Tier)
	}
}

func TestMESReadsLegacyTrustComments(t *testing.T) {
	for _, tc := range []struct{ comment, want string }{
		{"# LOW TRUST: guarded", "low"},
		{"# MODERATE TRUST", "moderate"},
		{"# MID TRUST", "moderate"},
		{"# HIGH TRUST", "high"},
		{"# just a comment", "any"},
	} {
		got := MES(tc.comment + "\nsays a thing\n")
		if len(got) != 1 {
			t.Fatalf("%q: got %d examples", tc.comment, len(got))
		}
		if got[0].Tier != tc.want {
			t.Errorf("%q: tier = %q, want %q", tc.comment, got[0].Tier, tc.want)
		}
		if got[0].Text != "{{char}}: says a thing" {
			t.Errorf("%q: the comment reached the text: %q", tc.comment, got[0].Text)
		}
	}
}

// An @key the notation does not define is content, not an annotation. That is
// what the reference parser does with it, and it is the same position effigy
// takes on unrecognised header keys and undeclared blocks.
func TestMESKeepsUndefinedDirectivesAsContent(t *testing.T) {
	got := MES("@sidenote worth keeping\nsays a thing\n")
	if len(got) != 1 {
		t.Fatalf("got %d examples", len(got))
	}
	if want := "{{char}}: @sidenote worth keeping says a thing"; got[0].Text != want {
		t.Errorf("text = %q, want %q", got[0].Text, want)
	}
}

func TestMESIgnoresEmptyItems(t *testing.T) {
	if got := MES("\n---\n\n---\n"); len(got) != 0 {
		t.Errorf("MES = %#v, want none", got)
	}
}
