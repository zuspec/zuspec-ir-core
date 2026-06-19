"""Pretty-printer for the Layer-1 ``scenario`` dialect.

Produces a compact, stable textual rendering of a :class:`ScenarioModule` — for
debugging and for golden-IR regression tests (so lowering changes are caught
without compiling).
"""
from __future__ import annotations

from typing import List

from ..scenario import (
    ScCoroutine, ScExecBlock, ScSeq, ScAtomic, ScInvoke, ScSpawn, ScJoin,
    ScWait, ScLoop, ScIf, ScMatch, ScSelect, ScPar, ScSolveProblem, ScImport,
    ScenarioModule,
)


def dump_module(module: ScenarioModule) -> str:
    out: List[str] = ["module"]
    if module.root is not None:
        out.append("  root %s : %s" % (module.root.name, module.root.type_name))
    out.append("  exports: %s" % ", ".join(module.export_actions))
    for name in module.coroutines:
        out.extend(_dump_coro(module.coroutines[name]))
    return "\n".join(out)


def _dump_coro(coro: ScCoroutine) -> List[str]:
    out = ["  coroutine %s%s {" % (
        coro.name, " <%s>" % coro.action_type if coro.action_type else "")]
    out.extend(_dump_stmts(coro.body, 2))
    out.append("  }")
    return out


def _dump_stmts(stmts, depth) -> List[str]:
    pad = "  " * depth
    out: List[str] = []
    for s in stmts:
        if isinstance(s, ScSolveProblem):
            vars_ = ", ".join("%s:u%d[%d]" % (v.name, v.width, v.var_id)
                              for v in s.vars)
            out.append("%ssolve_problem vars=[%s] n_constraints=%d"
                       % (pad, vars_, len(s.constraints)))
        elif isinstance(s, ScExecBlock):
            out.append("%sexec(%s, %d stmts)" % (pad, s.kind, len(s.stmts)))
        elif isinstance(s, ScInvoke):
            out.append("%sinvoke %s" % (pad, s.target))
        elif isinstance(s, ScSpawn):
            out.append("%sspawn %s" % (pad, s.target))
        elif isinstance(s, ScJoin):
            out.append("%sjoin" % pad)
        elif isinstance(s, ScWait):
            out.append("%swait" % pad)
        elif isinstance(s, ScImport):
            out.append("%simport %s(%d args)%s" % (
                pad, s.fn, len(s.args), "" if s.blocking else " [solve]"))
        elif isinstance(s, ScSeq):
            out.append("%sseq {" % pad)
            out.extend(_dump_stmts(s.body, depth + 1))
            out.append("%s}" % pad)
        elif isinstance(s, ScAtomic):
            out.append("%satomic {" % pad)
            out.extend(_dump_stmts(s.body, depth + 1))
            out.append("%s}" % pad)
        elif isinstance(s, ScLoop):
            out.append("%sloop(%s) {" % (pad, s.kind))
            out.extend(_dump_stmts(s.body, depth + 1))
            out.append("%s}" % pad)
        elif isinstance(s, ScIf):
            out.append("%sif {" % pad)
            out.extend(_dump_stmts(s.then_body, depth + 1))
            if s.else_body:
                out.append("%s} else {" % pad)
                out.extend(_dump_stmts(s.else_body, depth + 1))
            out.append("%s}" % pad)
        elif isinstance(s, ScMatch):
            out.append("%smatch {" % pad)
            for c in s.cases:
                out.append("%s  case:" % pad)
                out.extend(_dump_stmts(c.body, depth + 2))
            out.append("%s}" % pad)
        elif isinstance(s, ScSelect):
            out.append("%sselect {" % pad)
            for b in s.branches:
                out.append("%s  branch:" % pad)
                out.extend(_dump_stmts(b.body, depth + 2))
            out.append("%s}" % pad)
        elif isinstance(s, ScPar):
            kind = s.join_spec.kind.name if s.join_spec else "ALL"
            out.append("%spar(join=%s) {" % (pad, kind))
            out.extend(_dump_stmts(s.branches, depth + 1))
            out.append("%s}" % pad)
        else:
            out.append("%s<%s>" % (pad, type(s).__name__))
    return out
