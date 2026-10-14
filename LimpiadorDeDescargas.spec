# -*- mode: python ; coding: utf-8 -*-
# SPECPATH lo proporciona PyInstaller; no hay rutas personales codificadas.
from pathlib import Path
root = Path(SPECPATH)
a = Analysis(
    [str(root / 'gui.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tests', 'demo_gui', 'unittest', 'pytest', 'pip', 'setuptools'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='LimpiadorDeDescargas',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    uac_admin=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name='LimpiadorDeDescargas')
