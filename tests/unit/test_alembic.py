"""Defends against accidental dual-head migrations after merges."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Prefer alembic next to the running Python (covers venv + CI installs).
# Falls back to bare "alembic" so the test still works when alembic is on PATH.
_python_bin = Path(sys.executable).parent
_alembic_bin = _python_bin / "alembic"
ALEMBIC = str(_alembic_bin) if _alembic_bin.exists() else "alembic"


def test_alembic_heads_count_is_one():
    result = subprocess.run(
        [ALEMBIC, "heads"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    heads = [line for line in result.stdout.strip().splitlines() if line.strip()]
    assert len(heads) == 1, f"Expected exactly 1 alembic head, got {len(heads)}: {heads}"
