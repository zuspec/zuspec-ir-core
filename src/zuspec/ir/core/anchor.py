"""Anchors: stable, serializable identity for IR nodes.

The rules are specified in ``docs/anchors.md``; this module implements them.
:func:`assign_anchors` names every root type (or free function), named member,
statement and expression it is given, sets ``node.anchor``, and returns an
:class:`AnchorTable`: anchor -> kind, enclosing scope, source position and
source name, plus a content hash per named scope. Engine results are recorded
against anchors (``docs/results.md``), and the table is how a consumer finds
its way from an anchor back to user source, without loading the model.
"""
from __future__ import annotations

import dataclasses as dc
import enum
import hashlib
import json
import re
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union

from .base import Base
from .context import Context
from .data_type import DataType, Function, Process
from .expr import Expr
from .fields import Field
from .provenance import Provenance
from .stmt import Stmt, StmtAnnAssign, StmtPass

ANCHOR_TABLE_FORMAT = "zuspec.anchor-table"
ANCHOR_TABLE_VERSION = 2

#: Characters that structure an anchor; a name containing one is escaped.
_RESERVED = set(".#@:~+\\")

#: Fields that are about a node, not part of it: never walked for anchors.
_WALK_SKIP = frozenset({"loc", "doc", "doc_trailing", "anchor", "annotations",
                        "provenance", "metadata"})

#: Fields left out of a scope hash, so that moving code or editing comments
#: does not make its results stale.
_HASH_SKIP = frozenset({"loc", "doc", "doc_trailing", "anchor", "annotations",
                        "provenance"})

#: Fields that hold a type written as an expression (``x: u8``, an argument's
#: annotation): part of a declaration, not values, so they get no anchors.
_EXPR_SKIP = frozenset({"annotation"})

#: ``Provenance.pass_name`` prefix that marks a node a front end synthesized.
SYNTH_PREFIX = "synth:"

#: Kinds of anchored node.
KIND_TYPE, KIND_MEMBER, KIND_STMT, KIND_EXPR = "type", "member", "stmt", "expr"

LocKey = Tuple[Optional[str], int, int]


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def escape(name: str) -> str:
    """*name* as one anchor segment: reserved characters, whitespace and
    non-ASCII characters become ``\\xx`` (lowercase hex of each UTF-8 byte)."""
    out = []
    for ch in name:
        if ch in _RESERVED or ch.isspace() or ord(ch) > 0x7e or ord(ch) < 0x21:
            out.extend(f"\\{b:02x}" for b in ch.encode("utf-8"))
        else:
            out.append(ch)
    return "".join(out)


def unescape(seg: str) -> str:
    """The inverse of :func:`escape`."""
    raw = bytearray()
    i = 0
    while i < len(seg):
        if seg[i] == "\\":
            raw.append(int(seg[i + 1:i + 3], 16))
            i += 3
        else:
            raw.extend(seg[i].encode("ascii"))
            i += 1
    return raw.decode("utf-8")


def neutral_root(name: str) -> str:
    """The anchor root for a type or free-function name in any front end's
    spelling: ``rle_pkg::RunLengthEncoder`` and ``rle_pkg.RunLengthEncoder``
    both give ``rle_pkg.RunLengthEncoder``."""
    return ".".join(escape(s) for s in re.split(r"::|\.", name) if s)


def qualify(anchor: str, kind: str, *args: Any) -> str:
    """A derived anchor: *anchor* plus the qualifier ``@kind[:arg]*``
    (``docs/anchors.md`` §3), e.g. ``qualify(a, "reg", 2)`` -> ``a@reg:2``."""
    return anchor + "@" + escape(kind) + "".join(":" + escape(str(a)) for a in args)


def split_qualifier(anchor: str) -> Tuple[str, Optional[List[str]]]:
    """``(base anchor, [kind, args...])``, or ``(anchor, None)`` when it has no
    qualifier."""
    base, at, q = anchor.partition("@")
    if not at:
        return anchor, None
    return base, [unescape(p) for p in q.split(":")]


def mark_synthesized(node: Base, reason: str, by: str = "") -> Base:
    """Mark *node* as made up by a front end, not written by the user (a
    function's return variable, an implicit cast). Marked nodes take no
    ordinal, so they do not shift the anchors of what the user wrote
    (``docs/anchors.md`` §2.5). Returns *node*."""
    node.provenance = Provenance(pass_name=SYNTH_PREFIX + reason, description=by)
    return node


def synthesized_reason(node: Any) -> Optional[str]:
    """The reason *node* was synthesized, or None for a user-written node.
    A ``pass`` statement is a placeholder and always counts as synthesized."""
    p = getattr(node, "provenance", None)
    name = getattr(p, "pass_name", None)
    if isinstance(name, str) and name.startswith(SYNTH_PREFIX):
        return name[len(SYNTH_PREFIX):] or "node"
    if isinstance(node, StmtPass):
        return "pass"
    return None


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

