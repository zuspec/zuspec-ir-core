"""ZIR-1: ``Base.annotations`` -- source annotations on any IR node."""
import dataclasses as dc

from zuspec.ir.core import Annotation, AnnotationEnum, Base, DataTypeEnum
from zuspec.ir.core.visitor import Visitor


def test_default_is_empty_and_not_shared():
    a, b = DataTypeEnum(name="e"), DataTypeEnum(name="e")
    assert a.annotations == [] and b.annotations == []
    a.annotations.append(Annotation(type_qname="p::a_s"))
    assert b.annotations == []


def test_equality_and_repr_ignore_annotations():
    plain = DataTypeEnum(name="e", items={"A": 0})
    annotated = DataTypeEnum(name="e", items={"A": 0},
                             annotations=[Annotation(type_qname="p::a_s")])
    assert plain == annotated
    assert "annotations" not in repr(annotated)


def test_every_node_kind_has_the_slot():
    assert "annotations" in {f.name for f in dc.fields(Base)}


def test_survives_dataclasses_replace():
    a = DataTypeEnum(name="e", annotations=[Annotation(type_qname="p::a_s")])
    assert dc.replace(a, name="f").annotations[0].type_qname == "p::a_s"


def test_visit_default_does_not_walk_into_annotations():
    """An Annotation is metadata, not a Base: visitDefault passes over it, so
    no visitor needs a visitAnnotation."""
    from zuspec.ir.core import DataTypeInt, Field
    called = []

    class Recorder:
        def __getattr__(self, name):
            if not name.startswith("visit"):
                raise AttributeError(name)
            return lambda o: called.append(name)

    fld = Field(name="f", datatype=DataTypeInt(bits=8),
                annotations=[Annotation(
                    type_qname="p::a_s",
                    values={"m": AnnotationEnum("p::m_e", "A", 0)})])
    fld.visitDefault(Recorder())
    assert "visitDataTypeInt" in called
    assert not [c for c in called if "Annotation" in c]
