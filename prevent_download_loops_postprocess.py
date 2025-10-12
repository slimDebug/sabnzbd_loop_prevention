#!/usr/bin/env python3

"""
SABnzbd Download Loop Prevention - POST-PROCESSING Script
Updates download status after completion or failure
Uses shared library for common functionality
"""

import os
import sys
import time
from datetime import datetime
from typing import Dict, Any, Optional

# Import shared library
from loop_prevention_shared import (
    ConfigLoader, LockedFile, Logger, LogLevel, NotifierInterface, create_notifier, ensure_file_exists
)


class PostProcessLoopPrevention:
    """
    Post-processing script to update download status after completion.

    Updates the status of downloads in the history file to SUCCESS or FAILED
    based on the SABnzbd post-processing result.

    Attributes:
        current_time (int): Current Unix timestamp
        history_file (str): Path to download history file
        use_duplicate_key (str): Whether to use the duplicate key for matching
        wants_raw_data (bool): Whether to send raw data to notifier
        logger (Logger): Logger instance
        notifier (NotifierInterface): Notifier instance
        nzb_name (str): Name of the NZB from SABnzbd environment
        category (str): Category from SABnzbd environment
        duplicate_key (str): Duplicate key from SABnzbd environment
        status (str): Status code from SABnzbd (0=OK, 1+=Failed)
        filename (str): Alternative filename from SABnzbd
        complete_dir (str): Completion directory from SABnzbd
        match_method (str): Method used to match entry
        config (dict): Configuration dictionary
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Initialize the PostProcessLoopPrevention script.

        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.current_time = int(time.time())

        # Load config values
        self.history_file = config.get("history_file")
        self.use_duplicate_key = config.get("use_duplicate_key", True)
        self.wants_raw_data = config.get("wants_raw_data", False)

        # Initialize logger
        self.logger = Logger(
            config.get("log_file"),
            config.get("max_log_size_mb"),
            config.get("max_log_backups"),
            config.get("log_level")
        )

        # Initialize notifier using factory
        self.notifier = create_notifier(config.get("notifier", {}), self.logger)

        # Get SABnzbd environment variables
        # Note: These may differ from pre-queue values!
        self.nzb_name = os.environ.get('SAB_FINAL_NAME', '')
        self.category = os.environ.get('SAB_CAT', '')
        self.duplicate_key = os.environ.get('SAB_DUPLICATE_KEY', '')
        self.status = os.environ.get('SAB_PP_STATUS', '0')  # 0=OK, 1+=Failed

        # Alternative names that might be available
        self.filename = os.environ.get('SAB_FILENAME', '')
        self.complete_dir = os.environ.get('SAB_COMPLETE_DIR', '')

        # Track match method for notifications
        self.match_method = None

        ensure_file_exists(self.history_file)

    def log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        """
        Log a message using the logger.

        Args:
            message: Message to log
            level: Log level (default: INFO)

        Returns:
            None
        """
        self.logger.log(message, level)

    def _normalize_name(self, name: str) -> str:
        """
        Normalize name for better matching.

        Args:
            name: Name to normalize

        Returns:
            Normalized name string
        """
        if not name:
            return ""
        # Remove common variations
        return name.strip().lower().replace('.', ' ').replace('_', ' ')

    def _is_match(self, history_name: str, history_key: str) -> bool:
        """
        Check if history entry matches current download using multiple methods.

        Args:
            history_name: Name from history entry
            history_key: Duplicate key from history entry

        Returns:
            True if entries match, False otherwise
        """
        # Method 1: Exact duplicate_key match (most reliable)
        if self.use_duplicate_key and self.duplicate_key and history_key and self.duplicate_key == history_key:
            self.log(f"Match method: duplicate_key exact ({history_key})")
            self.match_method = "duplicate_key_exact"
            return True

        # Method 2: Exact name match
        if self.nzb_name and history_name and self.nzb_name == history_name:
            self.log(f"Match method: name exact ({history_name})")
            self.match_method = "name_exact"
            return True

        # Method 3: Normalized name match (handles case/formatting differences)
        if self.nzb_name and history_name:
            normalized_current = self._normalize_name(self.nzb_name)
            normalized_history = self._normalize_name(history_name)
            if normalized_current and normalized_history and normalized_current == normalized_history:
                self.log(f"Match method: normalized name ({normalized_history})")
                self.match_method = "name_normalized"
                return True

        # Method 4: Try filename if nzb_name didn't work
        if self.filename and history_name and self.filename == history_name:
            self.log(f"Match method: filename exact ({history_name})")
            self.match_method = "filename_exact"
            return True

        # Method 5: Partial match (name contains or is contained in history)
        if self.nzb_name and history_name:
            if self.nzb_name in history_name or history_name in self.nzb_name:
                self.log(f"Match method: partial name match")
                self.match_method = "name_partial"
                return True

        return False

    def _get_all_env_vars(self) -> Dict[str, str]:
        """
        Get all SABnzbd-related environment variables.

        Returns:
            Dictionary of all SAB_* environment variables
        """
        return {key: value for key, value in os.environ.items() if key.startswith('SAB_')}

    def update_status(self) -> bool:
        """
        Update the status of matching download in history file.

        Returns:
            True if history was updated, False otherwise
        """
        # Determine final status
        if self.status == '0':
            final_status = "SUCCESS"
            self.log(f"[POST-PROCESS] Download completed successfully")
        else:
            final_status = "FAILED"
            self.log(f"[POST-PROCESS] Download failed (code {self.status})")

        # Log all available identifiers for debugging
        self.log(f"Looking for match with:")
        self.log(f"  - nzb_name: '{self.nzb_name}'")
        self.log(f"  - filename: '{self.filename}'")
        self.log(f"  - category: '{self.category}'")
        self.log(f"  - duplicate_key: '{self.duplicate_key}'")

        updated = False

        try:
            # Read all entries
            with LockedFile(self.history_file, 'r') as f:
                lines = f.readlines()

            self.log(f"Checking {len(lines)} history entries")

            # Find and update matching entry
            new_lines = []

            for line_num, line in enumerate(lines, 1):
                parts = line.strip().split('|')
                if len(parts) < 5:
                    self.log(f"Line {line_num}: Skipping (malformed - {len(parts)} fields)")
                    new_lines.append(line)
                    continue

                timestamp, category, name, dupe_key, status = parts[0], parts[1], parts[2], parts[3], parts[4]

                self.log(f"Line {line_num}: Checking '{name}' (status: {status})")

                # Check if this entry matches
                if self._is_match(name, dupe_key) and status == "PENDING" and not updated:
                    # Update status (keep original timestamp, category, name, key)
                    new_line = f"{timestamp}|{category}|{name}|{dupe_key}|{final_status}{os.linesep}"
                    new_lines.append(new_line)
                    updated = True
                    self.log(f"✅ UPDATED Line {line_num}: PENDING -> {final_status}")
                else:
                    new_lines.append(line)

            # Write back updated entries
            with LockedFile(self.history_file, 'w') as f:
                f.writelines(new_lines)

            if not updated:
                self.log("⚠️ WARNING: No matching PENDING entry found!", LogLevel.ERROR)
                self.log("This usually means:", LogLevel.ERROR)
                self.log("  1. Pre-queue script didn't run (check SABnzbd config)", LogLevel.ERROR)
                self.log("  2. Name/key mismatch between pre-queue and post-process", LogLevel.ERROR)
                self.log("  3. Entry was already updated or removed", LogLevel.ERROR)
                # DO NOT add new entry - it causes duplicates with empty fields
                # Instead, log the issue for manual investigation
                self.log("NOT adding new entry to prevent corruption", LogLevel.ERROR)

        except Exception as e:
            self.log(f"Error updating status: {e}", LogLevel.ERROR)
            import traceback
            self.log(traceback.format_exc(), LogLevel.ERROR)

        return updated

    def send_update_notification(self, updated: bool) -> None:
        """
        Send notification about status update.

        Args:
            updated: Whether the history was successfully updated

        Returns:
            None
        """
        if not self.notifier:
            return

        # Determine status and icon
        if self.status == '0':
            title = "✅ Download Completed"
            final_status = "SUCCESS"
        else:
            title = "❌ Download Failed"
            final_status = "FAILED"

        message_parts = [
            f"**Download:** `{self.nzb_name}`",
            f"**Category:** `{self.category or 'None'}`",
        ]

        if self.duplicate_key:
            message_parts.append(f"**Duplicate Key:** `{self.duplicate_key}`")

        message_parts.append(f"**Status:** {final_status} (code: {self.status})")

        if self.match_method:
            message_parts.append(f"**Match Method:** {self.match_method}")

        if not updated:
            message_parts.append(f"**Warning:** History entry not found (may not be tracked)")

        message = "  \n".join(message_parts)

        # Check if script wants to send raw data
        if self.wants_raw_data:
            raw_data = {
                "title": title,
                "message": message,
                "script_type": "post-process",
                "action": "updated",
                "nzb_name": self.nzb_name,
                "category": self.category,
                "duplicate_key": self.duplicate_key,
                "status": final_status,
                "pp_status_code": self.status,
                "filename": self.filename,
                "complete_dir": self.complete_dir,
                "match_method": self.match_method,
                "history_updated": updated,
                "timestamp": self.current_time,
                "all_env_vars": self._get_all_env_vars(),
            }
            self.notifier.send_notification_raw(raw_data)
        else:
            self.notifier.send_notification(title, message)

    def run(self) -> None:
        """
        Main execution method for post-processing script.

        Returns:
            None
        """
        self.log(f"[POST-PROCESS] Starting (Status code: {self.status})")

        # Check if this category should be ignored
        ignored_categories = self.config.get("ignored_categories", [])
        ignore_no_category = self.config.get("ignore_no_category", False)

        if self.category and self.category in ignored_categories:
            self.logger.log(f"Category '{self.category}' is in ignored list - skipping status update", LogLevel.INFO)
            sys.exit(0)

        if not self.category and ignore_no_category:
            self.logger.log("Download has no category and ignore_no_category is enabled - skipping status update", LogLevel.INFO)
            sys.exit(0)

        updated = self.update_status()

        # Optional: Send notification on completion
        # Uncomment the next line if you want notifications for every download completion
        # self.send_update_notification(updated)

        self.log(f"[POST-PROCESS] Completed")
        sys.exit(0)


if __name__ == "__main__":
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_file = os.path.join(script_dir, "prevent_download_loops.json")

        config_loader = ConfigLoader(config_file)
        script = PostProcessLoopPrevention(config_loader.config)
        script.run()

    except Exception as e:
        import traceback
        sys.stderr.write(f"CRITICAL ERROR: {e}{os.linesep}")
        sys.stderr.write(traceback.format_exc())
        sys.exit(0)  # Don't fail the download
