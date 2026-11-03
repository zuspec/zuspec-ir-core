"""Engine-neutral block signature: what the RTL a back-end engine produces for a
component looks like from outside.

Both engines fill one in: the native scheduler (zuspec-synth) and XLS
(zuspec-be-xls). The glue generator and the transactors work from it alone,
so they do not need to know which engine built the block
(``killer-app.md`` §4, ``xls-phase0.md`` R9).
"""
from __future__ import annotations

import dataclasses as dc
import json
from typing import Any, Dict, List, Optional

from .base import Base
from .data_type import DataType, DataTypeArray, DataTypeInt, DataTypeStruct
from .fields import Field


@dc.dataclass(kw_only=True)
class ChannelSignature(Base):
    """One streaming channel of a block."""
    name: str = dc.field()
    #: "in" (the block receives) or "out" (the block sends).
    direction: str = dc.field()
    payload: DataType = dc.field()
    #: "ready_valid", "valid" or "none".
    flow_control: str = dc.field(default="ready_valid")
    #: RTL port name for each role: "data", "valid", "ready".
    ports: Dict[str, str] = dc.field(default_factory=dict)
    #: The payload type as the source language spells it (e.g. an SV typedef),
    #: for generated transactors and glue; None when unknown.
    payload_sv_type: Optional[str] = dc.field(default=None)
    #: Pipeline stage of the block's operation on this channel; None when
    #: unknown or not pipelined.
    stage: Optional[int] = dc.field(default=None)
    #: I/O flopping of the channel's ports: "none", "flop", "skid" or
    #: "zero_latency" (XLS's FlopKind); None when unknown.
    flop_kind: Optional[str] = dc.field(default=None)


@dc.dataclass(kw_only=True)
class DataPortSignature(Base):
    """One plain data port of a function block (no handshake of its own)."""
    name: str = dc.field()
    #: "in" or "out".
    direction: str = dc.field()
    type: DataType = dc.field()
    #: The type as the source language spells it; None when unknown.
    sv_type: Optional[str] = dc.field(default=None)


#: Block kinds: a proc talks through channels; a function block takes its
#: arguments on data ports and, when pipelined, qualifies them with the
#: ``valid_ports`` roles ``input_valid`` and ``output_valid``.
BLOCK_KINDS = ("proc", "function")


@dc.dataclass(kw_only=True)
class BlockSignature(Base):
    """The RTL-level interface of one block."""
    name: str = dc.field()
    #: "proc" or "function" (BLOCK_KINDS).
    kind: str = dc.field(default="proc")
    channels: List[ChannelSignature] = dc.field(default_factory=list)
    #: A function block's argument and result ports.
    data_ports: List[DataPortSignature] = dc.field(default_factory=list)
    #: A function block's valid pins by role: "input_valid", "output_valid".
    valid_ports: Dict[str, str] = dc.field(default_factory=dict)
    clock: Optional[str] = dc.field(default=None)
    reset: Optional[str] = dc.field(default=None)
    reset_active_low: bool = dc.field(default=False)
    #: Cycles from input to output, when fixed; None when unknown or variable.
    latency: Optional[int] = dc.field(default=None)
    #: Cycles between activations; None when unknown.
    initiation_interval: Optional[int] = dc.field(default=None)
    #: Which engine produced (or will produce) the block, e.g. "xls".
    engine: Optional[str] = dc.field(default=None)
    #: The files that define the module, when the block is a module reference.
    sources: List[str] = dc.field(default_factory=list)

    def channel(self, name: str) -> ChannelSignature:
        for c in self.channels:
            if c.name == name:
                return c
        raise KeyError(name)

    def data_port(self, name: str) -> DataPortSignature:
        for p in self.data_ports:
            if p.name == name:
                return p
        raise KeyError(name)


# ---------------------------------------------------------------------------
# File form (JSON). A block reaches the integration step as a signature file,
# whatever produced it: an engine, or a hand-written module (xls-phase2.md
# SIG-1, Rule 2).
# ---------------------------------------------------------------------------

SIGNATURE_FORMAT = "zuspec.block-signature"
SIGNATURE_VERSION = 3
#: Versions this reader accepts. Version 2 added channel ``stage`` and
#: ``flop_kind`` and struct and array payloads. Version 3 added ``kind``,
#: ``data_ports`` and ``valid_ports`` for function blocks; a version-2 reader
#: would see a function block as having no pins, hence the bump. Version-1
#: and -2 files read as procs.
_READABLE_VERSIONS = (1, 2, 3)


