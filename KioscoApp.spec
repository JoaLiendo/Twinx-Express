# -*- mode: python ; coding: utf-8 -*-
import os
import runpy

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

# Metadata del EXE (Propiedades > Detalles), tomada de `version.py`: única fuente de la versión.
_identidad = runpy.run_path(os.path.join(SPECPATH, 'version.py'))
_version = _identidad['VERSION']
_version_tupla = tuple(int(parte) for parte in _version.split('.')) + (0,)
_version_exe = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_version_tupla, prodvers=_version_tupla, mask=0x3F, flags=0x0, OS=0x40004,
                      fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('FileDescription', _identidad['NOMBRE_APLICACION']),
            StringStruct('FileVersion', _version),
            StringStruct('InternalName', 'KioscoApp'),
            StringStruct('OriginalFilename', 'KioscoApp.exe'),
            StringStruct('ProductName', _identidad['NOMBRE_APLICACION']),
            StringStruct('ProductVersion', _version),
        ])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])]),
    ],
)


a = Analysis(
    ['lanzador.py'],
    pathex=[],
    binaries=[],
    datas=[('interfaces/web/templates', 'interfaces/web/templates'), ('interfaces/web/static', 'interfaces/web/static'), ('db/migraciones', 'db/migraciones'), ('db/seed', 'db/seed')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KioscoApp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_version_exe,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='KioscoApp',
)
