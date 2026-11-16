"""Domain IR (fw-hdl core-domains-plan §6.2, "Z6").

A *domain kind* (clock, reset, a user kind) is a ``DataTypeDomainKind``,
stored once in ``Context.type_m`` under its name. Clock and reset are
instances of it, so a new kind needs no IR change.

A component declares its domain objects (``DomainDecl``), associates its
children and interfaces with domains (``DomainAssoc``), drives domains from
signals (``DomainDriver``), waives crossings (``DomainWaiver``), names its
generated domain ports (``DomainPortName``) and states the derivations it
drives (``DomainProvides``), in the lists ``DataTypeComponent.domains``,
``domain_assocs``, ``domain_drivers``, ``domain_waivers``,
``domain_port_names`` and ``domain_provides``. An extern maps module pins to domains through
``ExternPin.domain_kind``.

A statement made in a loop or under a condition on template parameters
sits in the matching ``GenerateFor``/``GenerateIf`` of
``DataTypeComponent.generates``, like a ``Bind``.

Paths (``DomainAssoc.target``, ``DomainDriver.signal``, ``DomainWaiver.a``
and ``b``, ``DomainRef.target``) are lists of field names from the
component down. An element is a ``str``, or an ``ExprHierarchicalElem``
when the field is indexed (``ch[i]``). ``[]`` is the component itself.

What a domain *resolves to* at an instance, the relation between domains,
crossings and realization are computed by passes; none of it is stored.
"""
from __future__ import annotations
import dataclasses as dc
import enum
from typing import Dict, List, Optional, Union

from .base import Base
from .data_type import DataType
from .expr import Expr, ExprHierarchicalElem

#: A path element: a field name, or a field name with a subscript.
PathElem = Union[str, ExprHierarchicalElem]


class DomainRel(enum.Enum):
    """The relation between two domains of one kind (spec
    ``fw_domain_rel_e``)."""
    SAME      = enum.auto()   # the same domain object
    RELATED   = enum.auto()   # they share a root through same-kind sources
    UNRELATED = enum.auto()


class DomainSeverity(enum.Enum):
    """The severity of a crossing. ``NONE`` reports nothing."""
    NONE    = enum.auto()
    INFO    = enum.auto()
    WARNING = enum.auto()
    ERROR   = enum.auto()


@dc.dataclass(kw_only=True)
class DomainDerivDecl(Base):
    """One derivation of a kind (``derived``, ``synced``): which of its
    arguments are source domains of the kind itself. Arguments that are
    domains of other kinds (a reset's ``clock``) are not sources."""
    sources: List[str] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class DataTypeDomainKind(DataType):
    """A domain kind. ``name`` is the kind's class name (``fw_clock_kind``).

    - ``physical``: a realized domain is a 1-bit net; False for a marker
      kind, which never emits anything.
    - ``derivations``: by derivation name.
    - ``severities``: the default severity of a crossing, keyed by the
      relation's name in lower case (``"same"``, ``"related"``,
      ``"unrelated"``), as the elaboration option spells it.
    - ``port_names``: ``K::port_name(assoc, attr)`` for each association
      name and pin attribute value a front end saw used, keyed by
      :func:`port_name_key` (``"/active_low=1,style=0" -> "rst_n"``). The
      kind's ``pin_default()`` value is ``pin_default``.
    - ``polarity``: the pin attribute that is a net's polarity
      (``"active_low"``), or ``""``. Where a sink's value differs from its
      net's, emission connects the sink to an inverted net.
    """
    physical: bool = dc.field(default=True)
    derivations: Dict[str, DomainDerivDecl] = dc.field(default_factory=dict)
    severities: Dict[str, DomainSeverity] = dc.field(default_factory=dict)
    port_names: Dict[str, str] = dc.field(default_factory=dict)
    pin_default: Dict[str, Expr] = dc.field(default_factory=dict)
    polarity: str = dc.field(default="")


def port_name_key(assoc: str, attr: Dict[str, Expr]) -> str:
    """The key of ``DataTypeDomainKind.port_names``: the association's name
    and the attribute value's integer fields, by name."""
    vals = ",".join(f"{k}={getattr(v, 'value', v)}" for k, v in sorted(attr.items()))
    return f"{assoc}/{vals}"


