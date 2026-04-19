from __future__ import annotations
import dataclasses as dc
import inspect
import logging
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable, Optional, Any, Type

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
