# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for GoodPlayer
Run with: pyinstaller GoodPlayer.spec
"""

import sys
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# Check for icon files (optional)
icon_file = None
if sys.platform == 'darwin' and os.path.exists('icon.icns'):
    icon_file = 'icon.icns'
elif sys.platform == 'win32' and os.path.exists('icon.ico'):
    icon_file = 'icon.ico'

# Collect PyAV/FFmpeg libraries
av_datas = collect_data_files('av')
av_binaries = collect_dynamic_libs('av')

# Collect sounddevice/PortAudio libraries
sd_binaries = collect_dynamic_libs('sounddevice')

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=av_binaries + sd_binaries,
    datas=av_datas,
    hiddenimports=[
        'av',
        'av.audio',
        'av.video',
        'av.container',
        'sounddevice',
        'numpy',
        'PyQt6',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'PIL',
        'scipy',
        'pandas',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GoodPlayer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Set to True for debugging
    disable_windowed_traceback=False,
    argv_emulation=True,  # Allows drag-and-drop on macOS
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GoodPlayer',
)

# macOS app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='GoodPlayer.app',
        icon=icon_file,
        bundle_identifier='com.goodplayer.app',
        info_plist={
            'CFBundleName': 'GoodPlayer',
            'CFBundleDisplayName': 'GoodPlayer',
            'CFBundleVersion': '1.0.0',
            'CFBundleShortVersionString': '1.0.0',
            'NSHighResolutionCapable': True,
            'NSRequiresAquaSystemAppearance': False,  # Support dark mode
            'CFBundleDocumentTypes': [
                {
                    'CFBundleTypeName': 'Video File',
                    'CFBundleTypeRole': 'Viewer',
                    'LSHandlerRank': 'Alternate',
                    'LSItemContentTypes': [
                        'public.movie',
                        'public.mpeg-4',
                        'public.avi',
                        'com.microsoft.windows-media-wmv',
                        'org.matroska.mkv',
                        'com.apple.quicktime-movie',
                    ],
                    'CFBundleTypeExtensions': ['mp4', 'mkv', 'avi', 'mov', 'wmv', 'webm', 'm4v'],
                }
            ],
        },
    )
