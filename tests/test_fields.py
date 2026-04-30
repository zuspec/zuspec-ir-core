"""T1-1: IR unit tests for FieldKind Lock/Share and bounded Pool capacity."""
import pytest
from zuspec.ir.core.fields import FieldKind, Pool, Field
from zuspec.ir.core.data_type import DataTypeInt


def test_fieldkind_lock_importable():
    """FieldKind.Lock is importable and distinct from other kinds."""
    assert FieldKind.Lock is not None
    assert FieldKind.Lock != FieldKind.Input
    assert FieldKind.Lock != FieldKind.Output
    assert FieldKind.Lock != FieldKind.Share


def test_fieldkind_share_importable():
    """FieldKind.Share is importable and distinct."""
    assert FieldKind.Share is not None
    assert FieldKind.Share != FieldKind.Lock


def test_field_with_lock_kind():
    """Field can be constructed with FieldKind.Lock."""
    dt = DataTypeInt(name="int", bits=32, signed=True)
    f = Field(name="ch", datatype=dt, kind=FieldKind.Lock)
    assert f.kind == FieldKind.Lock
    assert f.name == "ch"


def test_field_with_share_kind():
    """Field can be constructed with FieldKind.Share."""
    dt = DataTypeInt(name="int", bits=32, signed=True)
    f = Field(name="ch", datatype=dt, kind=FieldKind.Share)
    assert f.kind == FieldKind.Share


def test_pool_bounded_capacity():
    """Pool accepts and stores a bounded capacity."""
    pool = Pool(name="p", element_type_name="r", capacity=4)
    assert pool.capacity == 4
    assert pool.name == "p"
    assert pool.element_type_name == "r"


def test_pool_unbounded_capacity_default():
    """Pool defaults to None (unbounded) capacity."""
    pool = Pool(name="p2", element_type_name="r")
    assert pool.capacity is None
