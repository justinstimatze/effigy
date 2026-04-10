"""Effigy — Dense Character Notation Library.

A sculpted likeness of a person. The notation sculpts a character from
compressed symbols into three layers:

  Layer 1 (compile-time): Notation → JSON matching your character corpus schema.
  Layer 2 (runtime, per-turn): Notation + game state → optimized prompt context.
  Layer 3 (runtime, periodic): Dynamic profile evolution — arc phases,
           emotional axes, intentions, memory synthesis.
"""

__version__ = "0.1.0"

from effigy.notation import CharacterAST, NarrativeRole
from effigy.parser import ParseError, parse

__all__ = [
    "__version__",
    "CharacterAST",
    "NarrativeRole",
    "parse",
    "ParseError",
]
