"""
Where theseus keeps its own bookkeeping files.

Sidecars used to sit next to the generated dashboards, in Grafana's
provisioning directory — and Grafana's file provider tries to load every .json
there as a dashboard, logging "Dashboard title cannot be empty" every scan.
They live in THESEUS_STATE_DIR (the obs-theseus-data volume) now.
"""
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def state_file(name: str, legacy_dir: Path) -> Path:
    """Path for a state file; moves a legacy copy out of legacy_dir once."""
    d = Path(os.getenv("THESEUS_STATE_DIR", "/data"))
    d.mkdir(parents=True, exist_ok=True)
    new, old = d / name, legacy_dir / name
    if old.exists():
        try:
            if not new.exists():
                shutil.copy2(old, new)
            old.unlink()
            logger.info("moved %s out of %s", name, legacy_dir)
        except OSError as e:
            logger.warning("could not move legacy %s: %s", old, e)
    return new
