"""The IR bundle: a directory holding a model's anchors, overlays and,
optionally, the model itself::

    <dir>/manifest.json        format zuspec.ir-bundle
    <dir>/anchors.json
    <dir>/overlays/<engine>-<id>.json
    <dir>/model.yaml           (optional)

Tools that only report (CI diffs, viewers, editor hints) read the anchors and
overlays without loading the model or any engine.
"""
from __future__ import annotations

import json
import os
from typing import Iterable, List, Optional, Tuple

from ..anchor import AnchorTable
from .overlay import Overlay
from .store import ResultStore

BUNDLE_FORMAT = "zuspec.ir-bundle"
BUNDLE_VERSION = 1


def write_bundle(path: str, anchors: AnchorTable, overlays: Iterable[Overlay] = (),
                 model_yaml: Optional[str] = None) -> str:
    """Write (or add to) the bundle at *path*; returns the manifest's path.
    Overlays already in the bundle are kept."""
    os.makedirs(os.path.join(path, "overlays"), exist_ok=True)
    anchors.save(os.path.join(path, "anchors.json"))
    for o in overlays:
        o.save(os.path.join(path, "overlays", o.filename))
    if model_yaml is not None:
        with open(os.path.join(path, "model.yaml"), "w") as f:
            f.write(model_yaml)
    manifest = {
        "format": BUNDLE_FORMAT, "version": BUNDLE_VERSION,
        "anchors": "anchors.json",
        "overlays": sorted(os.path.join("overlays", n)
                           for n in os.listdir(os.path.join(path, "overlays"))
                           if n.endswith(".json")),
        "model": "model.yaml" if os.path.exists(os.path.join(path, "model.yaml")) else None,
    }
    mpath = os.path.join(path, "manifest.json")
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    return mpath


def read_bundle(path: str) -> Tuple[AnchorTable, List[Overlay], dict]:
    with open(os.path.join(path, "manifest.json")) as f:
        m = json.load(f)
    if m.get("format") != BUNDLE_FORMAT:
        raise ValueError(f"not an IR bundle (format {m.get('format')!r})")
    if m.get("version") != BUNDLE_VERSION:
        raise ValueError(f"unsupported IR bundle version {m.get('version')!r}")
    anchors = AnchorTable.load(os.path.join(path, m["anchors"]))
    overlays = [Overlay.load(os.path.join(path, p)) for p in m.get("overlays", [])]
    return anchors, overlays, m


def load_store(path: str) -> ResultStore:
    """A :class:`ResultStore` over everything in the bundle at *path*."""
    anchors, overlays, _ = read_bundle(path)
    store = ResultStore(anchors)
    for o in overlays:
        store.add(o)
    return store
