"""Tests for copy_loc() and merge_loc() helpers (R2)."""
import pytest
from zuspec.ir.core.base import Base, Loc, merge_loc
import dataclasses as dc


@dc.dataclass(kw_only=True)
class _SimpleNode(Base):
    value: int = 0


def _loc(line: int = 1) -> Loc:
    return Loc(file="test.py", line=line, pos=0)


class TestCopyLoc:
    def test_copy_loc_copies_and_returns_self(self):
        src = _SimpleNode(value=1, loc=_loc(10))
        dst = _SimpleNode(value=2)
        result = dst.copy_loc(src)
        assert result is dst, "copy_loc should return self for fluent chaining"
        assert dst.loc is src.loc

    def test_copy_loc_none_src_leaves_dst_unchanged(self):
        src = _SimpleNode(value=1)  # loc=None
        dst = _SimpleNode(value=2, loc=_loc(99))
        dst.copy_loc(src)
        # dst.loc should be unchanged since src.loc is None.
        assert dst.loc is not None
        assert dst.loc.line == 99

    def test_copy_loc_none_src_on_none_dst_stays_none(self):
        src = _SimpleNode(value=1)  # loc=None
        dst = _SimpleNode(value=2)   # loc=None
        dst.copy_loc(src)
        assert dst.loc is None

    def test_copy_loc_overwrites_existing_dst_loc(self):
        src = _SimpleNode(value=1, loc=_loc(42))
        dst = _SimpleNode(value=2, loc=_loc(1))
        dst.copy_loc(src)
        assert dst.loc.line == 42

    def test_copy_loc_fluent_chain(self):
        src = _SimpleNode(value=1, loc=_loc(7))
        dst = _SimpleNode(value=2).copy_loc(src)
        assert dst.loc.line == 7


class TestMergeLoc:
    def test_merge_loc_returns_first_non_none(self):
        a = _SimpleNode(loc=None)
        b = _SimpleNode(loc=_loc(5))
        c = _SimpleNode(loc=_loc(10))
        result = merge_loc([a, b, c])
        assert result is not None
        assert result.line == 5

    def test_merge_loc_all_none_returns_none(self):
        nodes = [_SimpleNode(), _SimpleNode()]
        result = merge_loc(nodes)
        assert result is None

    def test_merge_loc_single_non_none(self):
        n = _SimpleNode(loc=_loc(99))
        result = merge_loc([n])
        assert result.line == 99

    def test_merge_loc_empty_list_returns_none(self):
        assert merge_loc([]) is None
