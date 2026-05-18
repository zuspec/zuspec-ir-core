"""
LoweringRegistry — central dispatch table for abstraction lowering models.

The registry maps abstraction classes (by name **and** by type) to their
lowering models for each synthesis target:

* ``sv``   — :class:`~zuspec.ir.core.interfaces.SVEmittableInterface`
* ``fv``   — :class:`~zuspec.ir.core.interfaces.SVAEmittableInterface`
* ``csim`` — :class:`~zuspec.ir.core.interfaces.CSimEmittableInterface`
* ``elab`` — :class:`~zuspec.ir.core.interfaces.ElaboratableInterface`

Design note: analogous to MLIR's ``DialectRegistry``.  Two registration
patterns are supported:

1. **Inline model** — the abstraction class implements interfaces directly
   (e.g. ``Counter`` inherits ``SVEmittableInterface``).  Use
   :meth:`LoweringRegistry.register`.

2. **External model** — a third-party package attaches a model to a class it
   does not own (MLIR ``ExternalModel`` pattern).  Use
   :meth:`LoweringRegistry.attach_external_model`.

Third-party packages may also discover registration hooks at import time
through ``importlib.metadata`` entry points in the group ``"zuspec.lowering"``:

.. code-block:: toml

    # In a third-party package's pyproject.toml
    [project.entry-points."zuspec.lowering"]
    "my_fpga_primitives" = "my_pkg.zuspec_plugin:register_models"

Each entry point is expected to be a callable that accepts one argument — the
:class:`LoweringRegistry` singleton — and calls
:meth:`~LoweringRegistry.attach_external_model` or
:meth:`~LoweringRegistry.register` as needed.
"""

from typing import Any, Dict, Optional

from .interfaces import (
    ElaboratableInterface,
    SVEmittableInterface,
    SVAEmittableInterface,
    CSimEmittableInterface,
)

_VALID_TARGETS = frozenset({"sv", "fv", "csim", "elab"})