@dc.dataclass
class AnchorEntry:
    """What the table knows about one anchor."""
    kind: str
    #: The named scope (root or member anchor) whose content hash covers it.
    scope: str
    #: ``Loc.key()`` of the node: (absolute file, line, column), all 1-based.
    loc: Optional[LocKey] = None
    #: The name as the front end spelled it (roots and members only).
    source_name: Optional[str] = None


class AnchorTable:
    """Anchors of one model: entries, scope hashes, and a position index."""

    def __init__(self):
        self.entries: Dict[str, AnchorEntry] = {}
        self.scope_hashes: Dict[str, str] = {}
        self._nodes: Dict[str, Base] = {}
        self._by_loc: Optional[Dict[LocKey, List[str]]] = None

    def __contains__(self, anchor: str) -> bool:
        return anchor in self.entries

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[str]:
        return iter(self.entries)

    def __getitem__(self, anchor: str) -> AnchorEntry:
        return self.entries[anchor]

    def node_of(self, anchor: str) -> Optional[Base]:
        """The live node, when the table was built from a model in this process."""
        return self._nodes.get(anchor)

    def by_loc(self, key: LocKey) -> List[str]:
        """Anchors whose node starts at *key* ``(file, line, column)``; *file* is
        compared as an absolute path. Statements come before expressions, each
        in anchor order."""
        if self._by_loc is None:
            idx: Dict[LocKey, List[str]] = {}
            for a, e in self.entries.items():
                if e.loc is not None:
                    idx.setdefault(e.loc, []).append(a)
            order = {KIND_TYPE: 0, KIND_MEMBER: 1, KIND_STMT: 2, KIND_EXPR: 3}
            for v in idx.values():
                v.sort(key=lambda a: (order[self.entries[a].kind], a))
            self._by_loc = idx
        return list(self._by_loc.get(key, ()))

    def by_line(self, file: Optional[str], line: int) -> List[str]:
        """Statement anchors whose node starts on *line* of *file* (absolute
        path), for mapping a position that names no expression exactly."""
        self.by_loc((None, 0, 0))   # build the index
        return [a for (f, l, _), v in sorted(self._by_loc.items(), key=lambda kv: kv[0][2])
                if f == file and l == line
                for a in v if self.entries[a].kind == KIND_STMT]

    def scope_of(self, anchor: str) -> Optional[str]:
        """The named scope an anchor (derived or not) belongs to."""
        e = self.entries.get(split_qualifier(anchor)[0])
        return e.scope if e is not None else None

    # -- file form ----------------------------------------------------------

    def to_json(self) -> Dict[str, Any]:
        return {
            "format": ANCHOR_TABLE_FORMAT,
            "version": ANCHOR_TABLE_VERSION,
            "scope_hashes": dict(sorted(self.scope_hashes.items())),
            "anchors": {a: _entry_to_json(e) for a, e in self.entries.items()},
        }

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "AnchorTable":
        if d.get("format") != ANCHOR_TABLE_FORMAT:
            raise ValueError(f"not an anchor table (format {d.get('format')!r})")
        if d.get("version") != ANCHOR_TABLE_VERSION:
            raise ValueError(f"unsupported anchor-table version {d.get('version')!r}")
        t = cls()
        t.scope_hashes = dict(d.get("scope_hashes", {}))
        for a, e in d.get("anchors", {}).items():
            loc = e.get("loc")
            t.entries[a] = AnchorEntry(kind=e["kind"], scope=e["scope"],
                                       loc=tuple(loc) if loc is not None else None,
                                       source_name=e.get("source_name"))
        return t

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_json(), f, indent=1)
            f.write("\n")

    @classmethod
    def load(cls, path: str) -> "AnchorTable":
        with open(path) as f:
            return cls.from_json(json.load(f))


def _entry_to_json(e: AnchorEntry) -> Dict[str, Any]:
    d: Dict[str, Any] = {"kind": e.kind, "scope": e.scope}
    if e.loc is not None:
        d["loc"] = list(e.loc)
    if e.source_name is not None:
        d["source_name"] = e.source_name
    return d


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

Roots = Union[Context, Base, Dict[str, Base], Iterable[Base]]


def assign_anchors(roots: Roots, table: Optional[AnchorTable] = None) -> AnchorTable:
    """Anchor every node under *roots* and return the table.

    *roots* is a :class:`Context` (every ``type_m`` entry, named by its key), a
    single type or free function (named by its ``name``), a dict of them (named
    by key), or an iterable of them. Assigning twice gives the same anchors;
    nodes reached from no root are left alone.
    """
    t = table if table is not None else AnchorTable()
    named = _named_roots(roots)
    root_ids = {id(n) for _, n in named}
    for name, node in named:
        _Assigner(t, root_ids).root(name, node)
    t._by_loc = None
    return t


