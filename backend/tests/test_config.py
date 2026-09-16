"""Guards for environment configuration that only misbehaves on another machine."""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def _run(snippet: str, **env) -> str:
    result = subprocess.run([sys.executable, "-c", snippet], cwd=BACKEND, capture_output=True,
                            text=True, env={**os.environ, **env})
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_tessdata_dir_applies_whatever_is_imported_first():
    """A custom tessdata folder must take effect regardless of import order.

    Configuring it inside one pipeline module made it depend on which module happened to
    be imported first, so the setting silently did nothing for some entry points.
    """
    for first_import in ("app.pipeline.table", "app.pipeline.preprocess",
                         "app.pipeline.regions", "app.pipeline.ocr", "app.services.validation"):
        out = _run(f"import {first_import}; import os; print(os.environ.get('TESSDATA_PREFIX'))",
                   BHULEKH_TESSDATA_DIR="/tmp/some-tessdata")
        assert out == "/tmp/some-tessdata", f"{first_import} did not apply the tessdata folder"


def test_no_ocr_config_string_carries_a_quoted_path():
    """Paths must never be embedded in Tesseract's config string.

    pytesseract splits that string with shlex in non-POSIX mode on Windows, which keeps
    the surrounding quotes, so a quoted path becomes a directory name containing quote
    characters and every language fails to load — while working fine on Linux.
    """
    for module in ("app/pipeline/ocr.py", "app/pipeline/table.py",
                   "app/pipeline/regions.py", "app/main.py"):
        source = (BACKEND / module).read_text(encoding="utf-8")
        assert "--tessdata-dir" not in source, f"{module} passes a path via the config string"
