#!/usr/bin/env python3

"""
Shared library for SABnzbd Download Loop Prevention Scripts
Contains common classes and utilities used by both pre-queue and post-processing scripts
"""

import os
import sys
import json
import fcntl
import ssl
from typing import Optional, Dict, Any, List
from abc import ABC, abstractmethod
from enum import Enum
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


# ===== DEFAULT CONFIGURATION =====
DEFAULT_CONFIG = {
    "time_window_minutes": 1440,
    "history_file": "/config/scripts/download_history.txt",
    "log_file": "/config/scripts/loop_prevention.log",
    "max_log_size_mb": 10,
    "max_log_backups": 3,
    "log_level": "ALL",
    "ignored_categories": [],
    "ignore_no_category": False,
    "verify_ssl": True,
    "wants_raw_data": False,  # Enable raw data dictionary for notifications
    "use_duplicate_key": True,
    "radarr_instances": [],
    "sonarr_instances": [],
    "notifier": {
        "enabled": False,
        "name": "Gotify",
        "config_file": None,
        "url": "http://localhost:80",
        "token": "your_token"
    }
}


# ===== SHARED CLASSES =====
class LogLevel(Enum):
    """Enumeration for log levels."""
    INFO = "INFO"
    ERROR = "ERROR"
    ALL = "ALL"
    NONE = "NONE"


class ConfigLoader:
    """
    Load configuration from JSON file with fallback to defaults.

    Attributes:
        config_file (str): Path to the configuration file
        config (dict): Loaded configuration dictionary
    """

    def __init__(self, config_file: str) -> None:
        """
        Initialize the ConfigLoader.

        Args:
            config_file: Path to the JSON configuration file
        """
        self.config_file = config_file
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """
        Load configuration from file with fallback to defaults.

        Returns:
            Dictionary containing configuration values
        """
        config = DEFAULT_CONFIG.copy()

        if not os.path.exists(self.config_file):
            sys.stderr.write(f"Config file not found: {self.config_file}{os.linesep}")
            sys.stderr.write(f"Using default configuration.{os.linesep}")
            # Convert log_level string to enum
            config["log_level"] = LogLevel[config["log_level"]]
            return config

        try:
            with open(self.config_file, 'r') as f:
                user_config = json.load(f)
                for key, value in user_config.items():
                    if key in config:
                        # Convert log_level string to enum
                        if key == "log_level" and isinstance(value, str):
                            try:
                                config[key] = LogLevel[value]
                            except KeyError:
                                sys.stderr.write(f"Invalid log_level '{value}', using default{os.linesep}")
                                config[key] = LogLevel.ALL
                        else:
                            config[key] = value
        except Exception as e:
            sys.stderr.write(f"Error reading config: {e}{os.linesep}")

        return config

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by key.

        Args:
            key: Configuration key to retrieve
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        return self.config.get(key, default)


class LockedFile:
    """
    Context manager for thread-safe file locking using fcntl.

    Attributes:
        filepath (str): Path to the file to lock
        mode (str): File open mode ('r', 'w', 'a', etc.)
        file: File handle
    """

    def __init__(self, filepath: str, mode: str = 'r') -> None:
        """
        Initialize the LockedFile context manager.

        Args:
            filepath: Path to the file
            mode: File open mode (default: 'r')
        """
        self.filepath = filepath
        self.mode = mode
        self.file = None

    def __enter__(self):
        """
        Enter the context and acquire file lock.

        Returns:
            File handle with exclusive lock
        """
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        self.file = open(self.filepath, self.mode)
        fcntl.flock(self.file.fileno(), fcntl.LOCK_EX)
        return self.file

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """
        Exit the context and release file lock.

        Args:
            exc_type: Exception type if raised
            exc_val: Exception value if raised
            exc_tb: Exception traceback if raised

        Returns:
            False to propagate exceptions
        """
        if self.file:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
        return False


