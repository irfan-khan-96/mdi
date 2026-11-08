# Keep tests away from the real %APPDATA%\MDI (log, inbox, lock).
import os
import tempfile

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="mdi-test-")
