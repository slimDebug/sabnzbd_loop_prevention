#!/usr/bin/env python3

"""
SABnzbd Download Loop Prevention - PRE-QUEUE Script
Checks for duplicate downloads and blocks loops
Uses shared library for common functionality
"""

import os
import sys
import time
from datetime import datetime
import ssl
from typing import Optional, Tuple, List, Dict, Any
from urllib.request import Request, urlopen

# Import shared library
from loop_prevention_shared import (
    ConfigLoader, LockedFile, Logger, NotifierInterface, create_notifier,
    ensure_file_exists, clean_old_entries
)


class PreQueueLoopPrevention:
    """
    Pre-queue script to prevent download loops in SABnzbd.
    
    Checks for duplicate downloads before they are added to the queue and
    can optionally block them in Radarr/Sonarr if detected.
    
    Attributes:
        config (dict): Configuration dictionary
        current_time (int): Current Unix timestamp
        time_window_minutes (int): Time window for duplicate detection in minutes
        time_window_seconds (int): Time window in seconds
        history_file (str): Path to download history file
        verify_ssl (bool): Whether to verify SSL certificates
        radarr_instances (list): List of Radarr instance configurations
        sonarr_instances (list): List of Sonarr instance configurations
        wants_raw_data (bool): Whether to send raw data to notifier
        logger (Logger): Logger instance
        notifier (NotifierInterface): Notifier instance
        nzb_name (str): Name of the NZB from SABnzbd environment
        category (str): Category from SABnzbd environment
        duplicate_key (str): Duplicate key from SABnzbd environment
        ssl_context: SSL context for HTTPS requests
        duplicate_timestamp (int): Timestamp of duplicate entry
        duplicate_status (str): Status of duplicate entry
    """
    
    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Initialize the PreQueueLoopPrevention script.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.current_time = int(time.time())
        
        # Load config values
        self.time_window_minutes = config.get("time_window_minutes")
        self.time_window_seconds = self.time_window_minutes * 60
        self.history_file = config.get("history_file")
        self.verify_ssl = config.get("verify_ssl")
        self.radarr_instances = config.get("radarr_instances")
        self.sonarr_instances = config.get("sonarr_instances")
        self.wants_raw_data = config.get("wants_raw_data", False)
        
        # Initialize shared components
        self.logger = Logger(
            config.get("log_file"),
            config.get("max_log_size_mb"),
            config.get("max_log_backups"),
            config.get("log_level")
        )
        
        # Initialize notifier using factory
        self.notifier = create_notifier(config.get("notifier", {}), self.logger)
        
        # Get SABnzbd environment variables
        self.nzb_name = os.environ.get('SAB_FINAL_NAME', '')
        self.category = os.environ.get('SAB_CAT', '')
        self.duplicate_key = os.environ.get('SAB_DUPLICATE_KEY', '')
        
        # Create SSL context
        if self.verify_ssl:
            self.ssl_context = ssl.create_default_context()
        else:
            self.ssl_context = ssl._create_unverified_context()
        
        # Track duplicate info for notifications
        self.duplicate_timestamp = None
        self.duplicate_status = None
        
        ensure_file_exists(self.history_file)
    
    def log(self, message: str, level: str = "INFO") -> None:
        """
        Log a message using the logger.
        
        Args:
            message: Message to log
            level: Log level (default: "INFO")
        
        Returns:
            None
        """
        self.logger.log(message, level)
    
    def add_to_history(self) -> None:
        """
        Add download with PENDING status to history file.
        
        Returns:
            None
        """
        try:
            with LockedFile(self.history_file, 'a') as f:
                f.write(f"{self.current_time}|{self.category}|{self.nzb_name}|{self.duplicate_key}|PENDING{os.linesep}")
        except Exception as e:
            self.log(f"Error adding to history: {e}", "ERROR")
    
    def check_duplicate(self) -> bool:
        """
        Check if download already exists with PENDING or SUCCESS status.
        
        Returns:
            True if duplicate is found and should be blocked, False otherwise
        """
        try:
            with LockedFile(self.history_file, 'r') as f:
                lines = f.readlines()
        except Exception as e:
            self.log(f"Error reading history: {e}", "ERROR")
            return False
        
        for line in lines:
            parts = line.strip().split('|')
            if len(parts) < 5:
                continue
            
            timestamp, category, name, dupe_key, status = parts[0], parts[1], parts[2], parts[3], parts[4]
            
            # Match by duplicate_key or name
            match = False
            if self.duplicate_key and dupe_key and dupe_key == self.duplicate_key:
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
                    
                    if status == "SUCCESS":
                        self.log("Status is SUCCESS - BLOCKING")
                        return True
                    elif status == "PENDING":
                        self.log("Status is PENDING - BLOCKING (download in progress)")
                        return True
                    elif status == "FAILED":
                        self.log("Status is FAILED - ALLOWING retry")
                        return False
                    else:
                        self.log(f"Unknown status '{status}' - BLOCKING")
                        return True
        
        return False
    
    def find_instance_by_category(self, instances: List[Dict[str, Any]], category: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Find instance configuration by category.
        
        Args:
            instances: List of instance configurations
            category: Category to match
        
        Returns:
            Tuple of (url, api_key) or (None, None) if not found
        """
        for instance in instances:
            if instance.get("category") == category:
                return instance.get("url"), instance.get("api_key")
        return None, None
    
    def get_all_queue_items(self, url: str, api_key: str) -> List[Dict[str, Any]]:
        """
        Fetch all queue items from Radarr/Sonarr API with pagination.
        
        Args:
            url: Base URL of the *arr instance
            api_key: API key for authentication
        
        Returns:
            List of queue item dictionaries
        """
        all_records = []
        page = 1
        page_size = 1000
        
        while page <= 50:
            try:
                req_url = f"{url}/api/v3/queue?page={page}&pageSize={page_size}"
                req = Request(req_url)
                req.add_header('X-Api-Key', api_key)
                req.add_header('Content-Type', 'application/json')
                
                response = urlopen(req, timeout=10, context=self.ssl_context)
                
                import json
                data = json.loads(response.read().decode('utf-8'))
                records = data.get('records', [])
                
                if not records:
                    break
                
                all_records.extend(records)
                
                if len(records) < page_size:
                    break
                
                page += 1
            
            except Exception as e:
                self.log(f"Error fetching queue: {e}", "ERROR")
                break
        
        return all_records
    
    def find_queue_item_id(self, queue_items: List[Dict[str, Any]], title: str) -> Optional[int]:
        """
        Find queue item ID by title or download ID.
        
        Args:
            queue_items: List of queue item dictionaries
            title: Title to search for
        
        Returns:
            Queue item ID or None if not found
        """
        # Exact match first
        for item in queue_items:
            if item.get('title') == title or item.get('downloadId') == title:
                return item.get('id')
        
        # Partial match fallback
        for item in queue_items:
            item_title = item.get('title', '')
            if title in item_title or item_title in title:
                return item.get('id')
        
        return None
    
    def block_in_arr(self, url: str, api_key: str, arr_type: str) -> bool:
        """
        Block the download in Radarr/Sonarr by removing from queue and adding to blocklist.
        
        Args:
            url: Base URL of the *arr instance
            api_key: API key for authentication
            arr_type: Type of *arr application ("Radarr" or "Sonarr")
        
        Returns:
            True if successfully blocked, False otherwise
        """
        self.log(f"Attempting to block in {arr_type}: {url}")
        
        all_queue_items = self.get_all_queue_items(url, api_key)
        if not all_queue_items:
            self.log(f"No queue items in {arr_type}")
            return False
        
        queue_id = self.find_queue_item_id(all_queue_items, self.nzb_name)
        if not queue_id:
            self.log(f"Could not find queue item")
            return False
        
        try:
            delete_url = f"{url}/api/v3/queue/{queue_id}?removeFromClient=true&blocklist=true"
            req = Request(delete_url, method='DELETE')
            req.add_header('X-Api-Key', api_key)
            urlopen(req, timeout=10, context=self.ssl_context)
            
            self.log(f"Blocked in {arr_type} (Queue ID: {queue_id})")
            return True
        
        except Exception as e:
            self.log(f"Error blocking: {e}", "ERROR")
            return False
    
    def try_block_in_instances(self, instances: List[Dict[str, Any]], app_name: str) -> Tuple[bool, Optional[str]]:
        """
        Try to block download in configured instances.
        
        Args:
            instances: List of instance configurations
            app_name: Application name ("Radarr" or "Sonarr")
        
        Returns:
            Tuple of (success, instance_info_string)
        """
        url, api_key = self.find_instance_by_category(instances, self.category)
        if url and api_key:
            if self.block_in_arr(url, api_key, app_name):
                return True, f"{app_name} - {self.category} ({url})"
        return False, None
    
    def _get_all_env_vars(self) -> Dict[str, str]:
        """
        Get all SABnzbd-related environment variables.
        
        Returns:
            Dictionary of all SAB_* environment variables
        """
        return {key: value for key, value in os.environ.items() if key.startswith('SAB_')}
    
    def send_block_notification(self, blocked_instance: Optional[str] = None) -> None:
        """
        Send notification about blocked download.
        
        Args:
            blocked_instance: Optional string describing where the download was blocked
        
        Returns:
            None
        """
        if not self.notifier:
            return
        
        title = "🚫 Download Loop Prevented"
        
        if self.duplicate_timestamp:
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
        
        if blocked_instance:
            message_parts.append(f"**Blocked In:** {blocked_instance}")
        else:
            message_parts.append(f"**Action:** Download refused at SABnzbd")
        
        message_parts.append(f"**Window:** {self.time_window_minutes} minutes")
        
        message = "  \n".join(message_parts)
        
        # Check if script wants to send raw data
        if self.wants_raw_data:
            raw_data = {
                "title": title,
                "message": message,
                "priority": self.config.get("notifier", {}).get("priority"),
                "script_type": "pre-queue",
                "action": "blocked",
                "nzb_name": self.nzb_name,
                "category": self.category,
                "duplicate_key": self.duplicate_key,
                "duplicate_status": self.duplicate_status,
                "duplicate_timestamp": self.duplicate_timestamp,
                "duplicate_age_minutes": minutes_ago,
                "duplicate_age_seconds": self.current_time - self.duplicate_timestamp if self.duplicate_timestamp else None,
                "blocked_instance": blocked_instance,
                "time_window_minutes": self.time_window_minutes,
                "timestamp": self.current_time,
                "all_env_vars": self._get_all_env_vars(),
            }
            self.notifier.send_notification_raw(raw_data)
        else:
            self.notifier.send_notification(title, message)
    
    def print_sabnzbd_response(self, accept: bool = True) -> None:
        """
        Print SABnzbd pre-queue response.
        
        Args:
            accept: Whether to accept (True) or reject (False) the download
        
        Returns:
            None
        """
        if accept:
            for _ in range(7):
                print("")
        else:
            print("0")
            for _ in range(6):
                print("")
    
    def run(self) -> None:
        """
        Main execution method for pre-queue script.
        
        Returns:
            None
        """
        self.log(f"[PRE-QUEUE] Processing: {self.nzb_name} (Category: {self.category})")
        
        # Clean old entries
        clean_old_entries(self.history_file, self.time_window_seconds, self.current_time)
        
        if self.check_duplicate():
            self.print_sabnzbd_response(accept=False)
            self.log("BLOCKING: Duplicate detected")
            
            blocked_instance = None
            
            # Only block in *arr if status is SUCCESS
            if self.duplicate_status == "SUCCESS":
                success, instance_info = self.try_block_in_instances(self.radarr_instances, "Radarr")
                if success:
                    blocked_instance = instance_info
                else:
                    success, instance_info = self.try_block_in_instances(self.sonarr_instances, "Sonarr")
                    if success:
                        blocked_instance = instance_info
            else:
                self.log(f"Status is {self.duplicate_status} - not removing from *arr")
            
            self.send_block_notification(blocked_instance)
            sys.exit(0)
        
        else:
            self.add_to_history()
            self.log(f"ACCEPTED: Added with PENDING status")
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
        import traceback
        sys.stderr.write(f"CRITICAL ERROR: {e}{os.linesep}")
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)
