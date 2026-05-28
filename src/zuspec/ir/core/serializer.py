"""IRSerializer -- convert Zuspec IR objects to versioned YAML.

The serialized format includes two header fields that allow downstream tools
to validate they are reading a compatible snapshot:

.. code-block:: yaml

    _schema_version: "1"
    _layer: "STRUCTURAL"   # one of ACTIVITY | SCHEDULED | PIPELINE | STRUCTURAL
    # ... IR payload ...

``_type`` tags on every node mirror the tags emitted by :class:`JsonConverter`
so that existing tooling (diff scripts, regression comparators) works unchanged.

Type references (Python class objects stored in IR fields) are serialized as
``{"_type_ref": "FullyQualifiedName"}``.  Circular references are detected via
an ``id(obj)`` set and emitted as ``{"_ref": <int>}`` stubs to avoid infinite
recursion.
"""
from __future__ import annotations

import dataclasses as dc
import enum
import inspect
from typing import Any, Dict, Optional, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    pass

_SCHEMA_VERSION = "1"


class IRSerializer:
    """Convert a Zuspec IR object tree to a versioned YAML string.

    Usage::

        from zuspec.synth.ir.layers import IRLayer
        ser = IRSerializer()
        yaml_text = ser.serialize(my_ir_obj, IRLayer.STRUCTURAL)

    Args:
        None
    """

    def serialize(self, obj: Any, layer: Any) -> str:
        """Serialize *obj* to YAML text with schema version and layer headers.

        Args:
            obj: The IR object to serialize (any dataclass or ``Base`` instance).
            layer: An :class:`~zuspec.synth.ir.layers.IRLayer` enum value.

        Returns:
            A YAML string with ``_schema_version`` and ``_layer`` at the root.
        """
        self._seen: set = set()
        root_dict = self._to_dict(obj)
        # Inject header fields at the root.
        layer_name = layer.name if hasattr(layer, "name") else str(layer)
        header = {
            "_schema_version": _SCHEMA_VERSION,
            "_layer": layer_name,
        }
        if isinstance(root_dict, dict):
            merged = {**header, **root_dict}
        else:
            merged = {**header, "_root": root_dict}
        return yaml.dump(merged, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def _to_dict(self, obj: Any) -> Any:
        """Recursively convert *obj* to a YAML-compatible structure."""
        if obj is None:
            return None

        obj_id = id(obj)
        if not isinstance(obj, (str, int, float, bool, type)):
            if obj_id in self._seen:
                return {"_ref": obj_id}
            self._seen.add(obj_id)

        if isinstance(obj, type):
            # Python class reference.
            return {"_type_ref": f"{obj.__module__}.{obj.__qualname__}"}

        if isinstance(obj, bool):
            return obj
        if isinstance(obj, (int, float, str)):
            return obj

        if isinstance(obj, enum.Enum):
            return obj.name

        if isinstance(obj, (list, tuple)):
            return [self._to_dict(item) for item in obj]

        if isinstance(obj, dict):
            return {str(k): self._to_dict(v) for k, v in obj.items()}

        if isinstance(obj, set):
            return [self._to_dict(item) for item in obj]

        if dc.is_dataclass(obj) and not isinstance(obj, type):
            result: Dict[str, Any] = {"_type": type(obj).__name__}
            for f in dc.fields(obj):
                if f.name.startswith("_"):
                    continue
                result[f.name] = self._to_dict(getattr(obj, f.name))
            return result

        # Fallback for arbitrary objects.
        return str(obj)
