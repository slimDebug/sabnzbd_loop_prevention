#!/usr/bin/env python3
"""
Example Custom Notifier - Template for creating your own notifier
"""
from typing import Optional, Dict, Any
from loop_prevention_shared import NotifierInterface, Logger

class CustomNotifier(NotifierInterface):
    def __init__(self, config: Dict[str, Any], logger: Optional[Logger] = None) -> None:
        """
        Initialize the notifier.

        Args:
            config: Configuration dictionary
            logger: Optional logger instance (don't worry, we pass one to you by default)
        """
        self.enabled = config.get("enabled", False)
        self.logger = logger
        self.name = config.get("name", "CustomNotifier")

        # Extract your custom configuration fields (webhook_url, api_key, etc.)
        self.your_field = config.get("your_field", "default_value")
        # Add as many fields as you need!

    def _log(self, message: str, level: str = "ERROR") -> None:
        """Log a message with the notifier name."""
        if self.logger:
            self.logger.log(f"{self.name}: {message}", level)

    def send_notification(self, title: str, message: str, priority: Optional[int] = None) -> bool:
        """
        Send a notification.

        Args:
            title: Notification title
            message: Notification message body
            priority: Optional priority level

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            return False
        try:
            # Your notification logic here
            # Example: HTTP POST, API call, etc.
            self._log(f"Would send: {title}", "INFO")
            return True
        except Exception as e:
            self._log(f"Error: {e}")
            return False

    def send_notification_raw(self, raw_data: Dict[str, Any]) -> bool:
        """Enhanced notification with full data access."""
        # Access all available data
        script_type = raw_data.get("script_type")  # 'pre-queue' or 'post-process'
        action = raw_data.get("action")  # 'blocked' or 'updated'
        nzb_name = raw_data.get("nzb_name")
        category = raw_data.get("category")

        # Pre-queue specific data
        if script_type == "pre-queue":
            duplicate_age = raw_data.get("duplicate_age_minutes")
            blocked_instance = raw_data.get("blocked_instance")

        # Post-process specific data
        elif script_type == "post-process":
            match_method = raw_data.get("match_method")
            pp_status_code = raw_data.get("pp_status_code")

        # All SABnzbd environment variables
        env_vars = raw_data.get("all_env_vars", {})

        # Send your custom notification with all data
        # ... your implementation ...

        return True
