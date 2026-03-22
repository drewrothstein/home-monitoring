"""Tests for optional annotations.json loading."""

import json
from pathlib import Path

import pytest

from home_monitor.annotations_config import (
    get_annotations_config_path,
    load_dashboard_annotations_config,
    validate_annotation_entry,
)


class TestValidateAnnotationEntry:
    def test_all_day_single_date(self):
        validate_annotation_entry(
            {"all_day": True, "date": "2025-01-01", "title": "x"}, 0
        )

    def test_all_day_range(self):
        validate_annotation_entry(
            {
                "all_day": True,
                "date_start": "2025-01-01",
                "date_end": "2025-01-02",
                "title": "x",
            },
            0,
        )

    def test_point_requires_time(self):
        with pytest.raises(ValueError, match="time"):
            validate_annotation_entry({"title": "x"}, 0)

    def test_title_required(self):
        with pytest.raises(ValueError, match="title"):
            validate_annotation_entry({"all_day": True, "date": "2025-01-01"}, 0)


class TestLoadDashboardAnnotationsConfig:
    def test_missing_file_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "home_monitor.annotations_config.get_annotations_config_path",
            lambda: None,
        )
        assert load_dashboard_annotations_config() == {
            "annotations": [],
            "all_day_timezone": None,
        }

    def test_loads_valid_file(self, monkeypatch, tmp_path: Path):
        p = tmp_path / "annotations.json"
        p.write_text(
            json.dumps(
                {
                    "all_day_timezone": "America/Chicago",
                    "annotations": [
                        {
                            "all_day": True,
                            "date": "2025-03-01",
                            "title": "A",
                            "text": "",
                            "tags": "",
                            "locations": ["FL"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("ANNOTATIONS_CONFIG_PATH", str(p))
        cfg = load_dashboard_annotations_config()
        assert cfg["all_day_timezone"] == "America/Chicago"
        assert len(cfg["annotations"]) == 1
        assert cfg["annotations"][0]["title"] == "A"

    def test_skips_invalid_entries(self, monkeypatch, tmp_path: Path):
        p = tmp_path / "annotations.json"
        p.write_text(
            json.dumps(
                {
                    "annotations": [
                        {"all_day": True, "date": "2025-03-01", "title": "ok"},
                        {"title": "missing time"},
                        "not-an-object",
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("ANNOTATIONS_CONFIG_PATH", str(p))
        cfg = load_dashboard_annotations_config()
        assert len(cfg["annotations"]) == 1

    def test_invalid_root_raises(self, monkeypatch, tmp_path: Path):
        p = tmp_path / "annotations.json"
        p.write_text(json.dumps([]), encoding="utf-8")
        monkeypatch.setenv("ANNOTATIONS_CONFIG_PATH", str(p))
        with pytest.raises(ValueError, match="object"):
            load_dashboard_annotations_config()


def test_get_annotations_config_path_env_missing_file(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ANNOTATIONS_CONFIG_PATH", str(tmp_path / "nope.json"))
    assert get_annotations_config_path() is None
