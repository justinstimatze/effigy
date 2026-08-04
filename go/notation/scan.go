package notation

import (
	"fmt"
	"strings"
)

// Header is one @key line: the key with its @ removed, and the rest of the
// line trimmed. What a key means is the reader's business — effigy carries
// unrecognised ones through rather than dropping them, and so does this.
type Header struct {
	Key   string
	Value string
}

// Block is one delimited block: the keyword that opened it and its body with
// the delimiters removed and nothing else done to it.
type Block struct {
	Name string
	Body string
}

// Document is a card read as far as the notation defines it. Headers and
// Blocks are each in source order.
type Document struct {
	Headers []Header
	Blocks  []Block
}

// Block returns the body of the first block with the given name.
func (d *Document) Block(name string) (string, bool) {
	for _, b := range d.Blocks {
		if b.Name == name {
			return b.Body, true
		}
	}
	return "", false
}

// Values returns every value carried by headers with the given key, in source
// order. @gate and @shape repeat, one rule to a line, so a reader of those
// wants all of them and not the first.
func (d *Document) Values(key string) []string {
	var out []string
	for _, h := range d.Headers {
		if h.Key == key {
			out = append(out, h.Value)
		}
	}
	return out
}

// Scan reads a card's structure: its @headers and its delimited blocks, bodies
// untouched.
//
// A block whose name the vocabulary does not know is returned rather than
// dropped, with its body read as a unit. effigy already takes that position on
// header keys — notation.py keeps unrecognised @keys in `extra` so a consumer's
// private key survives parsing — and a block layer stricter than the notation
// it implements would send the first consumer with a private block straight
// back to writing its own reader. The delimiter for such a block is whichever
// one the card actually used, since there is no declaration to consult.
//
// Anything that is not a block is walked past a line at a time, which is what
// the reference parser does with a keyword it does not recognise. The one place
// this reads a card differently from that parser is an unknown block whose body
// contains a known one: here the body is consumed whole, so the inner block is
// part of it rather than a block in its own right.
func Scan(src []byte) (*Document, error) {
	doc := &Document{}
	s := &scanner{src: src}
	for {
		s.skipSpaceAndNewlines()
		if s.done() {
			return doc, nil
		}
		switch {
		case s.src[s.pos] == '#':
			s.skipLine()
		case s.src[s.pos] == '@':
			doc.Headers = append(doc.Headers, s.header())
		default:
			word := s.peekWord()
			if word == "" {
				s.skipLine()
				continue
			}
			d, known := vocabulary[word]
			if !known {
				// No declaration to consult, so look at what follows. A word
				// with no delimiter after it is a line, not a block.
				var isBlock bool
				d, isBlock = s.sniffDelim(len(word))
				if !isBlock {
					s.skipLine()
					continue
				}
			}
			s.pos += len(word)
			body, err := s.readBlock(d)
			if err != nil {
				return nil, fmt.Errorf("%s: %w", word, err)
			}
			doc.Blocks = append(doc.Blocks, Block{Name: word, Body: body})
		}
	}
}

type scanner struct {
	src []byte
	pos int
}

func (s *scanner) done() bool { return s.pos >= len(s.src) }

func (s *scanner) skipSpace() {
	for !s.done() && (s.src[s.pos] == ' ' || s.src[s.pos] == '\t') {
		s.pos++
	}
}

func (s *scanner) skipSpaceAndNewlines() {
	for !s.done() {
		switch s.src[s.pos] {
		case ' ', '\t', '\n', '\r':
			s.pos++
		default:
			return
		}
	}
}

func (s *scanner) skipLine() {
	for !s.done() && s.src[s.pos] != '\n' {
		s.pos++
	}
	if !s.done() {
		s.pos++
	}
}

func (s *scanner) readLine() string {
	start := s.pos
	for !s.done() && s.src[s.pos] != '\n' {
		s.pos++
	}
	line := string(s.src[start:s.pos])
	if !s.done() {
		s.pos++
	}
	return line
}

func (s *scanner) peekWord() string {
	i := s.pos
	for i < len(s.src) && (s.src[i] == ' ' || s.src[i] == '\t') {
		i++
	}
	j := i
	for j < len(s.src) && isAlpha(s.src[j]) {
		j++
	}
	return string(s.src[i:j])
}

// sniffDelim looks past a keyword for the delimiter that opens its body,
// without moving the scanner. It is how an undeclared block gets read at all.
func (s *scanner) sniffDelim(offset int) (delim, bool) {
	i := s.pos + offset
	for i < len(s.src) {
		switch s.src[i] {
		case ' ', '\t', '\n', '\r':
			i++
		case '{':
			return braces, true
		case '[':
			return brackets, true
		default:
			return delim{}, false
		}
	}
	return delim{}, false
}

// header reads one @key line.
func (s *scanner) header() Header {
	s.pos++ // @
	start := s.pos
	for !s.done() && s.src[s.pos] != ' ' && s.src[s.pos] != '\t' && s.src[s.pos] != '\n' {
		s.pos++
	}
	key := string(s.src[start:s.pos])
	s.skipSpace()
	return Header{Key: key, Value: strings.TrimSpace(s.readLine())}
}

// readBlock consumes a delimited body, counting nested delimiters so a regex
// carrying a character class survives inside a bracketed block.
func (s *scanner) readBlock(d delim) (string, error) {
	s.skipSpaceAndNewlines()
	if s.done() || s.src[s.pos] != d.open {
		return "", fmt.Errorf("expected %q", string(d.open))
	}
	s.pos++
	depth, start := 1, s.pos
	for !s.done() {
		switch s.src[s.pos] {
		case d.open:
			depth++
		case d.close:
			depth--
			if depth == 0 {
				body := string(s.src[start:s.pos])
				s.pos++
				return body, nil
			}
		}
		s.pos++
	}
	return "", fmt.Errorf("unterminated block, no closing %q", string(d.close))
}

func isAlpha(b byte) bool {
	return (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z')
}
