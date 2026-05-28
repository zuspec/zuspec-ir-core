from __future__ import annotations
import dataclasses as dc
import inspect
import logging
from typing import TYPE_CHECKING, Iterable, Protocol, Self, cast, runtime_checkable, Optional, Any, Type

if TYPE_CHECKING:
    from .visitor import Visitor

_log = logging.getLogger("zuspec.ir.Base")

@dc.dataclass
class Loc(object):
    file : Optional[str] = dc.field()
    line : int = dc.field()
    pos : int = dc.field()
    ref : Optional[Any] = dc.field(default=None)

@runtime_checkable
class BaseP(Protocol):

    def visitDefault(self, v : Visitor, cls : Type): ...

    def accept(self, v : Visitor): ...

    def getLoc(self) -> Optional[Loc]: ...


def merge_loc(nodes: Iterable["BaseP"]) -> Optional[Loc]:
    """Return the first non-None ``loc`` from *nodes*, or ``None`` if all are ``None``.

    Useful when a synthesis pass merges several source nodes into one new node
    and needs to pick a representative source location::

        new_node.loc = merge_loc([src_a, src_b, src_c])
    """
    for node in nodes:
        loc = node.getLoc()
        if loc is not None:
            return loc
    return None


@dc.dataclass(kw_only=True)
class Base(BaseP):
    loc : Optional[Loc] = dc.field(default=None)

    def visitDefault(self, v : Visitor, cls : Optional[Type] = None):
        _log.debug("visitDefault %s %s" % (
            str(type(self).__name__), 
            (cls.__name__ if cls is not None else "None")))

        # Handle bases first
        if cls is not None:
            for b in cls.__bases__:
                if issubclass(b, BaseP):
                    _log.debug("Visit base class %s" % b.__name__)
                    getattr(v, "visit%s" % b.__name__)(self)

        # Iterate through the fields and dynamically handle
        for f in dc.fields(self):
            _log.debug("Processing field %s (%s)" % (f.name, f.type))
            o = getattr(self, f.name)
            _log.debug("o: %s (%s)" % (str(o), type(o)))
            if inspect.isclass(type(o)):
                _log.debug("isclass")
                if isinstance(o, BaseP):
                    _log.debug("o isBaseP")
                    cast(BaseP, o).accept(v)
                elif isinstance(o, list):
                    _log.debug("islist")
                    for e in o:
                        _log.debug("  e: %s" % str(e))
                        if e is not None and inspect.isclass(type(e)) and isinstance(e, BaseP):
                            _log.debug("isBaseP")
                            cast(BaseP, e).accept(v)

    def accept(self, v : Visitor, virt : bool=True):
        getattr(v, "visit%s" % type(self).__name__)(self)

    def getLoc(self) -> Optional[Loc]:
        return self.loc

    def copy_loc(self, other: "BaseP") -> "Self":
        """Copy the ``loc`` from *other* onto ``self`` and return ``self`` (fluent).

        When ``other.loc`` is ``None`` the existing ``self.loc`` is left
        unchanged so that a node with an explicit location is never silently
        erased::

            new_node = MyNode(...).copy_loc(source_node)
        """
        src_loc = other.getLoc()
        if src_loc is not None:
            self.loc = src_loc
        return self
