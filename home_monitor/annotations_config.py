"""
Optional Grafana dashboard annotations (local JSON file).

Personal or site-specific events stay out of the repo: copy annotations.example.json
to annotations.json (gitignored) or set ANNOTATIONS_CONFIG_PATH.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def get_annotations_config_path() -> Path | None:
    """
    Resolve annotations.json path, or None if not configured.

    Order:
    1. ANNOTATIONS_CONFIG_PATH (must exist)
    2. ./annotations.json (cwd)
    3. <project_root>/annotations.json
    """
    env_path = os.getenv("ANNOTATIONS_CONFIG_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
        logger.warning("ANNOTATIONS_CONFIG_PATH is set but file not found: %s", env_path)
        return None

    for candidate in (
        Path.cwd() / "annotations.json",
        Path(__file__).resolve().parent.parent / "annotations.json",
    ):
        if candidate.is_file():
            return candidate
    return None


def validate_annotation_entry(item: dict[str, Any], index: int) -> None:
    if "title" not in item or not isinstance(item["title"], str):
        raise ValueError(f"annotations[{index}]: 'title' (string) is required")
    if item.get("all_day"):
        if "date" in item and isinstance(item["date"], str):
            return
        if (
            "date_start" in item
            and "date_end" in item
            and isinstance(item["date_start"], str)
            and isinstance(item["date_end"], str)
        ):
            return
        raise ValueError(
            f"annotations[{index}]: all_day entries need 'date' or 'date_start'+'date_end'"
        )
    if "time" not in item or not isinstance(item["time"], str):
        raise ValueError(f"annotations[{index}]: non-all_day entries need 'time' (string)")


def load_dashboard_annotations_config() -> dict[str, Any]:
    """
    Load optional dashboard annotations.

    Returns:
        {
          "annotations": list of dicts (validated),
          "all_day_timezone": str | None  # overrides generator default when set
        }
    """
    path = get_annotations_config_path()
    if path is None:
        return {"annotations": [], "all_day_timezone": None}

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Invalid annotations config {path}: root must be a JSON object")

    raw_list = raw.get("annotations")
    if raw_list is None:
        annotations: list[dict[str, Any]] = []
    elif not isinstance(raw_list, list):
        raise ValueError(f"Invalid annotations config {path}: 'annotations' must be a list")
    else:
        annotations = []
        for i, item in enumerate(raw_list):
            if not isinstance(item, dict):
                logger.warning("Skipping annotations[%s]: not an object", i)
                continue
            try:
                validate_annotation_entry(item, i)
            except ValueError as e:
                logger.warning("%s", e)
                continue
            annotations.append(item)

    tz = raw.get("all_day_timezone")
    if tz is not None and not isinstance(tz, str):
        raise ValueError(f"Invalid annotations config {path}: all_day_timezone must be a string")

    return {"annotations": annotations, "all_day_timezone": tz}
