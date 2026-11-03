"""docs/formats.md: every file Zuspec writes validates against its JSON Schema."""
import json
import os

import pytest

jsonschema = pytest.importorskip("jsonschema")

import zuspec.ir.core as ir
from zuspec.ir.core.anchor import assign_anchors
from zuspec.ir.core.results import write_bundle
from zuspec.ir.core.schemas import SCHEMAS, schema

from test_anchor import component
from test_results import overlay_for
from test_signature_file import _fn_sig, _sig


def check(d):
    jsonschema.validate(d, schema(d["format"]))


@pytest.mark.parametrize("fmt", sorted(SCHEMAS))
def test_schemas_are_valid(fmt):
    jsonschema.Draft202012Validator.check_schema(schema(fmt))


def test_written_files_validate(tmp_path):
    table = assign_anchors(component())
    check(table.to_json())
    o = overlay_for(table)
    check(o.to_json())
    write_bundle(str(tmp_path / "b.zir"), table, [o])
    for name in ("manifest.json", "anchors.json"):
        with open(tmp_path / "b.zir" / name) as f:
            check(json.load(f))
    sig = _sig()
    sig.channels[0].stage, sig.channels[0].flop_kind = 0, "flop"
    check(ir.signature_to_json(sig))
    check(ir.signature_to_json(_fn_sig()))


def test_schemas_reject_wrong_versions_and_qualities():
    table = assign_anchors(component())
    d = table.to_json()
    d["version"] = 1
    with pytest.raises(jsonschema.ValidationError):
        check(d)
    o = overlay_for(table).to_json()
    o["facts"]["sched.stage"]["quality"][0] = "approximate"
    with pytest.raises(jsonschema.ValidationError):
        check(o)
