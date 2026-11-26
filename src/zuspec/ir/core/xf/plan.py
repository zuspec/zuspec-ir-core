"""Planner v0, seed-time ordering of ``schedule`` members (M8b2, LRM 13.3.5).

A ``schedule`` runs its members in any order the flow objects they exchange
allow. This module finds that order statically, from the action types each
member traverses:

- a member that outputs a buffer of type ``T`` runs before one that inputs a
  ``T`` (LRM 14.4: a buffer is consumed after its producer completes);
- a member that outputs a state of type ``T`` does not overlap another member
  that inputs or outputs a ``T`` (LRM 14.5); either order is legal.

- a member that locks a resource of type ``R`` does not overlap another member
  that locks or shares an ``R`` (LRM 15.1); either order is legal. This
  serializes even when the pool has room for both, which is always legal.

Members tied by any of these relations form a *group*, run as one sequence in one of
the group's legal orders (its linear extensions), picked by the run's seed;
groups, and members with no ties, run in parallel. Matching is by object
type only, so ``schedule { do P; do P; do C; }`` runs C after both P's.
Scheduling constraints (LRM 16.2) add to these: ``sequence {a, b}`` is an
edge a -> b (so a and b form one group); ``parallel {a, b}`` needs a and b to
start together, which v0 gives only to members that run alone. A stream
output of one member and a stream input of the same type of another member
(LRM 13.2 c, d) need them to start together too (M8b4).
"""
from __future__ import annotations

import dataclasses as dc
import itertools
import random
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

#: the most orders a group is lowered to; above it, a seeded sample of them
MAX_ORDERS = 24


@dc.dataclass
class Footprint:
    """What one member reads and writes: object types (hashable keys) by kind."""
    buf_out: Set[Any] = dc.field(default_factory=set)
    buf_in: Set[Any] = dc.field(default_factory=set)
    state_out: Set[Any] = dc.field(default_factory=set)
    state_in: Set[Any] = dc.field(default_factory=set)
    res_lock: Set[Any] = dc.field(default_factory=set)
    res_share: Set[Any] = dc.field(default_factory=set)
    stream_out: Set[Any] = dc.field(default_factory=set)
    stream_in: Set[Any] = dc.field(default_factory=set)


@dc.dataclass
class SchedulePlan:
    """The members' order: *edges* ``(i, j)`` (i before j), *groups* of
    member indices that run as one sequence (singletons run alone), and each
    group's legal *orders*."""
    edges: Set[Tuple[int, int]]
    groups: List[List[int]]
    orders: List[List[List[int]]]

    @property
    def interacts(self) -> bool:
        return any(len(g) > 1 for g in self.groups)


class CycleError(Exception):
    """The members' ``before`` relation has a cycle: no legal order."""

    def __init__(self, cycle: List[int]):
        super().__init__("cycle %s" % cycle)
        self.cycle = cycle


class ParallelError(Exception):
    """Members a ``constraint parallel`` relates cannot start together: the
    footprints or a ``sequence`` constraint put them in one group (*tied*),
    or one of them is in a group, so it may wait for another member."""

    def __init__(self, members: List[int], tied: bool):
        super().__init__("parallel %s %s" % (members, "tied" if tied else "grouped"))
        self.members = members
        self.tied = tied


def plan_schedule(fps: List[Footprint], before=(), together=()) -> SchedulePlan:
    """The plan of members with footprints *fps*; *before* holds extra
    ``(i, j)`` edges (``constraint sequence``), *together* lists of members
    that start together (``constraint parallel``)."""
    n = len(fps)
    edges: Set[Tuple[int, int]] = set()
    ties: Set[FrozenSet[int]] = set()
    for i, j in before:
        edges.add((i, j))
        ties.add(frozenset((i, j)))
    for i, j in itertools.permutations(range(n), 2):
        if fps[i].buf_out & fps[j].buf_in:
            edges.add((i, j))
            ties.add(frozenset((i, j)))
        if fps[i].state_out & (fps[j].state_in | fps[j].state_out):
            ties.add(frozenset((i, j)))
        if fps[i].res_lock & (fps[j].res_lock | fps[j].res_share):
            ties.add(frozenset((i, j)))
    _check_acyclic(n, edges)
    up = list(range(n))

    def find(x):
        while up[x] != x:
            up[x] = up[up[x]]
            x = up[x]
        return x
    for t in ties:
        a, b = sorted(t)
        up[find(a)] = find(b)
    by_root: Dict[int, List[int]] = {}
    for i in range(n):
        by_root.setdefault(find(i), []).append(i)
    groups = sorted(by_root.values(), key=lambda g: g[0])
    together = list(together) + [
        [i, j] for i, j in itertools.permutations(range(n), 2)
        if fps[i].stream_out & fps[j].stream_in]
    for ms in together:
        roots = [find(i) for i in ms]
        if len(set(roots)) < len(roots):
            raise ParallelError(sorted(ms), True)
        if any(len(by_root[r]) > 1 for r in roots):
            raise ParallelError(sorted(ms), False)
    orders = [linear_extensions(g, edges) for g in groups]
    return SchedulePlan(edges=edges, groups=groups, orders=orders)


def _check_acyclic(n: int, edges: Set[Tuple[int, int]]) -> None:
    succ: Dict[int, List[int]] = {}
    for a, b in edges:
        succ.setdefault(a, []).append(b)
    state = [0] * n
    path: List[int] = []

    def visit(x):
        state[x] = 1
        path.append(x)
        for y in sorted(succ.get(x, ())):
            if state[y] == 1:
                raise CycleError(path[path.index(y):] + [y])
            if state[y] == 0:
                visit(y)
        path.pop()
        state[x] = 2
    for x in range(n):
        if state[x] == 0:
            visit(x)


def linear_extensions(group: List[int], edges: Set[Tuple[int, int]],
                      cap: int = MAX_ORDERS) -> List[List[int]]:
    """The orders of *group* that keep every edge among its members, in
    lexicographic order; when there are more than *cap*, *cap* of them drawn
    with a fixed seed (the run's seed then picks one)."""
    members = set(group)
    preds = {x: {a for a, b in edges if b == x and a in members} for x in group}
    out: List[List[int]] = []
    limit = 5000

    def walk(prefix, left):
        if len(out) >= limit:
            return
        if not left:
            out.append(list(prefix))
            return
        for x in sorted(left):
            if preds[x] <= set(prefix):
                prefix.append(x)
                walk(prefix, left - {x})
                prefix.pop()
    walk([], set(group))
    if len(out) > cap:
        out = sorted(random.Random(0).sample(out, cap))
    return out
