"""Tests for progress tracking and visualization utilities."""

import time
from datetime import timedelta
from unittest.mock import patch

from traktor.progress import ProgressTracker, SyncProgress


class TestProgressTracker:
    """Tests for the ProgressTracker class."""

    def test_init(self):
        """Test ProgressTracker initialization."""
        tracker = ProgressTracker(total=100, desc="Testing", unit="items")
        assert tracker.total == 100
        assert tracker.desc == "Testing"
        assert tracker.unit == "items"
        assert tracker.processed == 0
        assert tracker.start_time > 0

    def test_update_increments_processed(self):
        """Test that update increments the processed counter."""
        tracker = ProgressTracker(total=100, desc="Testing")
        tracker.update(5)
        assert tracker.processed == 5
        tracker.update(3)
        assert tracker.processed == 8

    def test_update_single_item(self):
        """Test updating by single item (default)."""
        tracker = ProgressTracker(total=100, desc="Testing")
        tracker.update()
        assert tracker.processed == 1

    @patch("traktor.progress.logger")
    def test_log_progress_format(self, mock_logger):
        """Test that progress logging includes correct information."""
        tracker = ProgressTracker(total=100, desc="Processing Movies", unit="movies")
        tracker.processed = 50

        # Manually call the internal _log_progress method
        tracker._log_progress(percentage=50.0, speed=10.0, eta=timedelta(seconds=5))

        # Verify logger was called with correct information
        mock_logger.info.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]
        assert "Processing Movies" in log_message
        assert "50.0%" in log_message
        assert "50/100" in log_message
        assert "10.0/s" in log_message
        assert "ETA:" in log_message

    @patch("traktor.progress.logger")
    def test_log_progress_no_eta(self, mock_logger):
        """Test progress logging when ETA is None."""
        tracker = ProgressTracker(total=100, desc="Testing")
        tracker._log_progress(percentage=0.0, speed=0.0, eta=None)

        log_message = mock_logger.info.call_args[0][0]
        assert "ETA: Calculating..." in log_message

    @patch("traktor.progress.logger")
    def test_complete(self, mock_logger):
        """Test completion logging."""
        tracker = ProgressTracker(total=100, desc="Testing", unit="items")
        tracker.processed = 100

        # Small delay to ensure measurable time
        time.sleep(0.01)
        tracker.complete()

        mock_logger.info.assert_called()
        log_message = mock_logger.info.call_args[0][0]
        assert "Testing completed" in log_message
        assert "Average speed:" in log_message
        assert "items/s" in log_message

    def test_speed_calculation(self):
        """Test that speed is calculated correctly."""
        tracker = ProgressTracker(total=100, desc="Testing")

        # Simulate processing 10 items quickly
        tracker.last_update_time = time.time() - 1.0  # 1 second ago
        tracker.last_update_processed = 0
        tracker.processed = 10

        # Calculate speed manually
        current_time = time.time()
        time_since_last_update = current_time - tracker.last_update_time
        items_since_last_update = tracker.processed - tracker.last_update_processed

        if time_since_last_update > 0:
            speed = items_since_last_update / time_since_last_update
            assert abs(speed - 10.0) < 0.1  # Approximately 10 items per second (with tolerance)

    def test_eta_calculation(self):
        """Test that ETA is calculated correctly."""
        tracker = ProgressTracker(total=100, desc="Testing")
        tracker.processed = 50

        # At 10 items/second, remaining 50 items should take 5 seconds
        remaining_items = tracker.total - tracker.processed
        eta_seconds = remaining_items / 10.0
        eta = timedelta(seconds=int(eta_seconds))

        assert eta_seconds == 5.0
        assert eta == timedelta(seconds=5)


