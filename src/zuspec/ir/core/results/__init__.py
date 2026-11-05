"""Engine results as IR: facts on anchors, held in overlays (``docs/results.md``)."""
from .fact import (DERIVED, EXACT, MERGED, PARTIAL, QUALITIES, UNMAPPED, Fact, FactError)
from .kinds import (AREA_UNITS, CORE_KINDS, CORE_NAMESPACES, FactKind, latency_range, register,
                    validate)
from .overlay import OVERLAY_FORMAT, OVERLAY_VERSION, PROVENANCES, Overlay, OverlayHeader, rank
from .store import (CONFLICT_KEYS, ORPHAN, STALE, VALID, Conflict, FactView, ResultStore,
                    agrees)
from .bundle import BUNDLE_FORMAT, load_store, read_bundle, write_bundle
