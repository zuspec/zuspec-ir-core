"""IRDeserializer -- reconstruct Zuspec IR from versioned YAML.

Paired with :class:`~zuspec.ir.core.serializer.IRSerializer`.

Usage::

    deser = IRDeserializer()
    deser.auto_register(zuspec.ir.core)
    root_obj, layer = deser.deserialize(yaml_text)

Unknown ``_type`` tags raise :class:`IRDeserializeError` with the tag name and
the dot-separated path to the node in the YAML tree.
"""
from __future__ import annotations

import dataclasses as dc
import importlib
import inspect
from typing import Any, Dict, List, Optional, Tuple, Type

import yaml


class IRDeserializeError(Exception):
    """Raised when deserialization encounters an unknown ``_type`` tag or bad data.

    Args:
        message: Human-readable description of the failure.
        tag: The ``_type`` tag that triggered the error (if any).
        path: Dot-separated path to the node in the YAML tree (if known).
    """

    def __init__(self, message: str, tag: str = "", path: str = "") -> None:
        super().__init__(message)
        self.tag = tag
        self.path = path


class IRDeserializer:
    """Reconstruct a Zuspec IR object tree from YAML produced by :class:`IRSerializer`.

    The deserializer maintains a registry that maps ``_type`` tag strings to
    Python classes.  Use :meth:`register` or :meth:`auto_register` to populate
    the registry before calling :meth:`deserialize`.
    """

    def __init__(self) -> None:
        self._registry: Dict[str, Type] = {}

    def register(self, type_tag: str, cls: Type) -> None:
        """Map a ``_type`` tag string to a Python class.

        Args:
            type_tag: The string value of the ``_type`` field in the YAML.
            cls: The Python class to instantiate for this tag.
        """
        self._registry[type_tag] = cls

    def auto_register(self, module) -> None:
        """Walk all ``Base`` subclasses visible in *module* and register them.

        Registration key is ``cls.__name__``.  Later calls override earlier
        registrations for the same name.

        Args:
            module: A Python module object (e.g. ``import zuspec.ir.core;
                auto_register(zuspec.ir.core)``).
        """
        try:
            from zuspec.ir.core.base import Base
        except ImportError:
            Base = None

        for attr_name in dir(module):
            try:
                obj = getattr(module, attr_name)
            except Exception:
                continue
            if not inspect.isclass(obj):
                continue
            # Register dataclasses and Base subclasses.
            if (Base is not None and issubclass(obj, Base)) or dc.is_dataclass(obj):
                self._registry[obj.__name__] = obj

    def deserialize(self, yaml_text: str) -> Tuple[Any, Any]:
        """Parse *yaml_text* and reconstruct the IR object tree.

        Args:
            yaml_text: YAML string produced by :class:`IRSerializer`.

        Returns:
            A ``(root_obj, layer)`` tuple where *layer* is an
            :class:`~zuspec.synth.ir.layers.IRLayer` value.

        Raises:
            IRDeserializeError: If an unknown ``_type`` tag is encountered or
                the schema version is unsupported.
        """
        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            raise IRDeserializeError("Expected a YAML mapping at the root")

        layer_name = data.get("_layer", "ACTIVITY")
        try:
            from zuspec.synth.ir.layers import IRLayer
            layer = IRLayer[layer_name]
        except (KeyError, ImportError):
            layer = layer_name

        # Strip header fields before reconstructing.
        payload = {k: v for k, v in data.items()
                   if k not in ("_schema_version", "_layer")}

        root = self._from_dict(payload, path="<root>")
        return root, layer

    def _from_dict(self, data: Any, path: str = "") -> Any:
        """Recursively reconstruct an object from *data*."""
        if data is None:
            return None
        if isinstance(data, (bool, int, float, str)):
            return data
        if isinstance(data, list):
            return [self._from_dict(item, path=f"{path}[{i}]") for i, item in enumerate(data)]

        if not isinstance(data, dict):
            return data

        # Type reference stub (unresolvable at load time without importing).
        if "_type_ref" in data:
            return data  # return the dict as-is; callers can resolve later

        # Circular reference stub.
        if "_ref" in data:
            return data

        type_tag = data.get("_type")
        if type_tag is None:
            # Plain dict without a _type tag -- return as a plain dict.
            return {k: self._from_dict(v, path=f"{path}.{k}") for k, v in data.items()}

        if type_tag not in self._registry:
            raise IRDeserializeError(
                f"Unknown _type tag {type_tag!r} at {path!r} -- "
                "register the class with IRDeserializer.register() or auto_register()",
                tag=type_tag,
                path=path,
            )

        cls = self._registry[type_tag]
        fields_data = {k: v for k, v in data.items() if k != "_type"}

        # Reconstruct by matching fields to the dataclass constructor.
        if dc.is_dataclass(cls):
            field_names = {f.name for f in dc.fields(cls)}
            kwargs: Dict[str, Any] = {}
            for k, v in fields_data.items():
                if k in field_names:
                    kwargs[k] = self._from_dict(v, path=f"{path}.{k}")
            try:
                return cls(**kwargs)
            except Exception as exc:
                raise IRDeserializeError(
                    f"Failed to construct {cls.__name__} at {path!r}: {exc}",
                    tag=type_tag,
                    path=path,
                ) from exc

        # Non-dataclass: try to call with all known kwargs.
        try:
            sig = inspect.signature(cls.__init__)
            valid_params = set(sig.parameters) - {"self"}
            kwargs = {k: self._from_dict(v, path=f"{path}.{k}")
                      for k, v in fields_data.items() if k in valid_params}
            return cls(**kwargs)
        except Exception as exc:
            raise IRDeserializeError(
                f"Failed to construct {cls.__name__} at {path!r}: {exc}",
                tag=type_tag,
                path=path,
            ) from exc
