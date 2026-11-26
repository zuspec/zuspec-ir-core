"""Packages: named scopes of declarations (an SV ``package``; a Python
module, later).

A ``Package`` lists its members in source order, since a declaration may
use any earlier one (a typedef, a constant of that type, a function over
both). Types are not owned here: a ``PackageType`` names a type kept in
``Context.type_m`` (or a front end's type table) by its qualified name.
"""
from __future__ import annotations
import dataclasses as dc
from typing import List, Optional, TYPE_CHECKING
from .base import Base

if TYPE_CHECKING:
    from .data_type import DataType, Function
    from .expr import Expr


@dc.dataclass(kw_only=True)
class PackageImport(Base):
    """``import pkg::*`` (``name`` None) or ``import pkg::name``."""
    package: str = dc.field()
    name: Optional[str] = dc.field(default=None)


@dc.dataclass(kw_only=True)
class PackageConst(Base):
    """A named constant (SV ``localparam``/``parameter``): its type and
    its value as written (``'{ADDR_W: 32, ...}``, ``W * 2``)."""
    name: str = dc.field()
    datatype: Optional["DataType"] = dc.field(default=None)
    value: Optional["Expr"] = dc.field(default=None)


@dc.dataclass(kw_only=True)
class PackageType(Base):
    """A type declared in the package (a typedef or a class), by its
    qualified name (``p::s_t``)."""
    name: str = dc.field()


@dc.dataclass(kw_only=True)
class Package(Base):
    """A package: ``items`` are ``PackageImport``, ``PackageConst``,
    ``Function`` (package-scope functions) and ``PackageType``, in source
    order."""
    name: str = dc.field()
    items: List[Base] = dc.field(default_factory=list)

    @property
    def imports(self) -> List[PackageImport]:
        return [i for i in self.items if isinstance(i, PackageImport)]

    @property
    def constants(self) -> List[PackageConst]:
        return [i for i in self.items if isinstance(i, PackageConst)]

    @property
    def functions(self) -> List["Function"]:
        from .data_type import Function
        return [i for i in self.items if isinstance(i, Function)]

    @property
    def types(self) -> List[str]:
        return [i.name for i in self.items if isinstance(i, PackageType)]
