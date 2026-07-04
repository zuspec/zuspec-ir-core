"""CoroutineFSMPass — structured-concurrency → state-machine lowering.

A generic, target-neutral transform (the same shape as ``unified-lowering``'s
``ProcessToFSMPass``): it splits a :class:`~..scenario.ScCoroutine` body at
**suspend points** into numbered blocks, so a stackless backend (C's
``zsp_timebase`` switch-on-``idx``) can resume mid-coroutine.  SV skips this pass
(the simulator is the scheduler); C and formal run it.

A *suspend point* is an explicit op that may yield to the scheduler:
:class:`ScWait`, :class:`ScJoin`, or a :class:`ScInvoke` whose target is known
to block.  Everything between suspend points is straight-line/structured and
executes within one ``idx`` block.

Phase-4 status: this pass produces the **block decomposition** (and the
frame-locals list) and is golden-tested; the C rendering of multi-block FSMs
lands in Phase 5 alongside ``ScWait``/``ScPar``.  A coroutine with no suspend
points decomposes to a single block → a plain C function (the no-suspend path),
which is what Phase-4 sequential/loop/conditional activities of atomic actions
produce.

Nested control flow (``ScLoop`` / ``ScIf`` / ``ScMatch``) that *contains* a
suspend point requires loop/branch-aware FSM lowering and is deferred (Phase 5);
the pass raises clearly rather than mis-lowering it.
"""
from __future__ import annotations

import dataclasses as dc
from typing import List, Optional

from ..scenario import (
    ScStmt, ScCoroutine, ScSeq, ScAtomic, ScLoop, ScIf, ScMatch,
    ScWait, ScJoin, ScInvoke, ScPar, ScSelect, ScImport,
)
from .validate import UnsupportedConstructError


@dc.dataclass
class FSMBlock:
    """One ``idx`` block: straight-line ``stmts`` ending at an optional
    ``suspend`` op (``None`` for the final block)."""
    idx: int
    stmts: List[ScStmt]
    suspend: Optional[ScStmt] = None


@dc.dataclass
class FSMForm:
    """Result of the pass: the ordered blocks + persistent frame locals."""
    blocks: List[FSMBlock]
    frame_locals: List[str] = dc.field(default_factory=list)

    @property
    def n_blocks(self) -> int:
        return len(self.blocks)

    @property
    def suspends(self) -> bool:
        return self.n_blocks > 1


class CoroutineFSMPass:
    """Decompose a coroutine into FSM blocks at suspend points."""

    # A ScPar suspends the parent: it spawns branches and blocks until the join
    # condition, resuming in the next block. A ScSelect likewise suspends -- it
    # runs the chosen branch as a blocking child and resumes when it returns.
    _SUSPEND_TYPES = (ScWait, ScJoin, ScPar, ScSelect)

    def __init__(self, blocking_targets=None, allow_nested_suspend=False):
        # Names of sub-coroutines that block (so an ScInvoke of them suspends).
        self.blocking = set(blocking_targets or [])
        # When True, a loop/branch that *contains* a suspend is kept opaque (a
        # single structured stmt) instead of being rejected. A consumer that lowers
        # to a flat, resumable code stream (the be-bc oracle, whose VM saves the pc
        # across a suspend) can execute the back-edge + interior suspend directly;
        # a stackless switch backend still needs the Phase-5 CFG split, so this
        # stays off by default.
        self.allow_nested_suspend = allow_nested_suspend

    # ------------------------------------------------------------------
    def is_suspend(self, s: ScStmt) -> bool:
        if isinstance(s, self._SUSPEND_TYPES):
            return True
        if isinstance(s, ScImport) and s.blocking:
            return True
        if isinstance(s, ScInvoke) and s.target in self.blocking:
            return True
        return False

    def run(self, coro: ScCoroutine) -> FSMForm:
        flat = self._flatten(coro.body)
        blocks: List[FSMBlock] = []
        cur: List[ScStmt] = []
        idx = 0
        for s in flat:
            if self.is_suspend(s):
                blocks.append(FSMBlock(idx=idx, stmts=cur, suspend=s))
                idx += 1
                cur = []
            else:
                cur.append(s)
        blocks.append(FSMBlock(idx=idx, stmts=cur, suspend=None))
        return FSMForm(blocks=blocks, frame_locals=self._frame_locals(coro, blocks))

    # ------------------------------------------------------------------
    def _flatten(self, stmts: List[ScStmt]) -> List[ScStmt]:
        """Inline sequence/atomic wrappers; keep control-flow ops opaque unless
        they contain a suspend (which is Phase-5 territory)."""
        out: List[ScStmt] = []
        for s in stmts:
            if isinstance(s, (ScSeq, ScAtomic)):
                out.extend(self._flatten(s.body))
            elif isinstance(s, (ScLoop, ScIf, ScMatch)):
                if self._contains_suspend(s) and not self.allow_nested_suspend:
                    raise UnsupportedConstructError(
                        "suspend point inside %s requires loop/branch-aware FSM "
                        "lowering (Phase 5)" % type(s).__name__,
                        loc=getattr(s, "loc", None))
                out.append(s)
            else:
                out.append(s)
        return out

    def _contains_suspend(self, s: ScStmt) -> bool:
        if self.is_suspend(s):
            return True
        for attr in ("body", "then_body", "else_body"):
            sub = getattr(s, attr, None)
            if isinstance(sub, list) and any(self._contains_suspend(x) for x in sub):
                return True
        if isinstance(s, ScMatch):
            return any(self._contains_suspend(x)
                       for case in s.cases for x in case.body)
        return False

    def _frame_locals(self, coro: ScCoroutine, blocks) -> List[str]:
        # A loop index variable whose loop spans a suspend must persist across
        # idx blocks.  In Phase 4 nothing spans a suspend, so this is empty; the
        # mechanism is here for Phase 5.
        if len(blocks) <= 1:
            return list(coro.frame_locals)
        locals_: List[str] = list(coro.frame_locals)
        for blk in blocks:
            for s in blk.stmts:
                if isinstance(s, ScLoop) and s.index_var:
                    locals_.append(s.index_var)
        return locals_
