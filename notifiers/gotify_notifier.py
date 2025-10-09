#!/usr/bin/env python3

"""
Gotify Notifier Implementation

Sends notifications to Gotify server with markdown support.
This file should be referenced in your notifier configuration.
"""

import json
from typing import Optional, Dict, Any
from urllib.request import Request, urlopen

# Import base class from shared library
from loop_prevention_shared import NotifierInterface, Logger


class GotifyNotifier(NotifierInterface):
    """
    Send notifications to Gotify server with markdown support.

    Attributes:
        enabled (bool): Whether notifications are enabled
        url (str): Gotify server URL
        token (str): Gotify API token
        priority (int): Default notification priority
        logger (Logger): Logger instance for error logging
        name (str): Name of this notifier for logging purposes
    """

    def __init__(self, config: Dict[str, Any], logger: Optional[Logger] = None) -> None:
        """
        Initialize the GotifyNotifier.

        Args:
            config: Dictionary containing Gotify configuration
            logger: Optional Logger instance for logging errors
        """
        self.enabled = config.get("enabled", False)
        self.url = config.get("url", "")
        self.token = config.get("token", "")
        self.priority = config.get("priority", 10)
        self.logger = logger
        self.name = config.get("name", "Gotify")

    def _log(self, message: str, level: str = "ERROR") -> None:
        """
        Internal logging method.

        Args:
            message: Message to log
            level: Log level (default: "ERROR")

        Returns:
            None
        """
        if self.logger:
            self.logger.log(f"{self.name}: {message}", level)

    def send_notification(self, title: str, message: str, priority: Optional[int] = None) -> bool:
        """
        Send a notification to Gotify server.

        Args:
            title: Notification title
            message: Notification message body (supports Markdown)
            priority: Optional priority level (overrides default)

        Returns:
            True if notification was sent successfully, False otherwise
        """
        if not self.enabled:
            return False

        if not self.url or not self.token:
            self._log("Missing URL or token configuration")
            return False

        try:
            notification_priority = priority if priority is not None else self.priority
            payload = {
                "title": title,
                "message": message,
                "priority": notification_priority,
                "extras": {
                    "client::display": {
                        "contentType": "text/markdown"
                    }
                }
            }

            url = f"{self.url.rstrip('/')}/message?token={self.token}"
            req = Request(url, method='POST')
            req.add_header('Content-Type', 'application/json')
            data = json.dumps(payload).encode('utf-8')
            response = urlopen(req, data=data, timeout=10)

            if response.status == 200:
                self._log("Notification sent successfully", "INFO")
                return True
            else:
                self._log(f"Unexpected response status: {response.status}")
                return False

        except Exception as e:
            self._log(f"Error sending notification: {e}")
            return False
