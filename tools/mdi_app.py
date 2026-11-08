"""Entry point for the packaged MDI.exe (PyInstaller needs a plain script;
``mdi/__main__.py`` uses a relative import)."""

from mdi.gui import main

if __name__ == "__main__":
    main()