class Logger:
    """
    Logging utility with rotation support.

    Attributes:
        log_file (str): Path to the log file
        max_size_mb (int): Maximum log file size in MB before rotation
        max_backups (int): Maximum number of backup log files to keep
        log_level (str): Logging level ('ALL', 'ERROR', 'NONE')
    """

    def __init__(self, log_file: str, max_size_mb: int, max_backups: int, log_level: LogLevel) -> None:
        """
        Initialize the Logger.

        Args:
            log_file: Path to the log file
            max_size_mb: Maximum log file size in MB before rotation
            max_backups: Maximum number of backup log files to keep
            log_level: Logging level (LogLevel enum)
        """
        self.log_file = log_file
        self.max_size_mb = max_size_mb
        self.max_backups = max_backups
        self.log_level = log_level
        self._ensure_log_exists()

    def _ensure_log_exists(self) -> None:
        """
        Ensure log file and directory exist.

        Returns:
            None
        """
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        if not os.path.exists(self.log_file):
            open(self.log_file, 'a').close()

    def _rotate_log(self) -> None:
        """
        Rotate log file if size exceeds maximum.

        Returns:
            None
        """
        try:
            if not os.path.exists(self.log_file):
                return

            file_size_mb = os.path.getsize(self.log_file) / (1024 * 1024)
            if file_size_mb < self.max_size_mb:
                return

            if self.max_backups <= 0:
                open(self.log_file, 'w').close()
                return

            for i in range(self.max_backups - 1, 0, -1):
                old_backup = f"{self.log_file}.{i}"
                new_backup = f"{self.log_file}.{i + 1}"
                if os.path.exists(old_backup):
                    if i == self.max_backups - 1:
                        os.remove(old_backup)
                    else:
                        os.rename(old_backup, new_backup)

            if os.path.exists(self.log_file):
                os.rename(self.log_file, f"{self.log_file}.1")

            open(self.log_file, 'a').close()
        except Exception as e:
            sys.stderr.write(f"Log rotation error: {e}{os.linesep}")

    def log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        """
        Write a log message with timestamp and level.

        Args:
            message: Log message to write
            level: Log level ('INFO', 'ERROR', etc.)

        Returns:
            None
        """
        if self.log_level == LogLevel.NONE:
            return

        if self.log_level == LogLevel.ERROR and level != LogLevel.ERROR:
            return

        import time
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        log_line = f"[{timestamp}] [{level}] {message}{os.linesep}"

        try:
            self._rotate_log()
            with LockedFile(self.log_file, 'a') as f:
                f.write(log_line)
        except Exception as e:
            sys.stderr.write(f"Logging error: {e}{os.linesep}")


class NotifierInterface(ABC):
    """
    Abstract base class for notification services.

    All notifier implementations must inherit from this class and implement
    the send_notification method. Optionally, they can implement
    send_notification_raw for receiving structured data.

    Attributes:
        config (dict): Notifier configuration dictionary
        logger (Optional[Logger]): Logger instance
    """

    def __init__(self, config: Dict[str, Any], logger: Optional[Logger] = None) -> None:
        """
        Initialize the NotifierInterface.

        Args:
            config: Notifier configuration dictionary
            logger: Optional Logger instance
        """
        self.config = config
        self.logger = logger

    @abstractmethod
    def send_notification(self, title: str, message: str) -> bool:
        """
        Send a notification with the given title and message.

        Args:
            title: Notification title
            message: Notification message body

        Returns:
            True if notification was sent successfully, False otherwise
        """
        pass

    def send_notification_raw(self, raw_data: Dict[str, Any]) -> bool:
        """
        Send a notification with raw structured data.

        This method is optional to implement. If not overridden, it will
        fall back to the standard send_notification method using the
        'title' and 'message' keys from raw_data.

        Args:
            raw_data: Dictionary containing all available notification data
                Common keys:
                    - title (str): Notification title
                    - message (str): Formatted message
                    - script_type (str): 'pre-queue' or 'post-process'
                    - action (str): Action taken (e.g., 'blocked', 'updated')
                    - nzb_name (str): Name of the NZB
                    - category (str): SABnzbd category
                    - duplicate_key (str): Duplicate detection key
                    - status (str): Current status
                    - duplicate_status (str): Status of duplicate (pre-queue only)
                    - duplicate_timestamp (int): Timestamp of duplicate (pre-queue only)
                    - duplicate_age_minutes (int): Age in minutes (pre-queue only)
                    - blocked_instance (str): Where blocked (pre-queue only)
                    - time_window_minutes (int): Detection window (pre-queue only)
                    - pp_status_code (str): Post-process status code (post-process only)
                    - match_method (str): How match was found (post-process only)
                    - history_updated (bool): If history was updated (post-process only)
                    - timestamp (int): Current timestamp
                    - all_env_vars (dict): All SABnzbd environment variables

        Returns:
            True if notification was sent successfully, False otherwise
        """
        # Default implementation: fall back to standard notification
        title = raw_data.get("title", "Notification")
        message = raw_data.get("message", "")

        return self.send_notification(title, message)


