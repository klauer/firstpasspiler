import inflection
import os
import pathlib
import re
import fpp
import shutil

import qtpy
from qtpy.QtCore import Qt
import collections
import inspect

from fpp import (parse, write_output, build_namespace, get_all_names,
                 dumb_rename_all, Identifier, Class)
from PyQt5 import (Qt, QtBluetooth, QtCore, QtDesigner, QtGui, QtHelp,
                   QtMultimedia, QtMultimediaWidgets, QtNetwork,
                   QtNfc, QtOpenGL, QtPrintSupport, QtQml, QtQuick,
                   QtQuickWidgets, QtSql, QtSvg, QtTest, QtWebChannel,
                   QtWebSockets, QtWidgets, QtXml, QtXmlPatterns)

output = pathlib.Path('output')

python_base_modules = (
    QtBluetooth, QtCore, QtDesigner, QtGui, QtHelp,
    QtMultimedia, QtMultimediaWidgets, QtNetwork, QtOpenGL,
    QtPrintSupport, QtQml, QtQuick, QtQuickWidgets, QtSql, QtSvg, QtTest,
    QtWebChannel, QtWebSockets, QtWidgets, QtXml, QtXmlPatterns, Qt
)

# TODO
fpp.project_namespaces = ['std::', 'std.', 'ads::', 'ads.', 'internal::',
                          'internal.']

home = pathlib.Path.home()
root_path = home / 'Repos' / 'Qt-Advanced-Docking-System'
source_path = root_path / 'src'
include_path = root_path / 'src'

args = [
    f'-stdlib=libc++',
    f'-O2',
    f'-std=gnu++1y',
    f'-isysroot',
    f'/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX10.14.sdk',
    f'-mmacosx-version-min=10.9',
    f'-Wall',
    f'-W',
    f'-fPIC',
    f'-DQT_DEPRECATED_WARNINGS',
    f'-DADS_SHARED_EXPORT',
    f'-DQT_NO_DEBUG',
    f'-DQT_WIDGETS_LIB',
    f'-DQT_GUI_LIB',
    f'-DQT_CORE_LIB',
    f'-I{include_path}',
    f'-I{home}/mc/envs/py36/include/qt',
    f'-I{home}/mc/envs/py36/include/qt/QtWidgets',
    f'-I{home}/mc/envs/py36/include/qt/QtGui',
    f'-I{home}/mc/envs/py36/include/qt/QtCore',
    f'-I/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX10.14.sdk/System/Library/Frameworks/OpenGL.framework/Headers',
    f'-I/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX10.14.sdk/System/Library/Frameworks/AGL.framework/Headers',
    f'-I{home}/mc/envs/py36/mkspecs/macx-clang',
]

python_namespace = build_namespace(python_base_modules)
dumb_rename_all.all_known_names = get_all_names(python_base_modules)
dumb_rename_all.simple_renames.update(
    {'_this': 'public',
     'next': 'next_',
     'from': 'from_',
     'in': 'in_',
     'format': 'format_',
     'super': 'super()',
     }
)

Class.class_renames = {
    'CDockAreaLayout': 'DockAreaLayout',
    'CDockAreaTabBar': 'DockAreaTabBar',
    'CDockAreaTitleBar': 'DockAreaTitleBar',
    'CDockAreaWidget': 'DockAreaWidget',
    'CDockContainerWidget': 'DockContainerWidget',
    'CDockInsertParam': 'DockInsertParam',
    'CDockManager': 'DockManager',
    'CDockOverlay': 'DockOverlay',
    'CDockOverlayCross': 'DockOverlayCross',
    'CDockSplitter': 'DockSplitter',
    'CDockWidget': 'DockWidget',
    'CDockWidgetTab': 'DockWidgetTab',
    'CElidingLabel': 'ElidingLabel',
    'CFloatingDockContainer': 'FloatingDockContainer',
}

identifiers = {
    'emit': Identifier('emit', '[emit_TODO]', None),
}

clsdict = parse(source_path, args=args, python_base_namespace=python_namespace,
                base_identifier_map=identifiers)

os.makedirs('output', exist_ok=True)
write_output(clsdict, 'output', python_base_modules=python_base_modules)


files = ['{}.py'.format(inflection.underscore(cls.name))
         for cls in clsdict.values()]

python_base_namespace = {}
for qt_module in python_base_modules:
    for attr in dir(qt_module):
        cls = getattr(qt_module, attr)
        if inspect.isclass(cls):
            if attr not in python_base_namespace:
                python_base_namespace[attr] = (
                    re.compile(r'\b' + attr + r'\b'), qt_module, cls)

renames = [
    (fn, fn[2:])
    for fn in files
    if fn.startswith('c_')
]

for from_, to in renames:
    files.remove(from_)
    files.append(to)
    shutil.move(output / from_, output / to)

privates = [
    (fn.replace('.py', '_private.py'), fn)
    for fn in files
]

combines = {
    fn: (private, fn)
    for private, fn in privates
    if (output / private).exists()
}

for dest, combine_files in combines.items():
    source = '\n'.join(
        open(output / fn).read()
        for fn in combine_files
    )
    for file in combine_files:
        files.remove(file)
        (output / file).unlink()
    files.append(dest)
    with open(output / dest, 'wt') as f:
        print(source, file=f)


for fn in files:
    with open(output / fn) as f:
        source = f.read()

    prepend = collections.defaultdict(list)
    found = []
    for attr, (expr, module, cls) in python_base_namespace.items():
        if attr not in found and expr.search(source):
            prepend[module.__name__].append(attr)
            found.append(attr)

    if prepend:
        print(prepend)
        with open(output / fn, "wt") as f:
            for module, imports in prepend.items():
                imports = ", ".join(imports)
                if imports.count(",") > 5:
                    imports = f"({imports})"
                print(f"from {module} import {imports}", file=f)
            print("", file=f)
            print("", file=f)
            print(source, file=f)
