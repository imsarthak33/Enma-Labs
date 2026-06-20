"""Create a Linux-compatible zip of the enma-backend directory for AWS CodeBuild."""
import zipfile
import os

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ZIP_PATH = os.path.join(os.path.dirname(BACKEND_DIR), "enma-backend.zip")

EXCLUDE_DIRS = {'.venv', '__pycache__', '.mypy_cache', '.ruff_cache',
                '.pytest_cache', 'enma_backend.egg-info', '.git'}
EXCLUDE_FILES = {'.coverage', 'coverage.xml', 'codebuild-log.json', 'create_zip.py'}

with zipfile.ZipFile(ZIP_PATH, 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(BACKEND_DIR):
        # Prune excluded directories
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if f in EXCLUDE_FILES:
                continue
            abs_path = os.path.join(root, f)
            # Use forward-slash relative paths
            rel_path = os.path.relpath(abs_path, BACKEND_DIR).replace('\\', '/')
            zf.write(abs_path, rel_path)

print(f"Created {ZIP_PATH}")
# List a few entries to verify
with zipfile.ZipFile(ZIP_PATH, 'r') as zf:
    names = zf.namelist()
    print(f"Total entries: {len(names)}")
    for n in names[:10]:
        print(f"  {n}")
