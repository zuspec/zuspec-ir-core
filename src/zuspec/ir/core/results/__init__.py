"""Engine results as IR: facts on anchors, held in overlays (``docs/results.md``)."""
from .fact import (DERIVED, EXACT, MERGED, PARTIAL, QUALITIES, UNMAPPED, Fact, FactError)
from .kinds import CORE_KINDS, CORE_NAMESPACES, FactKind, register, validate
from .overlay import OVERLAY_FORMAT, OVERLAY_VERSION, Overlay, OverlayHeader
from .store import ORPHAN, STALE, VALID, FactView, ResultStore
from .bundle import BUNDLE_FORMAT, load_store, read_bundle, write_bundle