def load_notifier_from_file(filepath: str, config: Dict[str, Any], logger: Optional[Logger] = None) -> Optional[NotifierInterface]:
    """
    Dynamically load a notifier implementation from an external Python file.

    The file must contain a class that inherits from NotifierInterface.

    Args:
        filepath: Path to the Python file containing the notifier implementation
        config: Configuration dictionary to pass to the notifier constructor
        logger: Optional Logger instance

    Returns:
        An instance of the notifier class, or None if loading failed
    """
    notifier_name = config.get("name", "Unknown")

    try:
        if not os.path.exists(filepath):
            if logger:
                logger.log(f"Notifier file not found: {filepath}", LogLevel.ERROR)
            return None

        with open(filepath, 'r') as f:
            code = f.read()

        namespace = {
            'NotifierInterface': NotifierInterface,
            'Logger': Logger,
            'Optional': Optional,
            'Dict': Dict,
            'Any': Any,
            '__name__': '__notifier_module__'
        }

        exec(code, namespace)

        notifier_class = None
        for name, obj in namespace.items():
            if (isinstance(obj, type) and
                issubclass(obj, NotifierInterface) and
                obj != NotifierInterface):
                notifier_class = obj
                break

        if notifier_class is None:
            if logger:
                logger.log(f"{notifier_name}: No NotifierInterface implementation found in file", LogLevel.ERROR)
            return None

        if logger:
            logger.log(f"{notifier_name}: Successfully loaded from {filepath}", LogLevel.INFO)

        return notifier_class(config, logger)

    except Exception as e:
        if logger:
            logger.log(f"{notifier_name}: Error loading from file: {e}", LogLevel.ERROR)
        return None


def create_notifier(config: Dict[str, Any], logger: Optional[Logger] = None) -> Optional[NotifierInterface]:
    """
    Factory function to create a notifier from configuration.

    Loads the notifier implementation from the file specified in config_file.

    Args:
        config: Notifier configuration dictionary
        logger: Optional Logger instance

    Returns:
        An instance of NotifierInterface, or None if creation failed or disabled
    """
    if not config.get("enabled", False):
        return None

    config_file = config.get("config_file")
    notifier_name = config.get("name", "Unknown")

    if not config_file:
        if logger:
            logger.log(f"{notifier_name}: No config_file specified in notifier configuration", LogLevel.ERROR)
        return None

    return load_notifier_from_file(config_file, config, logger)


# ===== UTILITY FUNCTIONS =====
def ensure_file_exists(filepath: str) -> None:
    """
    Ensure a file exists, creating it and parent directories if needed.

    Args:
        filepath: Path to the file

    Returns:
        None
    """
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    if not os.path.exists(filepath):
        open(filepath, 'a').close()


def clean_old_entries(history_file: str, time_window_seconds: int, current_time: int) -> None:
    """
    Remove entries older than time window from history file.

    Args:
        history_file: Path to the history file
        time_window_seconds: Time window in seconds
        current_time: Current timestamp

    Returns:
        None
    """
    try:
        with LockedFile(history_file, 'r') as f:
            lines = f.readlines()

        new_lines = []
        for line in lines:
            parts = line.strip().split('|')
            if len(parts) < 4:
                continue

            try:
                timestamp = int(parts[0])
                age = current_time - timestamp
                if age < time_window_seconds:
                    new_lines.append(line)
            except (ValueError, IndexError):
                continue

        with LockedFile(history_file, 'w') as f:
            f.writelines(new_lines)

    except Exception as e:
        sys.stderr.write(f"Error cleaning entries: {e}{os.linesep}")
