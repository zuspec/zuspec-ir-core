"""The flattened storage layout of an action object (P1-D1).

An action object is a flat list of scalar slots. A plain-data ``struct``
attribute takes one slot per scalar it holds, recursively, base struct's
fields first (LRM 8.3, 8.5.3: a struct is a value; assignment copies it). Every
consumer that addresses a slot -- the scenario pass (``ScField``), the solve
problem (``ScSolveVar.slot``), bc's procedural lowering and its locals -- takes
the layout from here, so a slot means the same thing to all of them.

A leaf is named by its dotted path from the object (``s.csr.eol``). Anything
that is not a plain-data struct -- a scalar, an array, an action handle, a flow
object reference, a resource claim -- is one opaque slot, exactly as before;
what an opaque slot may be used for is each consumer's business.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from ... import expr as E
from ...data_type import DataTypeClass, DataTypeRef, DataTypeStruct
from ..validate import UnsupportedConstructError


class Leaf(NamedTuple):
    """One scalar slot of a flattened value."""
    #: path from the value's root: ``("csr", "eol")``
    path: Tuple[str, ...]
    #: the leaf's Layer-0 type (scalar, or opaque)
    datatype: Any
    #: the ``Field`` declaring it
    field: Any
    #: randomized when its object is solved: it is ``rand`` and so is every
    #: struct attribute on its path (LRM 8.3.2)
    rand: bool

    @property
    def name(self) -> str:
        return ".".join(self.path)


Types = Optional[Dict[str, Any]]


def resolve(dt: Any, types: Types) -> Any:
    """*dt* with a by-name reference replaced by the type it names."""
    seen = 0
    while isinstance(dt, DataTypeRef):
        target = (types or {}).get(dt.ref_name)
        if target is None:
            raise UnsupportedConstructError(
                "type %r is not known to the layout" % dt.ref_name,
                loc=getattr(dt, "loc", None))
        dt = target
        seen += 1
        if seen > 64:
            raise UnsupportedConstructError(
                "type reference %r does not resolve" % dt.ref_name)
    return dt


def is_struct(dt: Any, types: Types = None) -> bool:
    """Is *dt* a plain-data struct, the one kind of type that is flattened?

    Actions and components are classes; a flow object or resource is a struct
    with a ``flow_kind`` and is reached through a reference, so it is not.
    """
    if isinstance(dt, DataTypeRef):
        try:
            dt = resolve(dt, types)
        except UnsupportedConstructError:
            return False
    return (isinstance(dt, DataTypeStruct) and not isinstance(dt, DataTypeClass)
            and getattr(dt, "flow_kind", None) is None)


def struct_chain(dt: Any, types: Types) -> List[Any]:
    """*dt* and its bases, base first."""
    chain = []
    dt = resolve(dt, types)
    while dt is not None:
        if any(c is dt for c in chain):
            raise UnsupportedConstructError(
                "struct %r inherits from itself" % getattr(dt, "name", "?"),
                loc=getattr(dt, "loc", None))
        chain.append(dt)
        sup = getattr(dt, "super", None)
        dt = resolve(sup, types) if sup is not None else None
    return list(reversed(chain))


def struct_fields(dt: Any, types: Types) -> List[Any]:
    """The fields of struct *dt*, its bases' first (LRM 8.3)."""
    out = []
    for t in struct_chain(dt, types):
        out.extend(getattr(t, "fields", []) or [])
    return out


def struct_functions(dt: Any, types: Types) -> List[Any]:
    """The functions (constraint and exec blocks) of struct *dt*, bases' first."""
    out = []
    for t in struct_chain(dt, types):
        out.extend(getattr(t, "functions", []) or [])
    return out


def value_leaves(dt: Any, types: Types, path: Tuple[str, ...] = (),
                 field: Any = None, rand: bool = False,
                 _open: Tuple[int, ...] = ()) -> List[Leaf]:
    """The leaves of a value of type *dt* rooted at *path*."""
    if not is_struct(dt, types):
        return [Leaf(path, dt, field, rand)]
    dt = resolve(dt, types)
    if id(dt) in _open:
        raise UnsupportedConstructError(
            "struct %r contains itself" % getattr(dt, "name", "?"),
            loc=getattr(dt, "loc", None))
    out: List[Leaf] = []
    for f in struct_fields(dt, types):
        out.extend(value_leaves(
            f.datatype, types, path + (f.name,), f,
            rand and getattr(f, "rand_kind", None) is not None,
            _open + (id(dt),)))
    return out


def field_leaves(f: Any, types: Types) -> List[Leaf]:
    """The leaves of attribute *f*, rooted at its name."""
    return value_leaves(f.datatype, types, (f.name,), f,
                        getattr(f, "rand_kind", None) is not None)


def object_layout(fields: List[Any], types: Types) -> List[Leaf]:
    """The slots of an object with *fields*, in slot order."""
    out: List[Leaf] = []
    for f in fields or []:
        out.extend(field_leaves(f, types))
    return out


def slot_count(dt: Any, types: Types) -> int:
    """How many slots a value of type *dt* takes."""
    return len(value_leaves(dt, types))


def prefix_self(e: Any, prefix: Tuple[str, ...]) -> Any:
    """*e* with ``self`` meaning the struct attribute at *prefix*.

    A struct type's constraints and field initializers are written against
    the struct (``self.f``); applied to the attribute ``s`` of an action they
    mean ``self.s.f``.
    """
    if not prefix:
        return e
    if isinstance(e, E.TypeExprRefSelf):
        out: Any = E.TypeExprRefSelf()
        for p in prefix:
            out = E.ExprAttribute(value=out, attr=p)
        return out
    if dc.is_dataclass(e) and not isinstance(e, type):
        repl = {}
        for f in dc.fields(e):
            val = getattr(e, f.name)
            if isinstance(val, E.Expr):
                repl[f.name] = prefix_self(val, prefix)
            elif isinstance(val, list) and any(isinstance(x, E.Expr) for x in val):
                repl[f.name] = [prefix_self(x, prefix) if isinstance(x, E.Expr)
                                else x for x in val]
        return dc.replace(e, **repl) if repl else e
    return e


__all__ = [
    "prefix_self",
    "Leaf", "resolve", "is_struct", "struct_chain", "struct_fields",
    "struct_functions", "value_leaves", "field_leaves", "object_layout",
    "slot_count",
]