def _named_roots(roots: Roots) -> List[Tuple[str, Base]]:
    if isinstance(roots, Context):
        return list(roots.type_m.items())
    if isinstance(roots, dict):
        return list(roots.items())
    if isinstance(roots, Base):
        roots = [roots]
    out = []
    for n in roots:
        name = getattr(n, "name", None)
        if not isinstance(name, str) or not name:
            raise ValueError(f"a root {type(n).__name__} has no name; pass a dict to name it")
        out.append((name, n))
    return out


def _is_stmt_list(v: Any) -> bool:
    return isinstance(v, list) and len(v) > 0 and all(isinstance(s, Stmt) for s in v)


def _fields(node: Any) -> Iterator[Tuple[str, Any]]:
    for f in dc.fields(node):
        if f.name not in _WALK_SKIP:
            yield f.name, getattr(node, f.name)


class _Assigner:

    def __init__(self, table: AnchorTable, root_ids: set):
        self.t = table
        self.root_ids = root_ids

    def _set(self, node: Base, anchor: str, kind: str, scope: str,
             source_name: Optional[str] = None) -> None:
        node.anchor = anchor
        loc = node.loc.key() if getattr(node, "loc", None) is not None else None
        self.t.entries[anchor] = AnchorEntry(kind=kind, scope=scope, loc=loc,
                                             source_name=source_name)
        self.t._nodes[anchor] = node

    # -- roots and members ----------------------------------------------------

    def root(self, name: str, node: Base) -> None:
        r = neutral_root(name)
        self._set(node, r, KIND_TYPE, r, source_name=name)
        self.t.scope_hashes[r] = scope_hash(node, self.root_ids)
        if isinstance(node, (Function, Process)):
            # A free function: its body sits directly under the root.
            self._owner(node, r, r)
            return
        member_lists = set()
        used: Dict[str, int] = {}
        for fname, v in _fields(node):
            if not isinstance(v, list):
                continue
            members = [m for m in v if isinstance(m, Base) and not isinstance(m, (Stmt, Expr))
                       and isinstance(getattr(m, "name", None), str) and m.name]
            if not members:
                continue
            member_lists.add(fname)
            for m in members:
                seg = escape(m.name)
                used[seg] = used.get(seg, 0) + 1
                if used[seg] > 1:
                    seg = f"{seg}~{used[seg]}"
                a = f"{r}.{seg}"
                self._set(m, a, KIND_MEMBER, a, source_name=m.name)
                self.t.scope_hashes[a] = scope_hash(m, self.root_ids)
                self._owner(m, a, a)
        # Statements and expressions held by the type itself (an action's
        # body_stmts, say) belong to the root.
        self._owner(node, r, r, skip=member_lists)

    def _owner(self, node: Base, anchor: str, scope: str, skip: Iterable[str] = ()) -> None:
        """Statement lists and expressions of a root or member."""
        skip = set(skip)
        for fname, v in _fields(node):
            if fname in skip or not _is_stmt_list(v):
                continue
            prefix = f"{anchor}#" if fname == "body" else f"{anchor}#{escape(fname)}."
            for s, seg in zip(v, _stmt_segments(v)):
                self._stmt(s, prefix + seg, scope)
        exprs: List[Expr] = []
        for fname, v in _fields(node):
            if fname not in skip and fname not in _EXPR_SKIP and not _is_stmt_list(v):
                _collect_exprs(v, exprs, set())
        self._exprs(exprs, anchor, scope)

    def _exprs(self, exprs: List[Expr], anchor: str, scope: str) -> None:
        """User expressions are ``.eK`` in pre-order; a synthesized one is
        ``.x:<reason>`` on the first user expression under it (or on *anchor*
        when it has none), so it shifts no ordinals."""
        k = 0
        synth = []
        for e in exprs:
            r = synthesized_reason(e)
            if r is None:
                k += 1
                self._set(e, f"{anchor}.e{k}", KIND_EXPR, scope)
            else:
                synth.append((e, r))
        used: Dict[str, int] = {}
        for e, r in synth:
            sub: List[Expr] = []
            for _, w in _fields(e):
                _collect_exprs(w, sub, set())
            on = next((x.anchor for x in sub
                       if synthesized_reason(x) is None and x.anchor), anchor)
            a = f"{on}.x:{escape(r)}"
            used[a] = used.get(a, 0) + 1
            self._set(e, a if used[a] == 1 else f"{a}~{used[a]}", KIND_EXPR, scope)

    # -- statements and expressions -----------------------------------------

    def _stmt(self, s: Stmt, anchor: str, scope: str) -> None:
        self._set(s, anchor, KIND_STMT, scope)
        exprs: List[Expr] = []
        for fname, v in _fields(s):
            if fname not in _EXPR_SKIP and not _is_stmt_list(v):
                _collect_exprs(v, exprs, set())
        self._exprs(exprs, anchor, scope)
        for fname, v in _fields(s):
            if _is_stmt_list(v):
                for c, seg in zip(v, _stmt_segments(v)):
                    self._stmt(c, f"{anchor}.{escape(fname)}.{seg}", scope)
            elif isinstance(v, list):
                # Lists of clauses, cases, handlers: each element's own lists.
                for k, item in enumerate(v, 1):
                    if not isinstance(item, Base) or isinstance(item, (Stmt, Expr)):
                        continue
                    for gname, w in _fields(item):
                        if _is_stmt_list(w):
                            for c, seg in zip(w, _stmt_segments(w)):
                                self._stmt(c, f"{anchor}.{escape(fname)}.{k}.{escape(gname)}.{seg}",
                                           scope)


