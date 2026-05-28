"""Tests for AbstractionFieldIR (Phase 1)."""

import dataclasses
import pytest
from zuspec.ir.core.abstraction_field_ir import AbstractionFieldIR


def _make(**overrides):
    defaults = dict(
        spec_type_name="Counter",
        field_name="cnt",
        field_index=0,
        py_cls=int,   # any type is valid
        inst_kwargs={"WIDTH": 8},
        ir_node=None,
    )
    defaults.update(overrides)
    return AbstractionFieldIR(**defaults)


def test_construction_stores_fields():
    ir = _make()
    assert ir.spec_type_name == "Counter"
    assert ir.field_name == "cnt"
    assert ir.field_index == 0
    assert ir.py_cls is int
    assert ir.inst_kwargs == {"WIDTH": 8}
    assert ir.ir_node is None


def test_is_abstraction_field_is_true():
    ir = _make()
    assert ir.is_abstraction_field is True


def test_ir_node_accepts_any_type():
    """ir_node should accept int, string, and a dataclass without complaint."""

    @dataclasses.dataclass
    class Payload:
        width: int

    for node in (42, "hello", Payload(width=16)):
        ir = _make(ir_node=node)
        assert ir.ir_node is node


def test_field_index_stored_correctly():
    ir = _make(field_index=3)
    assert ir.field_index == 3


def test_inst_kwargs_empty_is_valid():
    ir = _make(inst_kwargs={})
    assert ir.inst_kwargs == {}
