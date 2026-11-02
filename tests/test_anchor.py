"""Anchors (docs/anchors.md): names, assignment, the table and its file form."""
import copy
import os

import pytest

import zuspec.ir.core as ir
from zuspec.ir.core.anchor import (AnchorTable, assign_anchors, escape, neutral_root,
                                   qualify, scope_hash, split_qualifier, unescape)


def L(line, col=1, file="rle_pkg.sv"):
    return ir.Loc(file=file, line=line, pos=col)


def c(v, line=None, col=1):
    return ir.ExprConstant(value=v, loc=L(line, col) if line else None)


def ref(name, line=None, col=1):
    return ir.ExprRefLocal(name=name, loc=L(line, col) if line else None)


def add(a, b, line=None, col=1):
    return ir.ExprBin(lhs=a, op=ir.BinOp.Add, rhs=b, loc=L(line, col) if line else None)


def assign(name, value, line=None):
    return ir.StmtAssign(targets=[ref(name, line, 1)], value=value,
                         loc=L(line) if line else None)


def component():
    """A small component: two fields and a process with an if/else."""
    run = ir.Function(name="run", loc=L(10), body=[
        assign("x", add(ref("a", 11, 5), c(1, 11, 9), 11, 5), 11),
        ir.StmtIf(test=ref("go", 12, 5), loc=L(12), body=[
            assign("y", c(2, 13, 9), 13),
        ], orelse=[
            assign("y", c(3, 15, 9), 15),
            assign("z", ref("y", 16, 9), 16),
        ]),
    ])
    return ir.DataTypeComponent(
        name="rle_pkg::RunLengthEncoder", super=None, loc=L(1),
        fields=[ir.Field(name="prev_count", datatype=ir.DataTypeInt(bits=2), loc=L(3),
                         initial_value=c(0, 3, 30)),
                ir.Field(name="out_c", datatype=ir.DataTypeInt(bits=8), loc=L(4))],
        proc_processes=[run])


# -- names -------------------------------------------------------------------

@pytest.mark.parametrize("name", ["plain", "a.b", "x#y", "p@q:r", "sp ace", "back\\slash",
                                  "tilde~1", "ünï"])
def test_escape_round_trips(name):
    e = escape(name)
    assert e.isascii()
    assert not any(ch in e for ch in ".#@: ~") or name == e
    assert unescape(e) == name


def test_neutral_root_joins_any_spelling():
    assert neutral_root("rle_pkg::RunLengthEncoder") == "rle_pkg.RunLengthEncoder"
    assert neutral_root("rle_pkg.RunLengthEncoder") == "rle_pkg.RunLengthEncoder"
    assert neutral_root("gcd") == "gcd"


def test_qualifier_round_trips():
    a = qualify("m.T.prev_count", "reg", 2)
    assert a == "m.T.prev_count@reg:2"
    assert split_qualifier(a) == ("m.T.prev_count", ["reg", "2"])
    assert split_qualifier("m.T.x") == ("m.T.x", None)


# -- assignment ----------------------------------------------------------------

def test_component_anchors():
    comp = component()
    t = assign_anchors(comp)
    r = "rle_pkg.RunLengthEncoder"
    run = comp.proc_processes[0]
    if_ = run.body[1]
    assert comp.anchor == r
    assert comp.fields[0].anchor == f"{r}.prev_count"
    assert comp.fields[0].initial_value.anchor == f"{r}.prev_count.e1"
    assert run.anchor == f"{r}.run"
    assert run.body[0].anchor == f"{r}.run#s1"
    # Pre-order: the target, then the add, then its operands.
    s1 = run.body[0]
    assert [s1.targets[0].anchor, s1.value.anchor, s1.value.lhs.anchor, s1.value.rhs.anchor] == \
        [f"{r}.run#s1.e1", f"{r}.run#s1.e2", f"{r}.run#s1.e3", f"{r}.run#s1.e4"]
    assert if_.anchor == f"{r}.run#s2"
    assert if_.test.anchor == f"{r}.run#s2.e1"
    assert if_.body[0].anchor == f"{r}.run#s2.body.s1"
    assert if_.orelse[1].anchor == f"{r}.run#s2.orelse.s2"
    assert if_.orelse[1].value.anchor == f"{r}.run#s2.orelse.s2.e2"
    e = t[f"{r}.run#s2.orelse.s2.e2"]
    assert (e.kind, e.scope) == ("expr", f"{r}.run")
    assert t[r].source_name == "rle_pkg::RunLengthEncoder"
    assert t[f"{r}.prev_count"].kind == "member"


def test_free_function_root_and_match_cases():
    fn = ir.Function(name="gcd_pkg::gcd", body=[
        assign("t", c(0)),
        ir.StmtMatch(subject=ref("op"), cases=[
            ir.StmtMatchCase(pattern=ir.PatternValue(value=c(1)), body=[assign("t", c(5))]),
            ir.StmtMatchCase(pattern=ir.PatternValue(value=c(2)),
                             body=[assign("t", c(6)), assign("u", c(7))]),
        ]),
    ])
    assign_anchors(fn)
    m = fn.body[1]
    assert fn.anchor == "gcd_pkg.gcd"
    assert m.anchor == "gcd_pkg.gcd#s2"
    # The subject and both case patterns are the match statement's expressions.
    assert m.subject.anchor == "gcd_pkg.gcd#s2.e1"
    assert m.cases[1].pattern.value.anchor == "gcd_pkg.gcd#s2.e3"
    assert m.cases[1].body[1].anchor == "gcd_pkg.gcd#s2.cases.2.body.s2"


