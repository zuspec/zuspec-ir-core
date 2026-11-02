"""Named locals (P2) and synthesized nodes (P1): docs/anchors.md §2.4-2.5."""
import zuspec.ir.core as ir
from zuspec.ir.core.anchor import assign_anchors, mark_synthesized, synthesized_reason


def c(v):
    return ir.ExprConstant(value=v)


def ref(n):
    return ir.ExprRefLocal(name=n)


def decl(n, v=None, ann=None):
    return ir.StmtAnnAssign(target=ref(n), annotation=ann or c("bit [7:0]"), value=v)


def assign(n, v):
    return ir.StmtAssign(targets=[ref(n)], value=v)


def fn(body, name="f"):
    return ir.Function(name=name, body=body)


def anchors(f):
    assign_anchors(f)
    return [s.anchor for s in f.body]


def test_declarations_name_their_statements():
    f = fn([assign("a", c(1)), decl("t", c(2)), assign("a", ref("t")), assign("b", c(3)),
            decl("u"), assign("b", ref("u"))])
    assert anchors(f) == ["f#s1", "f#v:t", "f#v:t+1", "f#v:t+2", "f#v:u", "f#v:u+1"]


def test_head_insert_rekeys_only_up_to_the_next_declaration():
    body = [assign("a", c(1)), decl("t", c(2)), assign("a", ref("t"))]
    before = anchors(fn(body))
    after = anchors(fn([assign("z", c(0))] + body))
    assert before[1:] == after[2:]          # v:t and v:t+1 are unchanged
    assert after[:2] == ["f#s1", "f#s2"]


def test_repeated_declaration_names_are_numbered():
    f = fn([decl("t"), assign("a", c(1)), decl("t"), assign("a", c(2))])
    assert anchors(f) == ["f#v:t", "f#v:t+1", "f#v:t~2", "f#v:t~2+1"]


def test_type_annotations_get_no_anchors():
    ann = ir.ExprAttribute(value=ir.ExprRefUnresolved(name="zdc"), attr="u8")
    f = fn([decl("t", c(2), ann=ann)])
    t = assign_anchors(f)
    assert ann.anchor is None
    assert [f.body[0].target.anchor, f.body[0].value.anchor] == ["f#v:t.e1", "f#v:t.e2"]
    assert not any(a.endswith(".e3") for a in t)


def test_synthesized_statements_take_no_ordinal():
    ret = mark_synthesized(decl("f"), "retvar")
    user = [assign("a", c(1)), assign("b", c(2))]
    f = fn([ret] + user + [mark_synthesized(ir.StmtReturn(value=ref("f")), "return")])
    assert anchors(f) == ["f#x:retvar", "f#s1", "f#s2", "f#x:return"]
    # The same user statements without the synthesized ones: the same anchors.
    g = fn([assign("a", c(1)), assign("b", c(2))])
    assert anchors(g) == ["f#s1", "f#s2"]


def test_pass_is_a_placeholder():
    f = fn([ir.StmtIf(test=ref("p"), body=[ir.StmtPass()],
                      orelse=[assign("a", c(1))])])
    assign_anchors(f)
    assert synthesized_reason(f.body[0].body[0]) == "pass"
    assert f.body[0].body[0].anchor == "f#s1.body.x:pass"
    assert f.body[0].orelse[0].anchor == "f#s1.orelse.s1"


def test_synthesized_cast_anchors_on_what_it_wraps():
    def body(cast):
        lit = c(1)
        rhs = mark_synthesized(ir.ExprCast(target_type=ir.DataTypeInt(bits=8), value=lit),
                               "cast") if cast else lit
        return [assign("a", ir.ExprBin(lhs=ref("b"), op=ir.BinOp.Add, rhs=rhs))], lit, rhs
    b1, lit1, _ = body(False)
    b2, lit2, cast2 = body(True)
    assign_anchors(fn(b1))
    assign_anchors(fn(b2))
    assert lit1.anchor == lit2.anchor == "f#s1.e4"
    assert cast2.anchor == "f#s1.e4.x:cast"
    # Synthesized with no user expression under it: on the owner.
    loop = ir.StmtWhile(test=mark_synthesized(c(True), "forever"), body=[assign("a", c(0))])
    assign_anchors(fn([loop]))
    assert loop.test.anchor == "f#s1.x:forever"
