"""Import-time stub for PyGObject: every attribute is a permissive class."""
import sys, types

PATHS = {"get_user_config_dir", "get_user_cache_dir", "get_user_data_dir", "get_home_dir", "get_user_special_dir", "get_real_name", "get_user_name"}

class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name in PATHS:
            return lambda *a, **k: "/tmp/genesis-stub"
        return _Stub
    def __call__(cls, *a, **k):
        return type.__call__(cls)
    def __iter__(cls):
        return iter(())

class _Stub(metaclass=_Meta):
    def __init__(self, *a, **k): pass
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Stub
    def __call__(self, *a, **k): return _Stub()
    def __iter__(self): return iter(())
    def __bool__(self): return True

def require_version(*a, **k): pass

class _Repo(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Stub

repository = _Repo("gi.repository")
sys.modules["gi.repository"] = repository