def test_if_chain_clauses():
    fn = ir.Function(name="f", body=[ir.StmtIfChain(clauses=[
        ir.StmtIfClause(test=ref("a"), body=[assign("x", c(1))]),
        ir.StmtIfClause(test=ref("b"), body=[assign("x", c(2))]),
    ], orelse=[assign("x", c(3))])])
    assign_anchors(fn)
    ch = fn.body[0]
    assert ch.clauses[1].test.anchor == "f#s1.e2"
    assert ch.clauses[1].body[0].anchor == "f#s1.clauses.2.body.s1"
    assert ch.orelse[0].anchor == "f#s1.orelse.s1"


def test_duplicate_member_names_are_numbered():
    comp = ir.DataTypeComponent(name="T", super=None, fields=[
        ir.Field(name="x", datatype=ir.DataTypeInt(bits=1)),
        ir.Field(name="x", datatype=ir.DataTypeInt(bits=1))])
    assign_anchors(comp)
    assert [f.anchor for f in comp.fields] == ["T.x", "T.x~2"]


def test_context_roots_named_by_key():
    ctx = ir.Context(type_m={"pkg::A": ir.DataTypeComponent(name="A", super=None)})
    assign_anchors(ctx)
    assert ctx.type_m["pkg::A"].anchor == "pkg.A"


def test_reassignment_is_idempotent():
    comp = component()
    t1 = assign_anchors(comp)
    t2 = assign_anchors(comp)
    assert t1.to_json() == t2.to_json()


def test_anchors_do_not_change_equality():
    a, b = component(), component()
    assign_anchors(a)
    assert a == b
    assert "anchor" not in repr(a.fields[0])


def test_references_to_declarations_are_not_walked():
    """An expression's reference to a type is anchored where the type is."""
    other = ir.DataTypeComponent(name="Other", super=None,
                                 fields=[ir.Field(name="k", datatype=ir.DataTypeInt(bits=1))])
    fn = ir.Function(name="f", body=[ir.StmtAnnAssign(target=ref("v"), annotation=ref("Other"),
                                                      ir_type=other)])
    t = assign_anchors({"f": fn, "Other": other})
    assert other.fields[0].anchor == "Other.k"
    assert not any(a.startswith("f#s1") and "k" in a for a in t)


# -- the table -----------------------------------------------------------------

def test_by_loc_and_by_line():
    t = assign_anchors(component())
    r = "rle_pkg.RunLengthEncoder"
    key = (os.path.abspath("rle_pkg.sv"), 11, 5)
    # The add and its left operand start at the same column.
    assert t.by_loc(key) == [f"{r}.run#s1.e2", f"{r}.run#s1.e3"]
    assert t.by_line(os.path.abspath("rle_pkg.sv"), 16) == [f"{r}.run#s2.orelse.s2"]
    assert t.by_loc((None, 99, 1)) == []


def test_node_of_and_scope_of():
    comp = component()
    t = assign_anchors(comp)
    r = "rle_pkg.RunLengthEncoder"
    assert t.node_of(f"{r}.out_c") is comp.fields[1]
    assert t.scope_of(f"{r}.run#s1.e3") == f"{r}.run"
    assert t.scope_of(qualify(f"{r}.prev_count", "reg", 2)) == f"{r}.prev_count"


def test_table_json_round_trip(tmp_path):
    t = assign_anchors(component())
    p = tmp_path / "anchors.json"
    t.save(str(p))
    u = AnchorTable.load(str(p))
    assert u.to_json() == t.to_json()
    assert u.node_of("rle_pkg.RunLengthEncoder") is None
    assert u.by_loc((os.path.abspath("rle_pkg.sv"), 13, 9)) == \
        ["rle_pkg.RunLengthEncoder.run#s2.body.s1.e2"]


def test_table_rejects_other_formats():
    with pytest.raises(ValueError):
        AnchorTable.from_json({"format": "zuspec.overlay", "version": 1})
    with pytest.raises(ValueError):
        AnchorTable.from_json({"format": "zuspec.anchor-table", "version": 99})


def test_loc_key_is_absolute():
    assert ir.Loc(file="a/b.sv", line=3, pos=4).key() == (os.path.abspath("a/b.sv"), 3, 4)
    assert ir.Loc(file=None, line=3, pos=4).key() == (None, 3, 4)


def test_anchor_survives_serializer_round_trip():
    comp = component()
    assign_anchors(comp)
    import enum
    layer = enum.Enum("Layer", "STRUCTURAL").STRUCTURAL
    text = ir.IRSerializer().serialize(comp, layer)
    de = ir.IRDeserializer()
    de.auto_register(ir)
    back, _ = de.deserialize(text)
    assert back.fields[0].anchor == "rle_pkg.RunLengthEncoder.prev_count"
