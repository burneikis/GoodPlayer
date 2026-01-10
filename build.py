#!/usr/bin/env python3
"""
Build script for GoodPlayer
Creates standalone executables for Windows and macOS
"""

import os
import sys
import shutil
import subprocess
import platform

def clean_build():
    """Remove previous build artifacts."""
    dirs_to_remove = ['build', 'dist', '__pycache__']
    for d in dirs_to_remove:
        if os.path.exists(d):
            print(f"Removing {d}/")
            shutil.rmtree(d)
    
    # Remove .pyc files
    for root, dirs, files in os.walk('.'):
        for f in files:
            if f.endswith('.pyc'):
                os.remove(os.path.join(root, f))

def check_dependencies():
    """Check if required build tools are installed."""
    try:
        import PyInstaller
        print(f"PyInstaller version: {PyInstaller.__version__}")
    except ImportError:
        print("ERROR: PyInstaller not found. Install with: pip install pyinstaller")
        sys.exit(1)
    
    try:
        import av
        print(f"PyAV version: {av.__version__}")
    except ImportError:
        print("ERROR: PyAV not found. Install with: pip install av")
        sys.exit(1)

def build_executable():
    """Build the executable using PyInstaller."""
    print(f"\nBuilding for {platform.system()}...")
    
    # Use spec file if it exists, otherwise use command line
    if os.path.exists('GoodPlayer.spec'):
        cmd = ['pyinstaller', '--clean', 'GoodPlayer.spec']
    else:
        cmd = [
            'pyinstaller',
            '--clean',
            '--name=GoodPlayer',
            '--windowed',
            '--onedir',
            '--add-data', 'README.md:.',
            'run.py'
        ]
    
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    
    if result.returncode != 0:
        print("ERROR: Build failed!")
        sys.exit(1)
    
    print("\nBuild complete!")
    print(f"Output: dist/GoodPlayer/")
    
    if platform.system() == 'Darwin':
        print("macOS app bundle: dist/GoodPlayer.app/")

def create_dmg():
    """Create DMG installer for macOS."""
    if platform.system() != 'Darwin':
        print("DMG creation is only supported on macOS")
        return
    
    if not os.path.exists('dist/GoodPlayer.app'):
        print("ERROR: GoodPlayer.app not found. Run build first.")
        return
    
    print("\nCreating DMG installer...")
    
    dmg_name = 'GoodPlayer-1.0.0.dmg'
    
    # Check if create-dmg is available (brew install create-dmg)
    if shutil.which('create-dmg'):
        cmd = [
            'create-dmg',
            '--volname', 'GoodPlayer',
            '--window-pos', '200', '120',
            '--window-size', '600', '400',
            '--icon-size', '100',
            '--icon', 'GoodPlayer.app', '150', '190',
            '--app-drop-link', '450', '190',
            f'dist/{dmg_name}',
            'dist/GoodPlayer.app'
        ]
        subprocess.run(cmd)
    else:
        # Fallback to hdiutil
        cmd = [
            'hdiutil', 'create',
            '-volname', 'GoodPlayer',
            '-srcfolder', 'dist/GoodPlayer.app',
            '-ov',
            '-format', 'UDZO',
            f'dist/{dmg_name}'
        ]
        subprocess.run(cmd)
    
    print(f"DMG created: dist/{dmg_name}")

def create_windows_installer():
    """Create Windows installer using NSIS (if available)."""
    if platform.system() != 'Windows':
        print("Windows installer creation is only supported on Windows")
        return
    
    if not os.path.exists('dist/GoodPlayer'):
        print("ERROR: dist/GoodPlayer not found. Run build first.")
        return
    
    # Check if NSIS is available
    nsis_path = shutil.which('makensis')
    if not nsis_path:
        # Try common installation paths
        common_paths = [
            r'C:\Program Files (x86)\NSIS\makensis.exe',
            r'C:\Program Files\NSIS\makensis.exe',
        ]
        for p in common_paths:
            if os.path.exists(p):
                nsis_path = p
                break
    
    if nsis_path and os.path.exists('installer.nsi'):
        print("\nCreating Windows installer with NSIS...")
        subprocess.run([nsis_path, 'installer.nsi'])
    else:
        print("\nNSIS not found or installer.nsi missing.")
        print("You can distribute the dist/GoodPlayer folder as a portable app,")
        print("or install NSIS to create an installer.")
        print("Download NSIS: https://nsis.sourceforge.io/")

def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Build GoodPlayer executable')
    parser.add_argument('--clean', action='store_true', help='Clean build artifacts')
    parser.add_argument('--installer', action='store_true', help='Create installer (DMG/NSIS)')
    parser.add_argument('--skip-build', action='store_true', help='Skip build, only create installer')
    
    args = parser.parse_args()
    
    if args.clean:
        clean_build()
        if not args.installer and not args.skip_build:
            return
    
    check_dependencies()
    
    if not args.skip_build:
        build_executable()
    
    if args.installer:
        if platform.system() == 'Darwin':
            create_dmg()
        elif platform.system() == 'Windows':
            create_windows_installer()

if __name__ == '__main__':
    main()