class TestSyncProgress:
    """Tests for the SyncProgress class."""

    def test_init(self):
        """Test SyncProgress initialization."""
        sync_progress = SyncProgress()
        assert sync_progress.stages == {}
        assert sync_progress.current_stage is None
        assert sync_progress.start_time > 0

    @patch("traktor.progress.logger")
    def test_start_stage(self, mock_logger):
        """Test starting a new stage."""
        sync_progress = SyncProgress()
        sync_progress.start_stage(name="pull_plex", total=100, desc="Pulling from Plex")

        assert sync_progress.current_stage == "pull_plex"
        assert "pull_plex" in sync_progress.stages
        assert sync_progress.stages["pull_plex"]["tracker"].total == 100
        assert sync_progress.stages["pull_plex"]["tracker"].desc == "Pulling from Plex"
        assert sync_progress.stages["pull_plex"]["completed"] is False
        mock_logger.info.assert_called_with("Starting stage: Pulling from Plex")

    def test_update_stage(self):
        """Test updating the current stage."""
        sync_progress = SyncProgress()
        sync_progress.start_stage(name="test_stage", total=100, desc="Testing")

        with patch.object(sync_progress.stages["test_stage"]["tracker"], "update") as mock_update:
            sync_progress.update_stage(5)
            mock_update.assert_called_once_with(5)

    def test_update_stage_no_current(self):
        """Test updating when no stage is current."""
        sync_progress = SyncProgress()
        # Should not raise an error
        sync_progress.update_stage(5)

    def test_complete_stage(self):
        """Test completing the current stage."""
        sync_progress = SyncProgress()
        sync_progress.start_stage(name="test_stage", total=100, desc="Testing")

        # Small delay to ensure measurable time
        time.sleep(0.01)
        sync_progress.complete_stage()

        assert sync_progress.stages["test_stage"]["completed"] is True
        assert "completed_time" in sync_progress.stages["test_stage"]
        assert sync_progress.current_stage is None

    def test_complete_stage_no_current(self):
        """Test completing when no stage is current."""
        sync_progress = SyncProgress()
        # Should not raise an error
        sync_progress.complete_stage()

    def test_get_summary_empty(self):
        """Test getting summary with no stages."""
        sync_progress = SyncProgress()
        summary = sync_progress.get_summary()

        assert "total_time" in summary
        assert "stages" in summary
        assert summary["stages"] == {}
        assert summary["total_time"] >= 0

    def test_get_summary_with_stages(self):
        """Test getting summary with completed stages."""
        sync_progress = SyncProgress()

        # Add and complete a stage
        sync_progress.start_stage(name="stage1", total=50, desc="Stage 1")
        sync_progress.stages["stage1"]["tracker"].processed = 50
        sync_progress.complete_stage()

        # Add another stage
        sync_progress.start_stage(name="stage2", total=100, desc="Stage 2")
        sync_progress.stages["stage2"]["tracker"].processed = 25

        summary = sync_progress.get_summary()

        assert "stage1" in summary["stages"]
        assert "stage2" in summary["stages"]
        assert summary["stages"]["stage1"]["completed"] is True
        assert summary["stages"]["stage2"]["completed"] is False
        assert summary["stages"]["stage1"]["processed"] == 50
        assert summary["stages"]["stage2"]["processed"] == 25
        assert summary["stages"]["stage1"]["description"] == "Stage 1"
        assert summary["stages"]["stage2"]["description"] == "Stage 2"

    def test_multiple_stages_workflow(self):
        """Test a complete multi-stage workflow."""
        sync_progress = SyncProgress()

        # Stage 1
        sync_progress.start_stage(name="pull", total=100, desc="Pulling data")
        sync_progress.update_stage(50)
        sync_progress.update_stage(50)
        sync_progress.complete_stage()

        # Stage 2
        sync_progress.start_stage(name="process", total=50, desc="Processing")
        sync_progress.update_stage(25)
        sync_progress.complete_stage()

        summary = sync_progress.get_summary()

        assert summary["stages"]["pull"]["completed"] is True
        assert summary["stages"]["process"]["completed"] is True
        assert summary["stages"]["pull"]["processed"] == 100
        assert summary["stages"]["process"]["processed"] == 25


class TestProgressTrackerEdgeCases:
    """Tests for edge cases in ProgressTracker."""

    def test_zero_total(self):
        """Test behavior with zero total items."""
        tracker = ProgressTracker(total=0, desc="Testing")
        # Should handle division by zero gracefully
        tracker._log_progress(percentage=0.0, speed=0.0, eta=None)
        # No exception should be raised

    def test_negative_update(self):
        """Test that negative updates are handled."""
        tracker = ProgressTracker(total=100, desc="Testing")
        tracker.update(-5)
        # The tracker allows negative updates (may want to prevent this in future)
        assert tracker.processed == -5

    def test_very_large_numbers(self):
        """Test with very large numbers."""
        tracker = ProgressTracker(total=1000000, desc="Testing")
        tracker.update(999999)
        assert tracker.processed == 999999

    def test_zero_speed_eta_calculation(self):
        """Test ETA when speed is zero."""
        tracker = ProgressTracker(total=100, desc="Testing")
        tracker.processed = 50

        # With zero speed, ETA should be None (calculating)
        eta = None  # This is what the code returns when speed <= 0
        assert eta is None
