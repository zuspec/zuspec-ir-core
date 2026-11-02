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
class BlockSignature(Base):
    """The RTL-level interface of one block."""
    name: str = dc.field()
    channels: List[ChannelSignature] = dc.field(default_factory=list)
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


# ---------------------------------------------------------------------------
# File form (JSON). A block reaches the integration step as a signature file,
# whatever produced it: an engine, or a hand-written module (xls-phase2.md
# SIG-1, Rule 2).
# ---------------------------------------------------------------------------

SIGNATURE_FORMAT = "zuspec.block-signature"
SIGNATURE_VERSION = 2
#: Versions this reader accepts. Version 2 added channel ``stage`` and
#: ``flop_kind`` and struct and array payloads; a version-1 file reads as is.
_READABLE_VERSIONS = (1, 2)


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
        "name": sig.name, "engine": sig.engine, "sources": list(sig.sources),
        "clock": sig.clock, "reset": sig.reset, "reset_active_low": sig.reset_active_low,
        "latency": sig.latency, "initiation_interval": sig.initiation_interval,
        "channels": [{"name": c.name, "direction": c.direction,
                      "flow_control": c.flow_control, "ports": dict(c.ports),
                      "payload": _payload_to_json(c.payload),
                      "payload_sv_type": c.payload_sv_type,
                      "stage": c.stage, "flop_kind": c.flop_kind} for c in sig.channels],
    }


def signature_from_json(d: Dict[str, Any]) -> BlockSignature:
    if d.get("format") != SIGNATURE_FORMAT:
        raise ValueError(f"not a block signature (format={d.get('format')!r})")
    if d.get("version") not in _READABLE_VERSIONS:
        raise ValueError(f"block signature version {d.get('version')!r}; "
                         f"this reader knows {list(_READABLE_VERSIONS)}")
    return BlockSignature(
        name=d["name"], engine=d.get("engine"), sources=list(d.get("sources", [])),
        clock=d.get("clock"), reset=d.get("reset"),
        reset_active_low=bool(d.get("reset_active_low", False)),
        latency=d.get("latency"), initiation_interval=d.get("initiation_interval"),
        channels=[ChannelSignature(name=c["name"], direction=c["direction"],
                                   flow_control=c.get("flow_control", "ready_valid"),
                                   ports=dict(c.get("ports", {})),
                                   payload=_payload_from_json(c["payload"]),
                                   payload_sv_type=c.get("payload_sv_type"),
                                   stage=c.get("stage"), flop_kind=c.get("flop_kind"))
                  for c in d.get("channels", [])])


def save_signature(sig: BlockSignature, path: str) -> None:
    with open(path, "w") as f:
        json.dump(signature_to_json(sig), f, indent=2)
        f.write("\n")


def load_signature(path: str) -> BlockSignature:
    with open(path) as f:
        return signature_from_json(json.load(f))
