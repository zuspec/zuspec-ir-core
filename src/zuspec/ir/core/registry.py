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

3. **Name-keyed external entry** — a class written in another language (an SV
   class, say) that a front end recognizes by its generic name and parameters.
   Use :meth:`LoweringRegistry.register_external` and
   :meth:`LoweringRegistry.lookup`. Each entry declares the parameters it
   accepts; a lookup with an unknown parameter, or a value the entry does not
   accept, raises :class:`RecognitionError` rather than silently falling back.
   Targets add ``xls``, ``native``, ``extern`` and ``zuspec`` to the four
   above. A ``zuspec`` lowering builds Zuspec IR (a component) from the
   parameters, which any backend can then lower.

A plugin that fails to load does not break a run that uses only the
class-keyed API. It is recorded in :attr:`LoweringRegistry.plugin_errors`, and
:meth:`LoweringRegistry.lookup` raises :class:`PluginLoadError` while any is
recorded: a recognized class whose plugin failed must not lower as if it were
unknown.
"""

import dataclasses as _dc
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from .interfaces import (
    ElaboratableInterface,
    SVEmittableInterface,
    SVAEmittableInterface,
    CSimEmittableInterface,
)

_VALID_TARGETS = frozenset({"sv", "fv", "csim", "elab"})
#: Targets a name-keyed entry may use.
EXTERNAL_TARGETS = _VALID_TARGETS | frozenset({"xls", "native", "extern", "zuspec"})


class RecognitionError(ValueError):
    """A recognized class's parameters are not ones its entry accepts."""


class PluginLoadError(RuntimeError):
    """A ``zuspec.lowering`` plugin failed to load."""


@_dc.dataclass(frozen=True)
class ExternalEntry:
    """A name-keyed lowering (pattern 3).

    ``params`` maps each parameter the entry accepts to what it accepts:
    a Python type (``int``, ``str``: any value of it) or a collection of the
    values allowed. ``lowering`` is target-specific; the registry only stores it.
    """
    name: str
    target: str
    params: Mapping[str, Any]
    lowering: Any
    origin: str = ""

    def check(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        """*params*, validated against :attr:`params`."""
        out: Dict[str, Any] = {}
        for k, v in params.items():
            if k not in self.params:
                raise RecognitionError(
                    f"{self.name}: parameter {k!r} is not one its {self.target} lowering "
                    f"takes (it takes {sorted(self.params)})")
            want = self.params[k]
            if isinstance(want, type):
                if want is int and isinstance(v, bool) or not isinstance(v, want):
                    raise RecognitionError(
                        f"{self.name}: parameter {k}={v!r} is not a {want.__name__}")
            elif v not in want:
                raise RecognitionError(
                    f"{self.name}: {k}={v!r} has no {self.target} lowering "
                    f"(supported: {', '.join(map(str, want))})")
            out[k] = v
        return out


@_dc.dataclass(frozen=True)
class Recognized:
    """What :meth:`LoweringRegistry.lookup` returns: the entry and the
    validated parameters."""
    entry: ExternalEntry
    params: Dict[str, Any]

    @property
    def lowering(self) -> Any:
        return self.entry.lowering


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

        # Name-keyed external entries: (name, target) → entry
        self._external: Dict[Tuple[str, str], ExternalEntry] = {}
        #: (entry-point name, exception) for each plugin that failed to load
        self.plugin_errors: List[Tuple[str, BaseException]] = []

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

    def register_external(
        self,
        name: str,
        params: Mapping[str, Any],
        target: str,
        lowering: Any,
        *,
        origin: str = "",
        replace: bool = False,
    ) -> ExternalEntry:
        """Register a lowering for the class named *name* (its generic name,
        without parameters) on *target*. *params* is what each parameter may
        be (see :class:`ExternalEntry`). Registering the same (name, target)
        twice is an error unless *replace*."""
        if target not in EXTERNAL_TARGETS:
            raise ValueError(f"Unknown target {target!r}. "
                             f"Must be one of {sorted(EXTERNAL_TARGETS)}.")
        key = (name, target)
        if key in self._external and not replace:
            prev = self._external[key]
            raise ValueError(f"{name!r} already has a {target} lowering"
                             + (f" (from {prev.origin})" if prev.origin else ""))
        e = ExternalEntry(name, target, dict(params), lowering, origin)
        self._external[key] = e
        return e

    def lookup(self, name: str, params: Mapping[str, Any],
               target: str) -> Optional[Recognized]:
        """The entry for *name* on *target*, with *params* checked, or None
        when there is none. Raises :class:`RecognitionError` for parameters
        the entry does not accept, and :class:`PluginLoadError` while a
        plugin failure is recorded."""
        if self.plugin_errors:
            raise PluginLoadError("; ".join(f"{n}: {type(e).__name__}: {e}"
                                            for n, e in self.plugin_errors))
        e = self._external.get((name, target))
        if e is None:
            return None
        return Recognized(e, e.check(params))

    def externals(self, target: Optional[str] = None) -> List[ExternalEntry]:
        """Every name-keyed entry (on *target*, if given), by name."""
        return sorted((e for (n, t), e in self._external.items()
                       if target is None or t == target), key=lambda e: (e.name, e.target))

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
        except Exception as e:
            # A run that never looks a name up is not broken by it; lookup()
            # reports it (see the module docstring).
            registry.plugin_errors.append((ep.name, e))


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
