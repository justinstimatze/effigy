package character

import (
	"strings"

	"github.com/justinstimatze/effigy/go/notation"
)

// Parse reads a card into its AST. It is the Go side of effigy's parse(), and
// parity_test.go holds the two to the same output.
//
// Reading the card's structure is notation's job; what is here is the meaning
// laid over it. A block the notation does not define lands in ExtraBlocks
// rather than being dropped.
func Parse(src []byte) (*Character, error) {
	doc, err := notation.Scan(src)
	if err != nil {
		return nil, err
	}

	c := &Character{
		NarrativeRole: RoleNeutral,
		ExtraHeaders:  map[string][]string{},
		ExtraBlocks:   map[string][]string{},
	}

	for _, h := range doc.Headers {
		switch h.Key {
		case "id":
			c.CharID = h.Value
		case "name":
			c.Name = h.Value
		case "role":
			c.Role = h.Value
		case "arch":
			c.Archetype = h.Value
		case "narr":
			if r, ok := narrativeRoles[h.Value]; ok {
				c.NarrativeRole = r
			} else {
				// An unknown role is an authoring error that should not cost
				// the card, so it reads as neutral.
				c.NarrativeRole = RoleNeutral
			}
		case "presence":
			c.PresenceNote = h.Value
		case "tropes":
			for _, t := range strings.Split(h.Value, ",") {
				if t = strings.TrimSpace(t); t != "" {
					c.TropeTags = append(c.TropeTags, t)
				}
			}
		case "theme":
			c.Theme = h.Value
		default:
			// Kept under the key WITH its @, matching notation.py's extra, so
			// a typo'd header and a downstream consumer's header stay
			// distinguishable from inside the file.
			k := "@" + h.Key
			c.ExtraHeaders[k] = append(c.ExtraHeaders[k], h.Value)
		}
	}

	for _, b := range doc.Blocks {
		switch b.Name {
		case "VOICE":
			c.Voice = parseVoice(b.Body)
		case "TRAITS":
			c.Traits = parseTraits(b.Body)
		case "NEVER":
			c.NeverWouldSay = parseNever(b.Body)
		case "QUIRKS":
			c.Quirks = parseLines(b.Body)
		case "MES":
			c.MesExamples = parseExamples(b.Body)
		case "UNC":
			c.UncertaintyVoice = parseUncertainty(b.Body)
		case "ARC":
			c.ArcPhases = parseArc(b.Body)
		case "GOALS":
			c.Goals = parseGoals(b.Body)
		case "SECRETS":
			c.Secrets = parseSecrets(b.Body)
		case "RELS":
			c.Relationships = parseRels(b.Body)
		case "SCHED":
			c.Schedule = parseSched(b.Body)
		case "ERA":
			c.EraStates = parseEra(b.Body)
		case "DM":
			c.Drivermap = parseDrivermap(b.Body)
		case "ARRIVE":
			c.ArrivalLines = parseLines(b.Body)
		case "DEPART":
			c.DepartureLines = parseLines(b.Body)
		case "WRONG":
			c.WrongExamples = parseWrong(b.Body)
		case "TEST":
			c.Tests = parseTests(b.Body)
		case "PROPS":
			c.Props = parseLines(b.Body)
		case "BEHAVIORS":
			c.GoalBehaviors = notation.KV(b.Body)
		case "POSTPROC":
			c.PostProcessors = parsePostproc(b.Body)
		default:
			c.ExtraBlocks[b.Name] = append(c.ExtraBlocks[b.Name], b.Body)
		}
	}

	return c, nil
}
