"""An integer literal's type comes from its spelling (PSS 4.6.1, Table 21)."""
import pytest

from zuspec.ir.core import ExprConstant, int_literal_type


@pytest.mark.parametrize("c, expect", [
    (ExprConstant(value=16), (32, True)),                    # 16
    (ExprConstant(value=16, signed=False), (32, False)),     # 0x10, 0b10000
    (ExprConstant(value=(1 << 40) - 1, signed=False), (40, False)),
    (ExprConstant(value=1 << 31), (33, True)),               # 2147483648
    (ExprConstant(value=-(1 << 31)), (32, True)),
    (ExprConstant(value=255, width=8, signed=False), (8, False)),   # 8'hFF
    (ExprConstant(value=-6, width=4, signed=True), (4, True)),      # 4'sb1010
    (ExprConstant(value=5, signed=True), (32, True)),               # 'sd5
    (ExprConstant(value=(1 << 64) - 1), (64, False)),        # fits only unsigned
])
def test_int_literal_type(c, expect):
    assert int_literal_type(c) == expect


def test_the_type_survives_a_round_trip():
    import dataclasses as dc
    c = ExprConstant(value=255, width=8, signed=False)
    assert dc.replace(c) == c and c != ExprConstant(value=255)
