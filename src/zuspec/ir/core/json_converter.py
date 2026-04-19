from __future__ import annotations
import dataclasses as dc
import enum
import logging
from typing import Any, ClassVar, Dict, List, Optional, Type

from .base import Base, BaseP
from .profile_rgy import ProfileRgy


class JsonConverter:
    """Dynamically converts objects of Base type to JSON descriptions.
    
    Similar to the Visitor pattern, this class dynamically builds conversion
    methods for each Base-derived class in the profile. If a subclass provides 
    a custom `convert<ClassName>` method, it will be used; otherwise a default 
    conversion based on dataclass fields is applied.
    
    The converter walks the type hierarchy to find the most specific custom
    converter method available, falling back to default dataclass-based conversion.
    """
    _type_impl_m: Dict[Type['JsonConverter'], Type['JsonConverter']] = {}
    _log: ClassVar = logging.getLogger("zuspec.ir.JsonConverter")

    def __new__(cls, pmod) -> 'JsonConverter':
        if pmod is None:
            return super().__new__(cls)
        elif cls in JsonConverter._type_impl_m.keys():
            return JsonConverter._type_impl_m[cls].__new__(cls, None)
        else:
            # Build out the implementation dynamically
            # Create convert methods for Base-derived types in the profile
            fields = {}
            
            profile = ProfileRgy.get_profile(pmod)
            for t in profile.types:
                method_name = "convert%s" % t.__name__
                if hasattr(cls, method_name):
                    # Use the custom implementation
                    fields[method_name] = getattr(cls, method_name)
                else:
                    # Create a default implementation
                    fields[method_name] = lambda self, o, target_type=t: self._convertDefault(o, target_type)

            fields["__new__"] = lambda cls, pmod=None: super().__new__(cls)

            impl: Type[JsonConverter] = type(
                cls.__qualname__,
                (cls,),
                fields)

            JsonConverter._type_impl_m[cls] = impl
            return impl(None)

    def convert(self, obj: Base) -> Dict[str, Any]:
        """Convert a Base object to a JSON-compatible dictionary.
        
        Args:
            obj: The Base object to convert
            
        Returns:
            A dictionary representation suitable for JSON serialization
        """
        if obj is None:
            return None
        
        obj_type = type(obj)
        
        # Walk up the type hierarchy to find the best converter
        for cls in obj_type.__mro__:
            method_name = "convert%s" % cls.__name__
            if hasattr(self, method_name):
                return getattr(self, method_name)(obj)
            # Stop at Base - don't go past our base marker
            if cls is Base:
                break
        
        # Fallback to default conversion
        return self._convertDefault(obj, obj_type)

    def _convertDefault(self, obj: Base, cls: Type) -> Dict[str, Any]:
        """Default conversion for Base objects using dataclass fields."""
        self._log.debug("_convertDefault for %s (type=%s)" % (cls.__name__, type(obj).__name__))
        
        result = {
            "_type": type(obj).__name__
        }
        
        # If it's a dataclass, iterate through fields
        if dc.is_dataclass(obj):
            for f in dc.fields(obj):
                # Skip internal fields
                if f.name.startswith('_'):
                    continue
                
                value = getattr(obj, f.name)
                result[f.name] = self._convertValue(value)
        
        return result

    def _convertValue(self, value: Any) -> Any:
        """Convert a value to JSON-compatible format."""
        if value is None:
            return None
        elif isinstance(value, BaseP):
            return self.convert(value)
        elif isinstance(value, list):
            return [self._convertValue(item) for item in value]
        elif isinstance(value, dict):
            return {str(k): self._convertValue(v) for k, v in value.items()}
        elif isinstance(value, tuple):
            return [self._convertValue(item) for item in value]
        elif isinstance(value, set):
            return [self._convertValue(item) for item in value]
        elif isinstance(value, enum.Enum):
            return value.name
        elif isinstance(value, (str, int, float, bool)):
            return value
        elif dc.is_dataclass(value) and not isinstance(value, type):
            # Handle non-Base dataclasses
            return self._convertDataclass(value)
        elif isinstance(value, type):
            # Handle type references
            return {"_type_ref": value.__name__}
        else:
            # Fallback: convert to string
            return str(value)

    def _convertDataclass(self, obj: Any) -> Dict[str, Any]:
        """Convert a non-Base dataclass to dictionary."""
        result = {
            "_type": type(obj).__name__
        }
        
        for f in dc.fields(obj):
            if f.name.startswith('_'):
                continue
            value = getattr(obj, f.name)
            result[f.name] = self._convertValue(value)
        
        return result

    def convertBase(self, obj: Base) -> Dict[str, Any]:
        """Convert a Base object to JSON representation."""
        return self._convertDefault(obj, type(obj))
