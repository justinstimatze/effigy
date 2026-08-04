// Package notation reads effigy notation and stops where structure becomes
// schema.
//
// It answers the questions the notation itself defines: which keywords open a
// block, which delimiters close one, what an @header line is, and how a block
// body is cut into items and key/value pairs. It answers almost nothing about
// what any of it means. VOICE has a kernel and a peak because effigy's
// character schema says so, not because the notation does, so that reading
// lives above this package.
//
// MES is the one exception and it earns it. Whether an item is an exchange or a
// single utterance changes how the notation renders it, so the rule is the
// notation's rather than a consumer's — and every consumer that reimplemented
// it got a chance to disagree with the others about what a card teaches.
//
// The split is what it is because the reading half is shared and the meaning
// half is not. cope turns a card into gate rules, drag wants one block's text to
// show a judge, and effigy's own CharacterAST is a third shape again. All three
// need the same scanner and none of them need each other's types.
//
// The block vocabulary lives here rather than in a consumer for the same
// reason: it is a fact about the notation, it is declared in effigy/notation.py,
// and a copy of it in a downstream repo is a copy that goes stale the next time
// effigy defines a block. blocks_test.go reads that declaration and fails when
// the two disagree.
package notation
