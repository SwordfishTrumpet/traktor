"""Progress tracking and visualization utilities."""

import threading
import time
from datetime import timedelta
from typing import Optional

from .log import logger


class ProgressTracker:
    """Tracks progress of operations with ETA calculation."""

    def __init__(self, total: int, desc: str = "Processing", unit: str = "items"):
        """Initialize progress tracker.

        Args:
            total: Total number of items to process
            desc: Description of the operation
            unit: Unit name for items (e.g., "items", "movies", "episodes")
        """
        self.total = total
        self.desc = desc
        self.unit = unit
        self.start_time = time.time()
        self.processed = 0
        self.last_update_time = self.start_time
        self.last_update_processed = 0
        self._lock = threading.Lock()

    def update(self, n: int = 1):
        """Update progress by n items.

        Args:
            n: Number of items processed since last update
        """
        with self._lock:
            self.processed += n
            current_time = time.time()

            # Calculate speed (items per second)
            time_since_last_update = current_time - self.last_update_time
            items_since_last_update = self.processed - self.last_update_processed

            if time_since_last_update > 0:
                current_speed = items_since_last_update / time_since_last_update
            else:
                current_speed = 0

            # Calculate ETA
            if current_speed > 0:
                remaining_items = self.total - self.processed
                eta_seconds = remaining_items / current_speed
                eta = timedelta(seconds=int(eta_seconds))
            else:
                eta = None

            # Update tracking
            self.last_update_time = current_time
            self.last_update_processed = self.processed

            # Calculate percentage
            percentage = (self.processed / self.total) * 100 if self.total > 0 else 0

        # Log progress outside lock to avoid I/O holding the lock
        self._log_progress(percentage, current_speed, eta)

    def _log_progress(self, percentage: float, speed: float, eta: Optional[timedelta]):
        """Log progress information.

        Args:
            percentage: Completion percentage
            speed: Items per second
            eta: Estimated time remaining
        """
        eta_str = f"ETA: {eta}" if eta else "ETA: Calculating..."
        logger.info(
            f"{self.desc}: {percentage:.1f}% | "
            f"{self.processed}/{self.total} {self.unit} | "
            f"Speed: {speed:.1f}/s | {eta_str}"
        )

    def complete(self):
        """Mark operation as complete and log summary."""
        total_time = time.time() - self.start_time
        if total_time > 0:
            avg_speed = self.processed / total_time
        else:
            avg_speed = 0

        logger.info(
            f"{self.desc} completed in {timedelta(seconds=int(total_time))} | "
            f"Average speed: {avg_speed:.1f} {self.unit}/s"
        )


class SyncProgress:
    """Tracks progress of sync operations across multiple stages."""

    def __init__(self):
        """Initialize sync progress tracker."""
        self.stages = {}
        self.current_stage = None
        self.start_time = time.time()

    def start_stage(self, name: str, total: int, desc: str, unit: str = "items"):
        """Start a new stage of sync.

        Args:
            name: Stage identifier
            total: Total items in this stage
            desc: Description of the stage
            unit: Unit name for items
        """
        self.current_stage = name
        self.stages[name] = {
            "tracker": ProgressTracker(total, desc, unit),
            "started": time.time(),
            "completed": False,
        }
        logger.info(f"Starting stage: {desc}")

    def update_stage(self, n: int = 1):
        """Update progress in current stage.

        Args:
            n: Number of items processed
        """
        if self.current_stage and self.current_stage in self.stages:
            self.stages[self.current_stage]["tracker"].update(n)

    def complete_stage(self):
        """Mark current stage as complete."""
        if self.current_stage and self.current_stage in self.stages:
            self.stages[self.current_stage]["tracker"].complete()
            self.stages[self.current_stage]["completed"] = True
            self.stages[self.current_stage]["completed_time"] = time.time()
            self.current_stage = None

    def get_summary(self) -> dict:
        """Get summary of all stages.

        Returns:
            Dict with stage summaries
        """
        summary = {"total_time": time.time() - self.start_time, "stages": {}}

        for name, stage_info in self.stages.items():
            tracker = stage_info["tracker"]
            summary["stages"][name] = {
                "description": tracker.desc,
                "processed": tracker.processed,
                "total": tracker.total,
                "completed": stage_info.get("completed", False),
                "duration": stage_info.get("completed_time", time.time()) - stage_info["started"],
            }

        return summary
