# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for CortexUI (run from the repo root):

    pyinstaller --clean --noconfirm cortexui.spec

macOS: dist/CortexUI.app (then packaging/make_dmg.sh); Windows: dist/CortexUI/CortexUI.exe.
PyInstaller builds for the machine it runs on: Windows and each Mac architecture are built separately.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

sys.path.insert(0, SPECPATH)
from version import APP_NAME, APP_VERSION  # noqa: E402

hiddenimports = [
    'scipy.signal',
    'scipy.fft',
    'scipy._lib.messagestream',
    'numpy',
    'pyqtgraph',
    'tdt',
    'neo',
    'quantities',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
]
hiddenimports += collect_submodules('scipy')
hiddenimports += collect_submodules('neo.rawio')

datas = collect_data_files('pyqtgraph')
datas += [
    ('ui/assets', 'ui/assets'),  # theme images (checkmark)
    ('assets/icon.png', 'assets'),  # window icon (Windows)
]
try:
    datas += collect_data_files('tdt')
except Exception:
    pass

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'IPython', 'jupyter', 'notebook', 'h5py'],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

# upx=False everywhere: UPX can break Qt libraries on macOS and trips antivirus scanners on Windows
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico' if sys.platform == 'win32' else 'assets/icon.icns',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name=f'{APP_NAME}.app',
        icon='assets/icon.icns',
        bundle_identifier='io.github.sidhantm123.cortexui',
        version=APP_VERSION.split('-')[0],
        info_plist={
            'CFBundleName': APP_NAME,
            'CFBundleDisplayName': APP_NAME,
            # macOS wants plain numbers here: a beta (1.1.0-beta.1) is 1.1.0 (the About box shows the suffix)
            'CFBundleVersion': APP_VERSION.split('-')[0],
            'CFBundleShortVersionString': APP_VERSION.split('-')[0],
            # numpy/scipy wheels for macOS are built for 14 and later (Accelerate)
            'LSMinimumSystemVersion': '14.0',
            'NSHighResolutionCapable': True,
            'NSRequiresAquaSystemAppearance': False,
        },
    )
