#!/usr/bin/env python3
"""
GoodPlayer - Launcher script
Handles command line arguments and file associations.

Supports two playback modes:
  --dual     Use dual-mode player (native + frame-accurate, default if available)
  --legacy   Use legacy frame-accurate only mode
"""

import sys
import os

# Handle macOS file open events when launched via Finder
if sys.platform == 'darwin':
    # PyInstaller sets this when using argv_emulation
    # Files opened via "Open With" appear in sys.argv
    pass


def main():
    # Check for mode flags
    use_dual_mode = True  # Default to dual mode
    
    if '--legacy' in sys.argv:
        use_dual_mode = False
        sys.argv.remove('--legacy')
    elif '--dual' in sys.argv:
        use_dual_mode = True
        sys.argv.remove('--dual')
    
    if use_dual_mode:
        try:
            from dual_mode_window import main as app_main
            print("Starting in dual-mode (Native + Frame-Accurate)")
        except ImportError as e:
            print(f"Dual mode unavailable ({e}), falling back to legacy mode")
            from main_window import main as app_main
    else:
        from main_window import main as app_main
        print("Starting in legacy mode (Frame-Accurate only)")
    
    app_main()


if __name__ == "__main__":
    main()
