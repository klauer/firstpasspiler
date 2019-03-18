import os
import pathlib
import fpp

from fpp import parse, write_output, build_namespace
from PyQt5 import (Qt, QtBluetooth, QtCore, QtDesigner, QtGui, QtHelp,
                   QtMacExtras, QtMultimedia, QtMultimediaWidgets, QtNetwork,
                   QtNfc, QtOpenGL, QtPrintSupport, QtQml, QtQuick,
                   QtQuickWidgets, QtSql, QtSvg, QtTest, QtWebChannel,
                   QtWebSockets, QtWidgets, QtXml, QtXmlPatterns)

python_base_modules = (
    QtBluetooth, QtCore, QtDesigner, QtGui, QtHelp, QtMacExtras,
    QtMultimedia, QtMultimediaWidgets, QtNetwork, QtNfc, QtOpenGL,
    QtPrintSupport, QtQml, QtQuick, QtQuickWidgets, QtSql, QtSvg, QtTest,
    QtWebChannel, QtWebSockets, QtWidgets, QtXml, QtXmlPatterns, Qt
)

# TODO
fpp.project_namespaces = ['QtNodes::', 'QtNodes.', 'std::', 'std.']

source_path = pathlib.Path.home() / 'Repos' / 'nodeeditor' / 'src'
python_namespace = build_namespace(python_base_modules)
base_clsdict = parse(source_path, python_base_namespace=python_namespace)

os.makedirs('output', exist_ok=True)
write_output(base_clsdict, 'output', python_base_modules=python_base_modules)