@dc.dataclass(kw_only=True)
class DataTypeDomain(DataType):
    """The type of a domain handle: ``fw_domain #(K)``. ``kind`` is the
    kind's name in ``type_m``."""
    kind: str = dc.field()


@dc.dataclass(kw_only=True)
class DomainRef(Expr):
    """A domain as the component's code reaches it. Exactly one form:

    - ``decl``: the name of a ``DomainDecl`` of this component;
    - ``kind`` (with ``assoc`` and ``target``): the domain of kind ``kind``
      that ``target`` resolves to, for the association ``assoc`` (``""``
      is the default): ``get(this)`` is ``target=[]``, ``get(this.in)``
      is ``target=["in"]``.

    It is an ``Expr`` so that it can stand in a derivation's arguments and
    a domain's params (a reset's ``clock``).
    """
    decl: Optional[str] = dc.field(default=None)
    kind: Optional[str] = dc.field(default=None)
    assoc: str = dc.field(default="")
    target: List[PathElem] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class DomainDecl(Base):
    """A domain object the component creates (``fw_domain #(K)::create``).

    ``deriv`` is the derivation's name (``"root"`` for a root domain),
    ``sources`` its same-kind source domains, ``args`` all its arguments
    by name (sources included, as ``DomainRef``), and ``params`` the
    resulting ``params_t`` value by field."""
    name: str = dc.field()
    kind: str = dc.field()
    deriv: str = dc.field(default="root")
    sources: List[DomainRef] = dc.field(default_factory=list)
    args: Dict[str, Expr] = dc.field(default_factory=dict)
    params: Dict[str, Expr] = dc.field(default_factory=dict)


@dc.dataclass(kw_only=True)
class DomainAssoc(Base):
    """``fw_domain #(K)::set(target, domain, name)``: ``target`` is a
    direct child or an own interface, ``[]`` for the component itself;
    ``assoc`` is the association's name, ``""`` for the default. (Not
    ``name``: an association is not a declaration, and a named node in a
    component's lists is anchored as a member.)"""
    target: List[PathElem] = dc.field(default_factory=list)
    kind: str = dc.field()
    assoc: str = dc.field(default="")
    domain: DomainRef = dc.field()


@dc.dataclass(kw_only=True)
class DomainDriver(Base):
    """``domain.drive(signal, attr)``: ``signal`` (a port or local signal
    of the component) is the domain's net, with attributes ``attr`` (a
    reset's ``active_low``)."""
    domain: DomainRef = dc.field()
    signal: List[PathElem] = dc.field(default_factory=list)
    attr: Dict[str, Expr] = dc.field(default_factory=dict)


@dc.dataclass(kw_only=True)
class DomainWaiver(Base):
    """``fw_domain #(K)::waive(a, b, reason)``: the crossing of ``kind``
    between the interfaces ``a`` and ``b`` is waived."""
    kind: str = dc.field()
    a: List[PathElem] = dc.field(default_factory=list)
    b: List[PathElem] = dc.field(default_factory=list)
    reason: str = dc.field(default="")


@dc.dataclass(kw_only=True)
class DomainPortName(Base):
    """``fw_domain #(K)::port_name(this, assoc, port)``: the generated port
    of the class's association ``assoc`` of ``kind`` is named ``port``."""
    kind: str = dc.field()
    assoc: str = dc.field(default="")
    port: str = dc.field()


@dc.dataclass(kw_only=True)
class DomainProvides(Base):
    """``fw_domain #(K)::provides(this, assoc, p)``: the component drives
    the domain ``assoc`` resolves to, with the derivation ``deriv`` and
    params ``params`` (as in ``DomainDecl``)."""
    kind: str = dc.field()
    assoc: str = dc.field(default="")
    deriv: str = dc.field(default="root")
    sources: List[DomainRef] = dc.field(default_factory=list)
    params: Dict[str, Expr] = dc.field(default_factory=dict)
