"""The environment every rehearsal robot starts Clicky in.

The test PC is where the user keeps his real company configuration (engine.company_config, in Clicky's data
folder). A robot must never read it: its thresholds and segments would change the synthetic grading, and a
rehearsal has no business with an employer's configuration. So every robot points CLICKY_CONFIG_DIR at an empty
folder of its own output, which the loader reads as "no company configuration" and never falls back from. A case
that needs a configuration copies its own synthetic YAML into that folder.
"""
from __future__ import annotations

import os
from pathlib import Path

CONFIG_FOLDER = "cau-hinh-robot"


def clean_env(log: Path, **extra: str) -> dict:
    """os.environ with the scene log at log, and the company configuration folder at an empty folder beside it."""
    folder = Path(log).parent / CONFIG_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLICKY_SCENE_LOG=str(log), CLICKY_CONFIG_DIR=str(folder))
    env.update(extra)
    return env
