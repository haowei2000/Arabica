# __init__.py
import pkgutil
import importlib
import sys

for loader, name, is_pkg in pkgutil.iter_modules(__path__):
    module = importlib.import_module(f".{name}", package=__name__)
    # 将所有函数加入父模块命名空间
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if callable(attr) and not attr_name.startswith("_"):
            globals()[attr_name] = attr

__all__ = [name for name in globals() if callable(globals()[name])]
