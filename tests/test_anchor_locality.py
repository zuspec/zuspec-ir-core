"""Anchors re-key only what an edit touches (docs/anchors.md §2.3, §5)."""
import copy

import zuspec.ir.core as ir
from zuspec.ir.core.anchor import assign_anchors

from test_anchor import L, add, assign, c, component, ref

R = "rle_pkg.RunLengthEncoder"


def _second_process(comp):
    comp.proc_processes.append(ir.Function(name="drain", body=[assign("q", c(9))]))
    return comp


def test_edit_in_one_process_leaves_others_valid():
    a = _second_process(component())
    b = _second_process(component())
    b.proc_processes[0].body[0].value.rhs.value = 2          # edit `run` only
    ta, tb = assign_anchors(a), assign_anchors(b)
    assert set(ta) == set(tb)                                 # nothing re-keyed
    assert ta.scope_hashes[f"{R}.run"] != tb.scope_hashes[f"{R}.run"]
    assert ta.scope_hashes[f"{R}.drain"] == tb.scope_hashes[f"{R}.drain"]
    assert ta.scope_hashes[f"{R}.prev_count"] == tb.scope_hashes[f"{R}.prev_count"]


def test_insert_rekeys_only_later_siblings():
    a, b = component(), component()
    b.proc_processes[0].body[1].orelse.insert(0, assign("w", c(4)))
    ta, tb = assign_anchors(a), assign_anchors(b)
    unchanged = [x for x in ta if not x.startswith(f"{R}.run#s2.orelse")]
    assert all(x in tb for x in unchanged)
    assert f"{R}.run#s2.orelse.s3" in tb and f"{R}.run#s2.orelse.s3" not in ta


def test_moving_code_or_editing_docs_keeps_hashes():
    a, b = component(), component()
    for s in b.proc_processes[0].body:
        s.loc = L(s.loc.line + 40)                           # moved down 40 lines
    b.proc_processes[0].doc = "Encode runs."
    ta, tb = assign_anchors(a), assign_anchors(b)
    assert ta.scope_hashes == tb.scope_hashes


def test_field_type_change_changes_its_hash():
    a, b = component(), component()
    b.fields[0].datatype = ir.DataTypeInt(bits=3)
    ta, tb = assign_anchors(a), assign_anchors(b)
    assert ta.scope_hashes[f"{R}.prev_count"] != tb.scope_hashes[f"{R}.prev_count"]
    assert ta.scope_hashes[f"{R}.run"] == tb.scope_hashes[f"{R}.run"]
    assert ta.scope_hashes[R] != tb.scope_hashes[R]           # the type as a whole changed
