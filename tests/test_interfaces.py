"""Tests for zuspec.ir.core interface protocols (Phase 1)."""

import pytest
from zuspec.ir.core.interfaces import (
    Lowerable,
    ElaboratableInterface,
    SVEmittableInterface,
    SVAEmittableInterface,
    CSimEmittableInterface,
)


# ---------------------------------------------------------------------------
# Lowerable
# ---------------------------------------------------------------------------

class _IsLowerable(Lowerable):
    pass


def test_lowerable_protocol_is_satisfied_by_subclass():
    obj = _IsLowerable()
    assert isinstance(obj, Lowerable)


# ---------------------------------------------------------------------------
# ElaboratableInterface
# ---------------------------------------------------------------------------

class _GoodElaboratable:
    @classmethod
    def elaborate_field(cls, field_name, field_index, inst_kwargs, element_type=None):
        return None


class _BadElaboratable:
    pass


def test_elaboratable_interface_protocol_check():
    assert isinstance(_GoodElaboratable, ElaboratableInterface)


def test_elaboratable_interface_missing_method_fails():
    assert not isinstance(_BadElaboratable, ElaboratableInterface)


# ---------------------------------------------------------------------------
# SVEmittableInterface
# ---------------------------------------------------------------------------

class _GoodSV:
    @classmethod
    def sv_module_text(cls, field_ir):
        return ""

    @classmethod
    def sv_instance_text(cls, field_ir, parent_prefix):
        return ""

    @classmethod
    def rewrite_proc_stmts(cls, stmts, field_ir):
        return stmts


class _MissingSVMethod:
    @classmethod
    def sv_module_text(cls, field_ir):
        return ""

    # missing sv_instance_text and rewrite_proc_stmts


def test_sv_emittable_protocol_check():
    assert isinstance(_GoodSV, SVEmittableInterface)


def test_sv_emittable_missing_method_fails():
    assert not isinstance(_MissingSVMethod, SVEmittableInterface)


# ---------------------------------------------------------------------------
# SVAEmittableInterface
# ---------------------------------------------------------------------------

class _GoodSVA:
    @classmethod
    def sva_assert_properties(cls, field_ir):
        return []

    @classmethod
    def sva_assume_properties(cls, field_ir):
        return []

    @classmethod
    def bmc_depth(cls, field_ir):
        return 0

    @classmethod
    def cutpoint_signals(cls, field_ir):
        return []


class _IncompleteSVA:
    @classmethod
    def sva_assert_properties(cls, field_ir):
        return []
    # missing the other three methods


def test_sva_emittable_protocol_check():
    assert isinstance(_GoodSVA, SVAEmittableInterface)


def test_sva_emittable_incomplete_fails():
    assert not isinstance(_IncompleteSVA, SVAEmittableInterface)


# ---------------------------------------------------------------------------
# CSimEmittableInterface
# ---------------------------------------------------------------------------

class _GoodCSim:
    @classmethod
    def c_header(cls, field_ir):
        return ""

    @classmethod
    def c_impl(cls, field_ir):
        return ""


class _IncompleteCSim:
    @classmethod
    def c_header(cls, field_ir):
        return ""
    # missing c_impl


def test_csim_emittable_protocol_check():
    assert isinstance(_GoodCSim, CSimEmittableInterface)


def test_csim_emittable_incomplete_fails():
    assert not isinstance(_IncompleteCSim, CSimEmittableInterface)


# ---------------------------------------------------------------------------
# Incomplete class fails a composite check
# ---------------------------------------------------------------------------

def test_incomplete_class_fails_protocol_check():
    """A class missing one method from SVEmittableInterface must fail."""

    class AlmostSV:
        @classmethod
        def sv_module_text(cls, field_ir):
            return ""

        @classmethod
        def sv_instance_text(cls, field_ir, parent_prefix):
            return ""
        # intentionally omits rewrite_proc_stmts

    assert not isinstance(AlmostSV, SVEmittableInterface)
