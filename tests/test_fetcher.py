"""Unit tests for the fetcher module."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from home_monitor.fetcher import fetch_system_stats


class TestFetchSystemStats:
    """Tests for fetch_system_stats function."""

    @patch("home_monitor.fetcher.psutil.cpu_percent")
    @patch("home_monitor.fetcher.psutil.virtual_memory")
    @patch("home_monitor.fetcher.psutil.disk_usage")
    @patch("home_monitor.fetcher.insert_system_reading")
    def test_fetch_system_stats_success(self, mock_insert, mock_disk, mock_memory, mock_cpu):
        """Test successful system stats collection."""
        # Setup mocks
        mock_cpu.return_value = 25.5

        mock_mem = MagicMock()
        mock_mem.percent = 60.0
        mock_mem.used = 2 * 1024 * 1024 * 1024  # 2 GB
        mock_mem.total = 4 * 1024 * 1024 * 1024  # 4 GB
        mock_memory.return_value = mock_mem

        mock_disk_obj = MagicMock()
        mock_disk_obj.percent = 45.0
        mock_disk_obj.used = 100 * 1024 * 1024 * 1024  # 100 GB
        mock_disk_obj.total = 500 * 1024 * 1024 * 1024  # 500 GB
        mock_disk.return_value = mock_disk_obj

        # Call function
        fetch_system_stats()

        # Verify insert was called with correct values
        mock_insert.assert_called_once()
        call_args = mock_insert.call_args[1]

        assert call_args["cpu_percent"] == 25.5
        assert call_args["memory_percent"] == 60.0
        assert call_args["memory_used_mb"] == 2048.0
        assert call_args["memory_total_mb"] == 4096.0
        assert call_args["disk_percent"] == 45.0
        assert call_args["disk_used_gb"] == 100.0
        assert call_args["disk_total_gb"] == 500.0
        assert isinstance(call_args["timestamp"], datetime)

    @patch("home_monitor.fetcher.psutil.cpu_percent")
    @patch("home_monitor.fetcher.psutil.virtual_memory")
    @patch("home_monitor.fetcher.psutil.disk_usage")
    @patch("home_monitor.fetcher.insert_system_reading")
    @patch("home_monitor.fetcher.logger")
    def test_fetch_system_stats_handles_exception(
        self, mock_logger, mock_insert, mock_disk, mock_memory, mock_cpu
    ):
        """Test that exceptions are logged but don't crash."""
        # Make psutil raise an exception
        mock_cpu.side_effect = Exception("CPU error")

        # Should not raise
        fetch_system_stats()

        # Should log error
        mock_logger.error.assert_called_once()
        assert "Error collecting system stats" in str(mock_logger.error.call_args[0][0])

        # Should not call insert
        mock_insert.assert_not_called()
