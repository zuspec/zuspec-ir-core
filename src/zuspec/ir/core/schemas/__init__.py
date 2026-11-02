"""JSON Schemas for Zuspec's file formats (docs/formats.md)."""
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))

#: Format name -> schema file.
SCHEMAS = {
    "zuspec.anchor-table": "anchor-table.v2.json",
    "zuspec.overlay": "overlay.v1.json",
    "zuspec.ir-bundle": "ir-bundle.v1.json",
    "zuspec.block-signature": "block-signature.v2.json",
}


def schema(fmt: str) -> dict:
    """The JSON Schema of file format *fmt* (e.g. ``"zuspec.overlay"``)."""
    with open(os.path.join(_DIR, SCHEMAS[fmt])) as f:
        return json.load(f)