def _payload_to_json(t: DataType) -> Dict[str, Any]:
    if isinstance(t, DataTypeInt):
        return {"kind": "int", "bits": t.bits, "signed": t.signed}
    if isinstance(t, DataTypeArray):
        return {"kind": "array", "size": t.size, "element": _payload_to_json(t.element_type)}
    if isinstance(t, DataTypeStruct):
        return {"kind": "struct", "name": t.name,
                "fields": [{"name": f.name, "type": _payload_to_json(f.datatype)}
                           for f in t.fields]}
    raise TypeError(f"no file form for payload type {type(t).__name__}")


def _payload_from_json(d: Dict[str, Any]) -> DataType:
    kind = d.get("kind")
    if kind == "int":
        return DataTypeInt(bits=int(d["bits"]), signed=bool(d.get("signed", False)))
    if kind == "array":
        return DataTypeArray(size=int(d["size"]), element_type=_payload_from_json(d["element"]))
    if kind == "struct":
        return DataTypeStruct(name=d.get("name"), super=None,
                              fields=[Field(name=f["name"], datatype=_payload_from_json(f["type"]))
                                      for f in d.get("fields", [])])
    raise ValueError(f"unknown payload kind {kind!r}")


def signature_to_json(sig: BlockSignature) -> Dict[str, Any]:
    return {
        "format": SIGNATURE_FORMAT, "version": SIGNATURE_VERSION,
        "name": sig.name, "kind": sig.kind,
        "engine": sig.engine, "sources": list(sig.sources),
        "clock": sig.clock, "reset": sig.reset, "reset_active_low": sig.reset_active_low,
        "latency": sig.latency, "initiation_interval": sig.initiation_interval,
        "channels": [{"name": c.name, "direction": c.direction,
                      "flow_control": c.flow_control, "ports": dict(c.ports),
                      "payload": _payload_to_json(c.payload),
                      "payload_sv_type": c.payload_sv_type,
                      "stage": c.stage, "flop_kind": c.flop_kind} for c in sig.channels],
        "data_ports": [{"name": p.name, "direction": p.direction,
                        "type": _payload_to_json(p.type), "sv_type": p.sv_type}
                       for p in sig.data_ports],
        "valid_ports": dict(sig.valid_ports),
    }


def signature_from_json(d: Dict[str, Any]) -> BlockSignature:
    if d.get("format") != SIGNATURE_FORMAT:
        raise ValueError(f"not a block signature (format={d.get('format')!r})")
    if d.get("version") not in _READABLE_VERSIONS:
        raise ValueError(f"block signature version {d.get('version')!r}; "
                         f"this reader knows {list(_READABLE_VERSIONS)}")
    kind = d.get("kind", "proc")
    if kind not in BLOCK_KINDS:
        raise ValueError(f"block signature kind {kind!r}; expected one of {list(BLOCK_KINDS)}")
    return BlockSignature(
        name=d["name"], kind=kind,
        engine=d.get("engine"), sources=list(d.get("sources", [])),
        clock=d.get("clock"), reset=d.get("reset"),
        reset_active_low=bool(d.get("reset_active_low", False)),
        latency=d.get("latency"), initiation_interval=d.get("initiation_interval"),
        channels=[ChannelSignature(name=c["name"], direction=c["direction"],
                                   flow_control=c.get("flow_control", "ready_valid"),
                                   ports=dict(c.get("ports", {})),
                                   payload=_payload_from_json(c["payload"]),
                                   payload_sv_type=c.get("payload_sv_type"),
                                   stage=c.get("stage"), flop_kind=c.get("flop_kind"))
                  for c in d.get("channels", [])],
        data_ports=[DataPortSignature(name=p["name"], direction=p["direction"],
                                      type=_payload_from_json(p["type"]),
                                      sv_type=p.get("sv_type"))
                    for p in d.get("data_ports", [])],
        valid_ports=dict(d.get("valid_ports", {})))


def save_signature(sig: BlockSignature, path: str) -> None:
    with open(path, "w") as f:
        json.dump(signature_to_json(sig), f, indent=2)
        f.write("\n")


def load_signature(path: str) -> BlockSignature:
    with open(path) as f:
        return signature_from_json(json.load(f))
