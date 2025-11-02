#!/usr/bin/env python3

"""
SABnzbd Download Loop Prevention - PRE-QUEUE Script

Checks for duplicate downloads and blocks loops.
Uses background subprocess to handle blocking asynchronously.

Uses shared library for common functionality
"""

import os
import sys
import time
import json
import ssl
import traceback
import subprocess
from typing import Optional, Dict, Any
from urllib.request import Request, urlopen

# Import shared library
from loop_prevention_shared import (
    ConfigLoader, LockedFile, Logger, LogLevel, DownloadStatus, NotifierInterface, create_notifier,
    ensure_file_exists, clean_old_entries
)


class PreQueueLoopPrevention:
    """
    Pre-queue script to prevent download loops in SABnzbd.

    Checks for duplicate downloads before they are added to the queue and
    uses background subprocess to block them via the queue endpoint.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """Initialize the PreQueueLoopPrevention script."""
        self.config = config
        self.current_time = int(time.time())

        # Load config values
        self.time_window_minutes = config.get("time_window_minutes")
        self.time_window_seconds = self.time_window_minutes * 60
        self.history_file = config.get("history_file")
        self.verify_ssl = config.get("verify_ssl")
        self.use_duplicate_key = config.get("use_duplicate_key", True)
        self.radarr_instances = config.get("radarr_instances", [])
        self.sonarr_instances = config.get("sonarr_instances", [])
        self.wants_raw_data = config.get("wants_raw_data", False)

        # Initialize shared components
        self.logger = Logger(
            config.get("log_file"),
            config.get("max_log_size_mb"),
            config.get("max_log_backups"),
            config.get("log_level")
        )

        # Initialize notifier
        self.notifier = create_notifier(config.get("notifier", {}), self.logger)

        # Get SABnzbd environment variables
        self.nzb_name = os.environ.get('SAB_FINAL_NAME', '')
        self.category = os.environ.get('SAB_CAT', '')
        self.duplicate_key = os.environ.get('SAB_DUPLICATE_KEY', '')

        # Get SABnzbd API URL and key
        self.sabnzbd_api_url = os.environ.get('SAB_API_URL', 'http://localhost:8080/api')
        self.sabnzbd_api_key = os.environ.get('SAB_API_KEY', '')

        # Create SSL context
        self._setup_ssl_context()

        # Track duplicate info for notifications
        self.duplicate_timestamp = None
        self.duplicate_status = None

        ensure_file_exists(self.history_file)

        self.log(f"SABnzbd API URL: {self.sabnzbd_api_url} (API Key: {'***' if self.sabnzbd_api_key else 'NOT SET'})")

    def _setup_ssl_context(self) -> None:
        """Setup SSL context based on verify_ssl configuration."""
        try:
            if self.verify_ssl:
                self.ssl_context = ssl.create_default_context()
                self.logger.log("SSL verification enabled (verified certs only)", LogLevel.INFO)
            else:
                self.ssl_context = ssl._create_unverified_context()
                self.logger.log("SSL verification disabled (self-signed certs allowed)", LogLevel.INFO)
        except Exception as e:
            self.logger.log(f"Error setting up SSL context: {e}", LogLevel.ERROR)
            self.ssl_context = ssl._create_unverified_context()

    def log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        """Log a message using the logger."""
        self.logger.log(message, level)

    def add_to_history(self) -> None:
        """Add download with PENDING status to history file."""
        try:
            with LockedFile(self.history_file, 'a') as f:
                f.write(f"{self.current_time}|{self.category}|{self.nzb_name}|{self.duplicate_key}|{DownloadStatus.PENDING.value}{os.linesep}")
        except Exception as e:
            self.log(f"Error adding to history: {e}", LogLevel.ERROR)

    def check_duplicate(self) -> bool:
        """Check if download already exists with PENDING or SUCCESS status."""
        try:
            with LockedFile(self.history_file, 'r') as f:
                lines = f.readlines()
        except Exception as e:
            self.log(f"Error reading history: {e}", LogLevel.ERROR)
            return False

        for line in lines:
            parts = line.strip().split('|')
            if len(parts) < 5:
                continue

            timestamp, category, name, dupe_key, status = parts[0], parts[1], parts[2], parts[3], parts[4]

            # Match by duplicate_key or name
            match = False
            if self.use_duplicate_key and self.duplicate_key and dupe_key and dupe_key == self.duplicate_key:
                match = True
                self.log(f"Matched by duplicate_key: {dupe_key}")
            elif not self.duplicate_key and name == self.nzb_name:
                match = True
                self.log(f"Matched by name: {name}")

            if match:
                try:
                    age = self.current_time - int(timestamp)
                    self.duplicate_timestamp = int(timestamp)
                    self.duplicate_status = status
                except (ValueError, TypeError):
                    continue

                if age < self.time_window_seconds:
                    self.log(f"DUPLICATE: Found with status '{status}' from {age // 60} min ago")

                    if status == DownloadStatus.SUCCESS.value:
                        self.log(f"Status is {DownloadStatus.SUCCESS.value} - BLOCKING")
                        return True
                    elif status == DownloadStatus.PENDING.value:
                        self.log(f"Status is {DownloadStatus.PENDING.value} - BLOCKING (download in progress)")
                        return True
                    elif status == DownloadStatus.FAILED.value:
                        self.log(f"Status is {DownloadStatus.FAILED.value} - ALLOWING retry")
                        return False
                    else:
                        self.log(f"Unknown status '{status}' - BLOCKING")
                        return True

        return False

    def send_block_notification(self) -> None:
        """Send notification about blocked download."""
        if not self.notifier:
            return

        title = "🚫 Download Loop Prevented"

        if self.duplicate_timestamp:
            from datetime import datetime
            original_time = datetime.fromtimestamp(self.duplicate_timestamp).strftime('%Y-%m-%d %H:%M:%S')
            minutes_ago = (self.current_time - self.duplicate_timestamp) // 60
        else:
            original_time = "Unknown"
            minutes_ago = 0

        message_parts = [
            f"**Download:** `{self.nzb_name}`",
            f"**Category:** `{self.category or 'None'}`",
        ]

        if self.duplicate_key:
            message_parts.append(f"**Duplicate Key:** `{self.duplicate_key}`")

        message_parts.append(f"**First Seen:** {original_time} ({minutes_ago} min ago)")
        message_parts.append(f"**Status:** {self.duplicate_status}")
        message_parts.append(f"**Action:** Background subprocess blocking (pause + queue removal)")
        message_parts.append(f"**Window:** {self.time_window_minutes} minutes")

        message = " \n".join(message_parts)

        # Check if script wants to send raw data
        if self.wants_raw_data:
            all_sab_vars = {k: v for k, v in os.environ.items() if k.startswith('SAB_')}
            raw_data = {
                "title": title,
                "message": message,
                "script_type": "pre-queue",
                "action": "blocked",
                "nzb_name": self.nzb_name,
                "category": self.category,
                "duplicate_key": self.duplicate_key,
                "duplicate_status": self.duplicate_status,
                "duplicate_timestamp": self.duplicate_timestamp,
                "duplicate_age_minutes": minutes_ago,
                "duplicate_age_seconds": self.current_time - self.duplicate_timestamp if self.duplicate_timestamp else None,
                "time_window_minutes": self.time_window_minutes,
                "timestamp": self.current_time,
                "all_env_vars": all_sab_vars,
            }
            self.notifier.send_notification_raw(raw_data)
        else:
            self.notifier.send_notification(title, message)

    def print_sabnzbd_response(self, accept: bool = True) -> None:
        """Print SABnzbd pre-queue response."""
        if accept:
            for _ in range(7):
                print("")
        else:
            print("0")
            for _ in range(6):
                print("")

    def _spawn_blocker_subprocess(self) -> None:
        """Spawn blocker subprocess asynchronously."""
        try:
            task_data = {
                "sabnzbd_api_url": self.sabnzbd_api_url,
                "sabnzbd_api_key": self.sabnzbd_api_key,
                "radarr_instances": self.radarr_instances,
                "sonarr_instances": self.sonarr_instances,
                "nzb_name": self.nzb_name,
                "log_file": self.config.get("log_file"),
                "verify_ssl": self.verify_ssl
            }

            script_dir = os.path.dirname(os.path.abspath(__file__))
            blocker_script = os.path.join(script_dir, "queue_blocker_task.py")

            self.log("Spawning blocker subprocess")

            subprocess.Popen(
                [sys.executable, blocker_script, json.dumps(task_data)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )

            self.log("Blocker subprocess spawned successfully")
        except Exception as e:
            self.log(f"Error spawning blocker: {e}", LogLevel.ERROR)
            self.log(traceback.format_exc(), LogLevel.ERROR)

    def run(self) -> None:
        """Main execution method for pre-queue script."""
        self.log(f"[PRE-QUEUE] Processing: {self.nzb_name} (Category: {self.category})")

        # Check if this category should be ignored
        ignored_categories = self.config.get("ignored_categories", [])
        ignore_no_category = self.config.get("ignore_no_category", False)

        if self.category and self.category in ignored_categories:
            self.log(f"Category '{self.category}' is in ignored list - accepting without loop check", LogLevel.INFO)
            self.print_sabnzbd_response(accept=True)
            return

        if not self.category and ignore_no_category:
            self.log("Download has no category - accepting without loop check", LogLevel.INFO)
            self.print_sabnzbd_response(accept=True)
            return

        # Clean old entries
        clean_old_entries(self.history_file, self.time_window_seconds, self.current_time)

        # Check for duplicates
        is_duplicate = self.check_duplicate()

        if is_duplicate:
            # Duplicate detected
            self.log("BLOCKING: Duplicate detected")

            if self.duplicate_status == DownloadStatus.SUCCESS.value:
                self.log(f"Status is {DownloadStatus.SUCCESS.value} - accepting into SABnzbd and spawning blocker")
                self.print_sabnzbd_response(accept=True)
                self._spawn_blocker_subprocess()
            else:
                self.log(f"Status is {self.duplicate_status} - rejecting from SABnzbd")
                self.print_sabnzbd_response(accept=False)

            self.send_block_notification()
            sys.exit(0)

        else:
            # Not a duplicate - add to history and accept
            self.add_to_history()
            self.log(f"ACCEPTED: Added with {DownloadStatus.PENDING.value} status")
            self.print_sabnzbd_response(accept=True)
            sys.exit(0)


if __name__ == "__main__":
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_file = os.path.join(script_dir, "prevent_download_loops.json")
        config_loader = ConfigLoader(config_file)
        script = PreQueueLoopPrevention(config_loader.config)
        script.run()
    except Exception as e:
        sys.stderr.write(f"CRITICAL ERROR: {e}{os.linesep}")
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)
