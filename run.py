#!/usr/bin/env python3
"""
GoodPlayer - Launcher script
Handles command line arguments and file associations.
"""

import sys
import os

# Handle macOS file open events when launched via Finder
if sys.platform == 'darwin':
    # PyInstaller sets this when using argv_emulation
    # Files opened via "Open With" appear in sys.argv
    pass

def main():
    from main_window import main as app_main
    app_main()

if __name__ == "__main__":
    main()
