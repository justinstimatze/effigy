// Package character reads an effigy card into the character AST effigy's own
// Python package produces, and expands it to the same JSON.
//
// notation, one directory over, stops where structure becomes schema: it hands
// back blocks and bodies and takes no position on what VOICE means. This
// package is the position. It is a port of effigy/parser.py and effigy/expand.py
// against effigy/notation.py's dataclasses, so a Go consumer can do what
// `python3 -m effigy expand` does without a Python interpreter and a checkout.
//
// The port is held to the original by parity_test.go, which runs both over
// every card it can find and compares the expanded JSON. That test is the
// specification; where the two disagree, this package is wrong.
//
// One deliberate difference. Python drops a block whose keyword it does not
// define, so a consumer's private block vanishes with no way to tell a typo
// from a deliberate extension. Character.ExtraBlocks keeps them, which is what
// notation.py already does for unrecognised @keys and for the same stated
// reason. Expand does not emit them, so the JSON stays identical.
package character

// NarrativeRole is the @narr header's vocabulary. An unrecognised value falls
// back to Neutral rather than failing, matching the reference.
type NarrativeRole string

const (
	RoleNeutral    NarrativeRole = "neutral"
	RoleAlly       NarrativeRole = "ally"
	RoleSuspect    NarrativeRole = "suspect"
	RoleAntagonist NarrativeRole = "antagonist"
	RoleInfoBroker NarrativeRole = "info_broker"
	RoleMentor     NarrativeRole = "mentor"
	RoleRival      NarrativeRole = "rival"
	RoleBystander  NarrativeRole = "bystander"
)

var narrativeRoles = map[string]NarrativeRole{
	"neutral": RoleNeutral, "ally": RoleAlly, "suspect": RoleSuspect,
	"antagonist": RoleAntagonist, "info_broker": RoleInfoBroker,
	"mentor": RoleMentor, "rival": RoleRival, "bystander": RoleBystander,
}

// Voice is the VOICE block: the kernel that always applies, and a peak that
// replaces it when PeakWhen evaluates true.
type Voice struct {
	Kernel   string
	Peak     string
	PeakWhen string
}

// Secret is one trust-gated secret. Layer is 1 (easy) to 3 (deep).
type Secret struct {
	Layer           int
	Secret          string
	RevealCondition string
	RelatedEra      string
}

// Relationship is a directed relationship to another character.
type Relationship struct {
	Target    string
	Type      string
	Intensity float64
	Notes     string
}

// EraState is the character's state in one era. Age is nil when the card does
// not give one, which is distinct from zero.
type EraState struct {
	EraID       string
	Status      string
	Age         *int
	Occupation  string
	Disposition string
	Notes       string
}

// Schedule is where the character is across the day. A nil slot is a slot the
// card left out, and expands to null rather than "".
type Schedule struct {
	Morning   *string
	Afternoon *string
	Evening   *string
	Night     *string
}

// Drivermap is the DM block: a trait profile and situation features.
type Drivermap struct {
	Profile           map[string]string
	SituationFeatures []string
}

// ArcPhase is one phase of the ARC block.
type ArcPhase struct {
	Name string
	// Conditions is the parsed form: numeric comparisons keyed by name, plus
	// "facts" and "raw" lists. Kept because the reference AST has it.
	Conditions map[string]any
	// ConditionStr is the condition normalized to the DSL, where a bare trust
	// comparison gains the _NPC_ scope placeholder the caller substitutes.
	ConditionStr string
	Voice        string
	Deflection   string
	// Beats is nil when the phase authors no rotation, which is distinct from
	// an empty one.
	Beats []string
}

// WrongExample is one WRONG entry: an anti-pattern and its correction.
type WrongExample struct {
	Context string
	Wrong   string
	Right   string
	Why     string
	When    string
	Beat    string
}

// Test is one TEST entry: a question the model asks about its own output.
type Test struct {
	Name         string
	Question     string
	FailExamples []string
	PassExamples []string
	Why          string
	Dimension    string
	When         string
	Beat         string
}

// PostProcRule is one POSTPROC rule, applied to generated output rather than
// to the prompt.
type PostProcRule struct {
	Action  string
	Pattern string
	Why     string
	RuleID  string
}

// NeverRule is one NEVER constraint. When is empty or "*" for always.
type NeverRule struct {
	Text string
	When string
}

// Goal is one GOALS entry.
type Goal struct {
	Name      string
	Weight    float64
	GrowsWith string
}

// Example is one MES or UNC entry. It mirrors notation.Example; the tier and
// gates live here so a consumer can filter by trust.
type Example struct {
	Text string
	Tier string
	When string
	Beat string
}

// Character is the whole card.
type Character struct {
	CharID        string
	Name          string
	Role          string
	Archetype     string
	NarrativeRole NarrativeRole
	PresenceNote  string
	TropeTags     []string
	Theme         string

	// ExtraHeaders keeps @keys the notation does not define, under their key
	// WITH the leading @, values accumulating so a repeated key is a list.
	// This is where @gate and @shape arrive for a consumer that uses them.
	ExtraHeaders map[string][]string

	// ExtraBlocks keeps blocks the notation does not define, by keyword, bodies
	// verbatim. Python discards these; see the package comment.
	ExtraBlocks map[string][]string

	Voice *Voice

	Traits        []string
	NeverWouldSay []NeverRule
	Quirks        []string

	MesExamples      []Example
	UncertaintyVoice []string

	ArcPhases     []ArcPhase
	Goals         []Goal
	GoalBehaviors map[string]string

	Secrets       []Secret
	Relationships []Relationship
	Schedule      *Schedule
	EraStates     []EraState
	Drivermap     *Drivermap

	WrongExamples  []WrongExample
	Tests          []Test
	PostProcessors []PostProcRule

	Props          []string
	ArrivalLines   []string
	DepartureLines []string
}
