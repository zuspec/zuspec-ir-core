"""collect_solve_problem records each rand var's object slot (full-field index).

When non-rand fields are interleaved with rand fields, a rand var's solver
``var_id`` (rand-only enumeration) differs from its object storage ``slot`` (its
index in the full field list, which constraints/procedural code address). The
frontend must capture the slot so a backend can map solved var_id -> object slot.
"""

from zuspec.ir.core.data_type import DataTypeClass, DataTypeInt
from zuspec.ir.core.fields import Field, RandKind
from zuspec.ir.core.scenario import ScCoroutine
from zuspec.ir.core.xf.pss_lower.constraints import collect_solve_problem


def _field(name, rand):
    return Field(name=name, datatype=DataTypeInt(bits=8),
                 rand_kind=RandKind.RAND if rand else None)


def test_slot_is_full_field_index_with_interleaved_nonrand():
    # fields: [pad (non-rand), x (rand), y (rand), tag (non-rand), z (rand)]
    dt = DataTypeClass(name="A", super=None, fields=[
        _field("pad", False), _field("x", True), _field("y", True),
        _field("tag", False), _field("z", True)])
    coro = ScCoroutine(name="A", body=[], pending_constraints=[])

    p = collect_solve_problem(coro, dt)

    # var_id is the rand-only enumeration; slot is the full-field index.
    by_name = {v.name: v for v in p.vars}
    assert (by_name["x"].var_id, by_name["x"].slot) == (0, 1)
    assert (by_name["y"].var_id, by_name["y"].slot) == (1, 2)
    assert (by_name["z"].var_id, by_name["z"].slot) == (2, 4)
    assert p.writeback == {"x": 0, "y": 1, "z": 2}


def test_slot_equals_var_id_when_all_fields_rand():
    dt = DataTypeClass(name="B", super=None,
                       fields=[_field("a", True), _field("b", True)])
    p = collect_solve_problem(ScCoroutine(name="B", body=[], pending_constraints=[]), dt)
    assert [(v.var_id, v.slot) for v in p.vars] == [(0, 0), (1, 1)]
