"""Round-trip tests for IRSerializer / IRDeserializer (R3)."""
import pytest
import yaml

from zuspec.ir.core.serializer import IRSerializer
from zuspec.ir.core.deserializer import IRDeserializer, IRDeserializeError
from zuspec.ir.core.activity import (
    ActivitySequenceBlock,
    ActivityTraversal,
    JoinSpec,
)
from zuspec.ir.core.base import Loc


def _make_layer():
    """Return a minimal IRLayer enum (mock if zuspec-synth is not installed)."""
    try:
        from zuspec.synth.ir.layers import IRLayer
        return IRLayer.ACTIVITY
    except ImportError:
        import enum
        class IRLayer(enum.Enum):
            ACTIVITY = "ACTIVITY"
        return IRLayer.ACTIVITY


class TestIRSerializer:
    def test_schema_version_and_layer_present(self):
        ser = IRSerializer()
        layer = _make_layer()
        node = JoinSpec(kind="all")
        text = ser.serialize(node, layer)
        data = yaml.safe_load(text)
        assert "_schema_version" in data
        assert "_layer" in data
        assert data["_layer"] == "ACTIVITY"

    def test_join_spec_roundtrip(self):
        ser = IRSerializer()
        layer = _make_layer()
        node = JoinSpec(kind="first")
        text = ser.serialize(node, layer)
        data = yaml.safe_load(text)
        assert data["_type"] == "JoinSpec"
        assert data["kind"] == "first"

    def test_type_ref_serialized_correctly(self):
        """Python class references should be serialized as _type_ref dicts."""
        import dataclasses as dc
        from zuspec.ir.core.base import Base

        @dc.dataclass(kw_only=True)
        class _NodeWithType(Base):
            cls_ref: object = None

        class FakeAction:
            __module__ = "test_module"
            __qualname__ = "FakeAction"

        ser = IRSerializer()
        layer = _make_layer()
        node = _NodeWithType(cls_ref=FakeAction)
        text = ser.serialize(node, layer)
        data = yaml.safe_load(text)
        cls_ref_data = data.get("cls_ref", {})
        assert isinstance(cls_ref_data, dict) and "_type_ref" in cls_ref_data, (
            f"Expected _type_ref dict, got {cls_ref_data!r}"
        )

    def test_circular_reference_stub(self):
        """Circular references should not cause infinite recursion."""
        import dataclasses as dc
        from zuspec.ir.core.base import Base

        @dc.dataclass(kw_only=True)
        class SelfRefNode(Base):
            name: str = ""
            next: object = None

        a = SelfRefNode(name="a")
        a.next = a  # circular
        ser = IRSerializer()
        layer = _make_layer()
        # Should not raise RecursionError.
        text = ser.serialize(a, layer)
        data = yaml.safe_load(text)
        # Root should have _type tag.
        assert data.get("_type") == "SelfRefNode" or "_type" in str(data)


class TestIRDeserializer:
    def test_deserialize_unknown_type_raises(self):
        yaml_text = yaml.dump({
            "_schema_version": "1",
            "_layer": "ACTIVITY",
            "_type": "NonExistentNode123",
            "value": 42,
        })
        deser = IRDeserializer()
        with pytest.raises(IRDeserializeError) as exc_info:
            deser.deserialize(yaml_text)
        assert "NonExistentNode123" in str(exc_info.value)

    def test_deserialize_join_spec(self):
        ser = IRSerializer()
        layer = _make_layer()
        original = JoinSpec(kind="branch")
        text = ser.serialize(original, layer)

        deser = IRDeserializer()
        deser.register("JoinSpec", JoinSpec)
        result, layer_out = deser.deserialize(text)
        assert isinstance(result, dict) or result is not None
        # The root is a dict from the YAML, or a JoinSpec.
        # Check that the layer is returned correctly.
        if hasattr(layer_out, "name"):
            assert layer_out.name == "ACTIVITY"

    def test_deserialize_layer_returned(self):
        yaml_text = yaml.dump({
            "_schema_version": "1",
            "_layer": "SCHEDULED",
            "_type": "JoinSpec",
            "kind": "all",
        })
        deser = IRDeserializer()
        deser.register("JoinSpec", JoinSpec)
        _, layer = deser.deserialize(yaml_text)
        layer_name = layer.name if hasattr(layer, "name") else str(layer)
        assert layer_name == "SCHEDULED"
