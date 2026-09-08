"""Coefficient memory of a tableau. New module; nothing pinned is modified.

One number for the trade-offs matrix: how many int16 constant words a tableau
would need if every non-trivial coefficient were held in a table.

The definition has to be stated because two defensible ones exist and the
column is meaningless without saying which is published:

* The counted entries are the entries of ``A`` and ``b``, ``c`` excluded, the
  same set ``coeffrep.tableau_csd_total`` sums over, so the memory column and
  the CSD-weight column stand on the same footing.
* An entry counts when ``coeffrep.is_trivial`` is false, that is when it is
  not 0, 1 or -1. Those three need no stored constant under any emitter: zero
  drops the term, plus or minus one is an add or a subtract.
* Each counted entry costs one word, one int16 mantissa ``m`` from
  ``coeffrep.to_rep``. The shift ``s`` is reported separately as
  ``max_shift`` rather than charged as memory, because a shift is an immediate
  in the instruction, not a stored word.

What the number is NOT: a measurement of the reference emitter. The reference
C from ``costmodel.emit_c`` inlines both the mantissa and the shift as
literals in each expression, so under that emitter these constants are code
immediates and there is no constant table at all. The published quantity is an
upper bound on a table an implementation might choose to build, and the
findings and overview pages have to say so where they print it.

Kept out of ``benchmark.py`` deliberately: importing that module sets five
BLAS thread environment variables and hard-imports numpy, which the overview
generator has no business inheriting just to print a column. This module
imports ``fractions``, ``rk_harness.coeffrep`` and ``rk_harness.types`` and
nothing else. It calls the pinned ``coeffrep``; it never edits it.
"""
from __future__ import annotations

from fractions import Fraction

from rk_harness import coeffrep
from rk_harness.types import Tableau


def coefficient_memory(t: Tableau) -> dict:
    """Constant-word cost of a tableau under the definition above.

    Returns ``{'words', 'bytes', 'max_shift', 'entries', 'trivial'}``:

    * ``entries``: entries of A and b, c excluded, trivial ones included,
    * ``trivial``: how many of those are 0, 1 or -1 and cost nothing,
    * ``words``: the rest, one int16 mantissa each,
    * ``bytes``: ``2 * words``,
    * ``max_shift``: the largest ``CoeffRep.s`` over the counted entries, 0
      when there are none (euler has none, so the empty case is real).
    """
    entries = 0
    words = 0
    max_shift = 0
    # coeffrep._ab_entries is private but pinned: the file is one of the ten
    # verifier-hashed sources, so its iteration order cannot drift out from
    # under this column.
    for x in coeffrep._ab_entries(t):
        entries += 1
        if coeffrep.is_trivial(Fraction(x)):
            continue
        words += 1
        s = coeffrep.to_rep(Fraction(x)).s
        if s > max_shift:
            max_shift = s
    return {
        "words": words,
        "bytes": 2 * words,
        "max_shift": max_shift,
        "entries": entries,
        "trivial": entries - words,
    }
