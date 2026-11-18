"""Protocol IR (fw-hdl protocol-kit-plan §8.11, "Z7").

An *API* is an interface class: the existing ``DataTypeProtocol`` (its
``methods``). A class that implements APIs names them in
``DataTypeClass.implements``.

A *protocol hub* is a ``DataTypeProtocolHub``, stored once in
``Context.type_m`` under its family name: its template parameters and
the APIs it carries (at most one per direction). A specialization of a family is always a
``ProtocolRef`` (family plus named params), never a type of its own.

A bundle class names its protocol in ``DataTypeClass.bundle_protocol``
(``DataTypeBundle.bundle_protocol`` for a bundle type).
A component holds, in call order:

- ``converter``: set on a transactor or abstractor component
  (``ProtocolConverter``);
- ``bundle_protocols``: the protocol and role of its bundle instances;
- ``api_endpoints``: its ports, exports and sockets;
- ``api_connections``: the API connections it makes;
- ``cfg_publishers``/``cfg_consumers``/``cfg_connections``: the
  configuration objects it publishes and reads, and how they connect;
- ``cfg_listeners``: the listeners its code registers on sinks.

Configuration is connected, not run: the IR says which sinks and
listeners a change at a source reaches, not what values they see.

Paths are lists of field names from the component down, as in
``domain.py``. A domain association of an endpoint is a ``DomainAssoc``
on the endpoint's path; there is no field for it here.
"""
from __future__ import annotations
import dataclasses as dc
import enum
from typing import Dict, List, Optional

from .base import Base
from .data_type import DataType, TemplateParam
from .domain import PathElem
from .expr import Expr


class ProtoDir(enum.Enum):
    """The direction of an API within a protocol."""
    REQ = enum.auto()
    RSP = enum.auto()


@dc.dataclass(kw_only=True)
class ProtocolRef(Expr):
    """A specialization of a protocol family: ``family`` is the hub's name
    in ``type_m``, ``params`` its template parameters by name."""
    family: str = dc.field()
    params: Dict[str, Expr] = dc.field(default_factory=dict)


@dc.dataclass(kw_only=True)
class ProtocolApi(Base):
    """One API a hub carries: ``api`` is the name of a ``DataTypeProtocol``,
    or of a ``DataTypeParameterized`` over one (a parameterized API)."""
    api: str = dc.field()
    dir: ProtoDir = dc.field()


@dc.dataclass(kw_only=True)
class DataTypeProtocolHub(DataType):
    """A protocol family. ``name`` is the family's name.

    - ``apis``: at most one per direction.

    Configuration belongs to endpoints (``CfgPublisher``/``CfgConsumer``)
    and timing to channels, not to the hub.
    """
    template_params: List[TemplateParam] = dc.field(default_factory=list)
    apis: List[ProtocolApi] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class ProtocolConverter(Base):
    """What a converter component converts. A transactor has
    ``from_proto == to_proto`` and a ``role``; an abstractor has two
    protocols and ``role == ""``."""
    from_proto: ProtocolRef = dc.field()
    to_proto: ProtocolRef = dc.field()
    role: str = dc.field(default="")


@dc.dataclass(kw_only=True)
class BundleProtocol(Base):
    """The protocol of a bundle instance at ``target``, and its role
    (``bundle``, ``mirror``, ``monitor``)."""
    target: List[PathElem] = dc.field(default_factory=list)
    protocol: ProtocolRef = dc.field()
    role: str = dc.field()


@dc.dataclass(kw_only=True)
class ApiEndpoint(Base):
    """A port, export or socket of a component.

    - ``kind``: ``port``, ``export``, ``socket`` or ``target_socket``;
    - ``api``: the API's name (port, export), or ``protocol`` (sockets);
    - ``imp``: the path of the implementation set by ``set_imp``; ``[]``
      is derived (the owner).
    """
    name: str = dc.field()
    kind: str = dc.field()
    api: Optional[str] = dc.field(default=None)
    protocol: Optional[ProtocolRef] = dc.field(default=None)
    imp: List[PathElem] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class ApiConnection(Base):
    """An API connection (``kind``: ``connect``, ``connect_up``) from the
    port side ``src`` to the export side ``dst``."""
    kind: str = dc.field(default="connect")
    src: List[PathElem] = dc.field(default_factory=list)
    dst: List[PathElem] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class CfgPublisher(Base):
    """A configuration object a component publishes."""
    name: str = dc.field()
    cfg_type: DataType = dc.field()


@dc.dataclass(kw_only=True)
class CfgConsumer(Base):
    """A configuration object a component reads."""
    name: str = dc.field()
    cfg_type: DataType = dc.field()


@dc.dataclass(kw_only=True)
class CfgConnection(Base):
    """A publisher-to-consumer connection: one writer, the consumer
    updated on each ``set()``. ``derived``: made by elaboration (a sink's
    source, resolved through ``connect_up`` chains), not by an explicit
    call."""
    publisher: List[PathElem] = dc.field(default_factory=list)
    consumer: List[PathElem] = dc.field(default_factory=list)
    derived: bool = dc.field(default=False)


@dc.dataclass(kw_only=True)
class CfgListener(Base):
    """A listener registered on a configuration sink (``add_listener``):
    its ``changed`` runs on each change that reaches ``sink``.
    ``listener`` is the path of the listening object (``[]``: the
    component itself), or None for an object no path reaches."""
    sink: List[PathElem] = dc.field(default_factory=list)
    listener: Optional[List[PathElem]] = dc.field(default_factory=list)