class LoweringRegistry:
    """Central registry mapping abstraction classes to their lowering models.

    String-keyed dicts are the primary storage (``spec_type_name`` → model).
    Type-keyed caches accelerate repeated runtime dispatches.
    """

    def __init__(self) -> None:
        # Primary storage: spec_type_name (str) → model
        self._sv_models:   Dict[str, Any] = {}
        self._fv_models:   Dict[str, Any] = {}
        self._csim_models: Dict[str, Any] = {}
        self._elab_models: Dict[str, Any] = {}

        # Type-keyed caches: type → model  (populated lazily on first get_* call)
        self._sv_cache:   Dict[type, Any] = {}
        self._fv_cache:   Dict[type, Any] = {}
        self._csim_cache: Dict[type, Any] = {}
        self._elab_cache: Dict[type, Any] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, cls: type) -> None:
        """Register a class that implements interfaces directly (inline model).

        Walks through all known interfaces and records *cls* under its own name.
        Clears the type-keyed cache entry for *cls* so stale lookups are not
        served.
        """
        name = cls.__name__
        if issubclass(cls, ElaboratableInterface):
            self._elab_models[name] = cls
            self._elab_cache.pop(cls, None)
        if issubclass(cls, SVEmittableInterface):
            self._sv_models[name] = cls
            self._sv_cache.pop(cls, None)
        if issubclass(cls, SVAEmittableInterface):
            self._fv_models[name] = cls
            self._fv_cache.pop(cls, None)
        if issubclass(cls, CSimEmittableInterface):
            self._csim_models[name] = cls
            self._csim_cache.pop(cls, None)

    def attach_external_model(
        self,
        cls: type,
        target: str,
        model: Any,
    ) -> None:
        """Retroactively attach a lowering model to an existing class.

        MLIR ``ExternalModel`` pattern: a third-party package can register a
        model for a class in ``zuspec-dataclasses`` without forking or editing
        ``zuspec-dataclasses``.

        Parameters
        ----------
        cls:
            The existing class (e.g. ``Counter`` from ``zuspec-dataclasses``).
        target:
            One of ``'sv'``, ``'fv'``, ``'csim'``, ``'elab'``.
        model:
            Object (or class) implementing the target interface.

        Raises
        ------
        ValueError
            If *target* is not one of the recognised target names.
        """
        if target not in _VALID_TARGETS:
            raise ValueError(
                f"Unknown target {target!r}. "
                f"Must be one of {sorted(_VALID_TARGETS)}."
            )
        name = cls.__name__
        if target == "sv":
            self._sv_models[name] = model
            self._sv_cache.pop(cls, None)
        elif target == "fv":
            self._fv_models[name] = model
            self._fv_cache.pop(cls, None)
        elif target == "csim":
            self._csim_models[name] = model
            self._csim_cache.pop(cls, None)
        elif target == "elab":
            self._elab_models[name] = model
            self._elab_cache.pop(cls, None)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get_sv_model(self, cls_or_name) -> Optional[Any]:
        """Return the registered SV model for *cls_or_name*, or ``None``."""
        return self._get(_sv_models=True, cls_or_name=cls_or_name)

    def get_fv_model(self, cls_or_name) -> Optional[Any]:
        """Return the registered formal (FV) model for *cls_or_name*, or ``None``."""
        return self._get(_fv_models=True, cls_or_name=cls_or_name)

    def get_csim_model(self, cls_or_name) -> Optional[Any]:
        """Return the registered C-sim model for *cls_or_name*, or ``None``."""
        return self._get(_csim_models=True, cls_or_name=cls_or_name)

    def get_elab_model(self, cls_or_name) -> Optional[Any]:
        """Return the registered elaboration model for *cls_or_name*, or ``None``."""
        return self._get(_elab_models=True, cls_or_name=cls_or_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(
        self,
        cls_or_name,
        *,
        _sv_models: bool = False,
        _fv_models: bool = False,
        _csim_models: bool = False,
        _elab_models: bool = False,
    ) -> Optional[Any]:
        if _sv_models:
            store, cache = self._sv_models, self._sv_cache
        elif _fv_models:
            store, cache = self._fv_models, self._fv_cache
        elif _csim_models:
            store, cache = self._csim_models, self._csim_cache
        else:
            store, cache = self._elab_models, self._elab_cache

        if isinstance(cls_or_name, str):
            return store.get(cls_or_name)

        # Type-keyed fast path
        if cls_or_name in cache:
            return cache[cls_or_name]

        model = store.get(cls_or_name.__name__)
        if model is not None:
            cache[cls_or_name] = model
        return model


# ---------------------------------------------------------------------------
# Module-level singleton with lazy plugin loading
# ---------------------------------------------------------------------------

_global_registry: Optional[LoweringRegistry] = None
_plugins_loaded: bool = False


def _load_plugins(registry: LoweringRegistry) -> None:
    """Discover and invoke entry-point registration hooks.

    Loads all entry points in the ``"zuspec.lowering"`` group.  Each entry
    point is expected to be a callable that accepts the registry as its sole
    argument.
    """
    import importlib.metadata as _meta

    try:
        eps = _meta.entry_points(group="zuspec.lowering")
    except TypeError:
        # Python < 3.12: entry_points() does not accept group=
        eps = _meta.entry_points().get("zuspec.lowering", [])

    for ep in eps:
        try:
            fn = ep.load()
            fn(registry)
        except Exception:
            pass  # plugin failures must not break the synthesis run


def global_registry() -> LoweringRegistry:
    """Return the module-level :class:`LoweringRegistry` singleton.

    On the first call, the registry is created and all installed
    ``"zuspec.lowering"`` entry-point hooks are invoked.
    """
    global _global_registry, _plugins_loaded
    if _global_registry is None:
        _global_registry = LoweringRegistry()
    if not _plugins_loaded:
        _plugins_loaded = True
        _load_plugins(_global_registry)
    return _global_registry
