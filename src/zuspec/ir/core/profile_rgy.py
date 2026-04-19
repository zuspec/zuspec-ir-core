
import dataclasses as dc
import inspect
import logging
import sys
from typing import ClassVar, Dict, List, Type
from types import ModuleType
from .base import Base, BaseP

@dc.dataclass
class Profile(object):
    mod : ModuleType = dc.field()
    pkg : ModuleType = dc.field()
    types : List[Type[BaseP]] = dc.field(default_factory=list)


class ProfileRgy(object):
    _profile_m : Dict[ModuleType, Profile] = {}
    _log : ClassVar = logging.getLogger("zuspec.ir.ProfileRgy")

    @classmethod
    def register_profile(cls, modname, super):
        cls._log.debug("--> register_profile %s %s" % (cls, modname))
        # Find the package containing 'modname'
        mod = sys.modules[modname]
        pkg = mod
        while pkg is not None and not hasattr(pkg, "__path__"):
            elems = pkg.__name__.split('.')
            if len(elems) > 1:
                pname = '.'.join(elems[:-1])
                pkg = sys.modules[pname]
            else:
                pkg = None
        if pkg is None:
            raise Exception("Failed to find package for module \"%s\"" % modname)

        # Now, find all instances of 'BaseP' in the package
        profile_t : Dict[str, Type[BaseP]] = {}
        for name,obj in inspect.getmembers(pkg, inspect.isclass):
            if obj.__module__.startswith(pkg.__name__):
                if issubclass(obj, BaseP):
                    if obj.__name__ != "BaseP" and obj.__name__ not in profile_t.keys():
                        cls._log.debug("Add type %s (%s)" % (obj.__name__, obj.__qualname__))
                        profile_t[obj.__name__] = obj

        ProfileRgy._profile_m[mod] = Profile(
            mod, 
            pkg, 
            list([e[1] for e in profile_t.items()]))

        cls._log.debug("Profile: %s", str(profile_t))


    @classmethod
    def get_profile(cls, pmod) -> Profile:
        return ProfileRgy._profile_m[pmod]


    @classmethod
    def inst(cls):
        pass
    pass