def _decl_name(s: Stmt) -> Optional[str]:
    """The local a declaration statement declares, if *s* is one."""
    if isinstance(s, StmtAnnAssign):
        name = getattr(s.target, "name", None)
        if isinstance(name, str) and name:
            return name
    return None


def _stmt_segments(stmts: List[Stmt]) -> List[str]:
    """The last anchor segment of each statement of one list (§2.4-2.5):

    * a declaration of local ``n``: ``v:n``;
    * a statement after it: ``v:n+K`` (the K-th since that declaration);
    * a statement before any declaration: ``sK``;
    * a synthesized statement: ``x:<reason>``, taking no ordinal.

    A repeated segment gets ``~N``, as members do.
    """
    out: List[str] = []
    used: Dict[str, int] = {}

    def uniq(seg: str) -> str:
        used[seg] = used.get(seg, 0) + 1
        return seg if used[seg] == 1 else f"{seg}~{used[seg]}"

    base, n = None, 0
    for s in stmts:
        r = synthesized_reason(s)
        if r is not None:
            out.append(uniq("x:" + escape(r)))
            continue
        name = _decl_name(s)
        if name is not None:
            base, n = uniq("v:" + escape(name)), 0
            out.append(base)
            continue
        n += 1
        out.append(f"s{n}" if base is None else f"{base}+{n}")
    return out


#: Declarations are anchored where they are declared, never through a reference.
_NOT_WALKED = (DataType, Field, Function, Process, Stmt)


def _collect_exprs(v: Any, out: List[Expr], seen: set) -> None:
    """Pre-order: each Expr before its operands; operands in field order."""
    if isinstance(v, (list, tuple)):
        for x in v:
            _collect_exprs(x, out, seen)
        return
    if not isinstance(v, Base) or isinstance(v, _NOT_WALKED) or id(v) in seen:
        return
    seen.add(id(v))
    if isinstance(v, Expr):
        out.append(v)
    for n, w in _fields(v):
        if n not in _EXPR_SKIP and not _is_stmt_list(w):
            _collect_exprs(w, out, seen)


# ---------------------------------------------------------------------------
# Scope hashes
# ---------------------------------------------------------------------------

def scope_hash(node: Base, root_ids: Optional[set] = None) -> str:
    """16 hex digits of SHA-256 over *node*'s canonical content, ignoring
    positions, docs, anchors, annotations and provenance. Other root types it
    refers to count by name only."""
    canon = _canon(node, node, root_ids or set(), set())
    data = json.dumps(canon, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


def _canon(v: Any, top: Base, root_ids: set, seen: set) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, enum.Enum):
        return f"{type(v).__name__}.{v.name}"
    if isinstance(v, type):
        return f"<type {v.__module__}.{v.__qualname__}>"
    if isinstance(v, (list, tuple)):
        return [_canon(x, top, root_ids, seen) for x in v]
    if isinstance(v, dict):
        return {str(k): _canon(x, top, root_ids, seen) for k, x in v.items()}
    if dc.is_dataclass(v):
        if v is not top and id(v) in root_ids:
            return {"_root": getattr(v, "name", None)}
        if id(v) in seen:
            return "<cycle>"
        seen.add(id(v))
        d = {"_t": type(v).__name__}
        for f in dc.fields(v):
            if f.name not in _HASH_SKIP:
                d[f.name] = _canon(getattr(v, f.name), top, root_ids, seen)
        seen.discard(id(v))
        return d
    # Opaque objects (callables, Python types' instances): their type only.
    return f"<{type(v).__name__}>"
