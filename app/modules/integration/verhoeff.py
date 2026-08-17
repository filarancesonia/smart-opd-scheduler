"""Verhoeff checksum — the check digit used by ABHA and Aadhaar numbers.

Validating locally means a mistyped 14-digit number is caught at the counter,
in front of the patient, instead of after a round trip to a government gateway
that may be down. The algorithm is a dihedral-group checksum: unlike a simple
modulus it catches all single-digit errors and all adjacent transpositions,
which are exactly the mistakes people make copying numbers off a card.
"""

from __future__ import annotations

# Multiplication table for the dihedral group D5.
_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

# Permutation table.
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)

# Inverse of D.
_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def digits_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def is_valid(number: str) -> bool:
    """True if ``number`` carries a correct Verhoeff check digit."""
    digits = digits_only(number)
    if not digits:
        return False

    check = 0
    for index, digit in enumerate(reversed(digits)):
        check = _D[check][_P[index % 8][int(digit)]]
    return check == 0


def check_digit(payload: str) -> str:
    """The digit that would make ``payload`` a valid Verhoeff number."""
    digits = digits_only(payload)
    check = 0
    for index, digit in enumerate(reversed(digits)):
        check = _D[check][_P[(index + 1) % 8][int(digit)]]
    return str(_INV[check])


def append_check_digit(payload: str) -> str:
    digits = digits_only(payload)
    return digits + check_digit(digits)
