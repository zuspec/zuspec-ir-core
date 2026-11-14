"""IR unit tests for synchronous-logic field attributes and memory ports."""
import dataclasses as dc

from zuspec.ir.core.fields import Field, FieldInOut
from zuspec.ir.core.data_type import (
    DataTypeComponent, DataTypeInt, DataTypeMemory, ProcessKind,
)


def _u(bits):
    return DataTypeInt(name=f"u{bits}", bits=bits, signed=False)


def test_field_sync_defaults():
    """A plain field has no drive kind, no reset-force and is reset."""
    f = Field(name="q", datatype=_u(8))
    assert f.drive is None
    assert f.reset_force is None
    assert f.no_reset is False


def test_sync_output_reset_forced():
    """A sync output port carries its drive kind and reset-force value."""
    f = FieldInOut(name="out_valid", datatype=_u(1), is_out=True,
                   drive=ProcessKind.SYNC, reset_force=0)
    assert f.drive == ProcessKind.SYNC
    assert f.reset_force == 0
    assert f.is_out


def test_comb_output():
    f = FieldInOut(name="in_ready", datatype=_u(1), is_out=True,
                   drive=ProcessKind.COMB)
    assert f.drive == ProcessKind.COMB
    assert f.reset_force is None


def test_state_not_reset():
    """Payload state that the RTL leaves out of reset."""
    f = Field(name="skid_data", datatype=_u(37), is_reg=True,
              drive=ProcessKind.SYNC, no_reset=True)
    assert f.no_reset
    assert f.reset_value is None


def test_component_ports_and_state():
    """The rung-1 slice shape: ports and state on one component."""
    comp = DataTypeComponent(name="fw_rv_slice", super=None)
    comp.fields.extend([
        FieldInOut(name="in_valid", datatype=_u(1), is_out=False),
        FieldInOut(name="in_ready", datatype=_u(1), is_out=True,
                   drive=ProcessKind.SYNC, reset_force=0),
        FieldInOut(name="out_valid", datatype=_u(1), is_out=True,
                   drive=ProcessKind.SYNC, reset_force=0),
        FieldInOut(name="out_data", datatype=_u(37), is_out=True,
                   drive=ProcessKind.SYNC),
        Field(name="skid_valid", datatype=_u(1), is_reg=True,
              drive=ProcessKind.SYNC, reset_value=0),
        Field(name="skid_data", datatype=_u(37), is_reg=True,
              drive=ProcessKind.SYNC, no_reset=True),
    ])
    forced = [f.name for f in comp.fields if f.reset_force is not None]
    assert forced == ["in_ready", "out_valid"]
    assert [f.name for f in comp.fields if f.no_reset] == ["skid_data"]


def test_field_replace_keeps_sync_attrs():
    f = Field(name="q", datatype=_u(4), drive=ProcessKind.SYNC,
              reset_force=1, no_reset=True)
    g = dc.replace(f, name="q2")
    assert (g.drive, g.reset_force, g.no_reset) == (ProcessKind.SYNC, 1, True)


def test_memory_defaults():
    """Existing users see one read and one write port and no strobe."""
    m = DataTypeMemory(element_type=_u(32), size=256)
    assert (m.read_ports, m.write_ports, m.strobe_bits) == (1, 1, 0)


def test_memory_byte_strobe():
    """The axi_ram shape: words x width, one read port, one byte-strobed write."""
    m = DataTypeMemory(name="mem", element_type=_u(64), size=1 << 12,
                       strobe_bits=8)
    assert m.size == 4096
    assert m.element_type.bits == 64
    assert m.element_type.bits // m.strobe_bits == 8
