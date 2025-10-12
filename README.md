# SABnzbd Download Loop Prevention

Prevent download loops in SABnzbd by tracking and blocking duplicate downloads. Integrates with Radarr/Sonarr to automatically blocklist repeated failed downloads and supports flexible notification services.

## Table of Contents

- [Features](#features)
- [Problem This Solves](#problem-this-solves)
- [How It Works](#how-it-works)
- [Installation](#installation)
- [Configuration](#configuration)
- [Notifier Support](#notifier-support)
- [Creating Custom Notifiers](#creating-custom-notifiers)
- [Folder Structure](#folder-structure)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## Features

✅ **Automatic Loop Detection** - Tracks downloads and prevents duplicates within a configurable time window  
✅ **Radarr/Sonarr Integration** - Automatically blocklists repeated downloads in *arr applications  
✅ **Flexible Notifications** - Plugin-based notifier system supports any notification service  
✅ **Zero External Dependencies** - Uses only Python standard library  
✅ **Docker Ready** - Designed for containerized SABnzbd deployments  
✅ **Type Safe** - Full type hints throughout the codebase  
✅ **Status Tracking** - Differentiates between SUCCESS, PENDING, and FAILED downloads  

## Problem This Solves

When Radarr or Sonarr sends a download to SABnzbd that fails (incomplete, password-protected, corrupted), the *arr application may automatically search for another release and send it again. If the same release gets sent repeatedly, it creates a **download loop** that:

- Wastes bandwidth and disk space
- Clutters SABnzbd's history
- Causes unnecessary API calls
- May eventually grab the same bad release repeatedly

This script **detects and blocks** these loops by:

1. **Tracking** every download with timestamp and status
2. **Blocking** duplicates within a configurable time window
3. **Automatically blocklisting** in Radarr/Sonarr to stop the loop
4. **Notifying** you when loops are detected (optional)

## How It Works

### Pre-Queue Script (Before Download Starts)

1. SABnzbd calls the pre-queue script when a new download is added
2. Script checks if this download (by name or duplicate key) exists in recent history
3. If found with `SUCCESS` or `PENDING` status within the time window → **BLOCKS**
4. If found with `FAILED` status → **ALLOWS** (giving it another chance)
5. If not found → **ALLOWS** and adds to history with `PENDING` status
6. Optionally removes from Radarr/Sonarr queue and adds to blocklist
7. Sends notification if blocked (configurable)

### Post-Processing Script (After Download Completes)

1. SABnzbd calls the post-processing script when download finishes
2. Script finds the matching `PENDING` entry in history
3. Updates status to `SUCCESS` (if completed) or `FAILED` (if failed)
4. This status is used by future pre-queue checks

### Time Window

Downloads are tracked for a configurable period (default: 24 hours). After this window expires, old entries are cleaned up and the same download can be attempted again.

## Installation

### 1. Download Files

Clone this repository or download the files:

```bash
git clone https://github.com/slimDebug/sabnzbd_loop_prevention.git
cd sabnzbd_loop_prevention
```

### 2. Copy to SABnzbd Scripts Directory

For Docker deployments (recommended structure):

```bash
mkdir -p /config/scripts/loop_prevention
cp prevent_download_loops_prequeue.py /config/scripts/loop_prevention/
cp prevent_download_loops_postprocess.py /config/scripts/loop_prevention/
cp loop_prevention_shared.py /config/scripts/loop_prevention/
mkdir -p /config/scripts/loop_prevention/notifiers
cp notifiers/your_custom_notifier.py /config/scripts/loop_prevention/notifiers/
```

### 3. Make Scripts Executable

```bash
chmod +x /config/scripts/loop_prevention/prevent_download_loops_*.py
```

### 4. Configure

Create your configuration file:

```bash
cp prevent_download_loops_example.json /config/scripts/loop_prevention/prevent_download_loops.json
```

Edit the configuration file with your settings (see [Configuration](#configuration)).

### 5. Configure SABnzbd

In SABnzbd Settings → Queue:

- **Pre-queue script**: `/config/scripts/loop_prevention/prevent_download_loops_prequeue.py`

In SABnzbd Settings → Categories:

- Add the **Post-queue script** to every category: `/config/scripts/loop_prevention/prevent_download_loops_postprocess.py`

In SABnzbd Settings → Post processing:

- You need to disable `Post-Process Only Verified Jobs` in order for the scripts to work properly.

## Configuration

Create a `prevent_download_loops.json` file (remove comments (starting with `//`) before using it). If you have trouble you can find examples [in the example folder](/examples/config_examples.txt):

```json
{
  // Time window for duplicate detection (in minutes)
  "time_window_minutes": 1440,  // 24 hours

  // File paths (adjust for your Docker mount)
  "history_file": "/config/scripts/loop_prevention/download_history.txt",
  "log_file": "/config/scripts/loop_prevention/loop_prevention.log",

  // Logging configuration
  "max_log_size_mb": 10,
  "max_log_backups": 3,
  "log_level": "ALL",  // Options: "ALL", "ERROR", "INFO", "NONE"

  // Define ignored categories
  "ignored_categories": ["manual", "special"],
  "ignore_no_category": false,

  // SSL verification for *arr API calls
  "verify_ssl": true,

  // Pass all data we have to the notifier so you can build your own message
  "wants_raw_data": false,

  // Do we want to use the duplicate key for matching?
  "use_duplicate_key": true,

  // Radarr instances (for automatic blocklisting)
  "radarr_instances": [
    {
      "category": "movies",  // SABnzbd category
      "url": "http://radarr:7878",
      "api_key": "your_radarr_api_key"
    }
  ],

  // Sonarr instances (for automatic blocklisting)
  "sonarr_instances": [
    {
      "category": "tv",  // SABnzbd category
      "url": "http://sonarr:8989",
      "api_key": "your_sonarr_api_key"
    }
  ],

  // Notifier configuration (see Notifier Support section)
  "notifier": {
    "enabled": true,
    "name": "Gotify",
    "config_file": "/config/scripts/loop_prevention/notifiers/gotify_notifier.py",
    "url": "http://gotify:80",
    "token": "your_gotify_token",
    "priority": 8
  }
}
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `time_window_minutes` | int | 1440 | How long to track downloads (minutes) |
| `history_file` | string | - | Path to download history file |
| `log_file` | string | - | Path to log file |
| `max_log_size_mb` | int | 10 | Max log file size before rotation |
| `max_log_backups` | int | 3 | Number of rotated log files to keep |
| `log_level` | string | "ALL" | Logging level: ALL, ERROR, INFO or NONE |
| `ignored_categories` | array | [] | Categories that will be ignored |
| `ignore_no_category` | bool | false | Specify if you want to ignore categoryless dowloads |
| `verify_ssl` | bool | true | Verify SSL for *arr API calls |
| `use_duplicate_key` | bool | true | Use the duplicate key for matching |
| `wants_raw_data` | bool | false | Pass all data we have to the notifier |
| `radarr_instances` | array | [] | List of Radarr configurations |
| `sonarr_instances` | array | [] | List of Sonarr configurations |
| `notifier` | object | - | Notifier configuration (see below) |

## Notifier Support

The script supports a **flexible plugin-based notifier system**. Any notification service can be integrated by creating a simple Python class.

### Built-in Notifiers

#### Gotify

```json
{
  "notifier": {
    "enabled": true,
    "name": "Gotify",
    "config_file": "/config/scripts/loop_prevention/notifiers/gotify_notifier.py",
    "url": "http://gotify:80",
    "token": "your_gotify_token",
    "priority": 8
  }
}
```

### Community Notifiers

You can create notifiers for any service. See [Creating Custom Notifiers](#creating-custom-notifiers).

Popular services that can be integrated:

- Discord (via webhook)
- Telegram (via bot API)
- Pushover
- Slack
- Email (SMTP)
- Home Assistant
- Custom HTTP endpoints

## Creating Custom Notifiers

### Step 1: Create Your Notifier File

Create a new Python file in the `notifiers/` directory (example notifier can be found [in the example folder](/examples/example_custom_notifier.py)):

```python
#!/usr/bin/env python3
"""
Example Custom Notifier - Template for creating your own notifier
"""
from typing import Optional, Dict, Any
from loop_prevention_shared import NotifierInterface, Logger, LogLevel

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

    def _log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        """Log a message with the notifier name."""
        if self.logger:
            self.logger.log(f"{self.name}: {message}", level)

    def send_notification(self, title: str, message: str) -> bool:
        """
        Send a notification.

        Args:
            title: Notification title
            message: Notification message body

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            return False
        try:
            # Your notification logic here
            # Example: HTTP POST, API call, etc.
            self._log(f"Would send: {title}", LogLevel.INFO)
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
```

### Step 2: Configure Your Notifier

Reference your notifier in the configuration (full examples can be found [in the example folder](/examples/config_examples.txt)):

```json
{
  "notifier": {
    "enabled": true,
    "name": "YourNotifier",
    "config_file": "/config/scripts/loop_prevention/notifiers/your_notifier.py",

    // Add your custom configuration fields
    "your_field": "your_value",
    "another_field": 12345
  }
}
```

### Step 3: Test

The notifier will be automatically loaded and used when loops are detected.

### Example: Discord Notifier

```python
#!/usr/bin/env python3
"""Discord Notifier - Send notifications via Discord webhook"""

import json
from typing import Optional, Dict, Any
from urllib.request import Request, urlopen
from loop_prevention_shared import NotifierInterface, Logger, LogLevel


class DiscordNotifier(NotifierInterface):
    def __init__(self, config: Dict[str, Any], logger: Optional[Logger] = None) -> None:
        self.logger = logger
        self.enabled = config.get("enabled", False)
        self.name = config.get("name", "Discord")
        self.webhook_url = config.get("webhook_url", "")
        self.username = config.get("username", "SABnzbd Loop Prevention")

    def _log(self, message: str, level: LogLevel = LogLevel.ERROR) -> None:
        if self.logger:
            self.logger.log(f"{self.name}: {message}", level)

    def send_notification(self, title: str, message: str) -> bool:
        if not self.enabled or not self.webhook_url:
            return False

        try:
            payload = {
                "username": self.username,
                "embeds": [{
                    "title": title,
                    "description": message,
                    "color": 16711680  # Red
                }]
            }

            req = Request(self.webhook_url, method='POST')
            req.add_header('Content-Type', 'application/json')
            data = json.dumps(payload).encode('utf-8')
            response = urlopen(req, data=data, timeout=10)

            return response.status == 204
        except Exception as e:
            self._log(f"Error: {e}")
            return False
```

## Folder Structure

Recommended structure for Docker deployments:

```text
/config/scripts/loop_prevention/
├── prevent_download_loops_prequeue.py
├── prevent_download_loops_postprocess.py
├── loop_prevention_shared.py
├── notifiers/
│   ├── gotify_notifier.py
│   ├── discord_notifier.py
│   └── your_custom_notifier.py
├── prevent_download_loops.json
├── download_history.txt (auto-generated)
└── loop_prevention.log (auto-generated)
```

## Troubleshooting

### Script Not Running

1. **Check file permissions**: Scripts must be executable

   ```bash
   chmod +x /config/scripts/loop_prevention/*.py
   ```

2. **Check SABnzbd logs**: Look for script execution errors in SABnzbd's logs

3. **Check script logs**: Review `/config/scripts/loop_prevention/loop_prevention.log`

### Downloads Not Being Blocked

1. **Verify pre-queue script is configured** in SABnzbd Settings → Folders

2. **Check time window**: Downloads outside the time window won't be blocked

3. **Check duplicate key**: Some indexers don't provide duplicate keys - script falls back to name matching

4. **Review logs**: Check `loop_prevention.log` for what the script is detecting

### Post-Processing Not Updating Status

1. **Verify post-processing script is enabled** in SABnzbd

2. **Check matching logic**: The script uses 5 different matching methods - review logs to see which one matched

3. **Check for environment variable differences**: Pre-queue and post-process may receive slightly different names

### Notifier Not Working

1. **Check notifier is enabled**: `"enabled": true` in config

2. **Verify config_file path**: Must be absolute path to notifier .py file

3. **Check notifier logs**: Notifier errors are logged with the notifier name prefix

4. **Test notifier independently**: You can test notifiers by importing and calling directly

## Advanced Features

### Multiple Radarr/Sonarr Instances

Configure multiple instances with different categories:

```json
{
  "radarr_instances": [
    {
      "category": "movies-4k",
      "url": "http://radarr-4k:7878",
      "api_key": "key1"
    },
    {
      "category": "movies-1080p",
      "url": "http://radarr-1080p:7878",
      "api_key": "key2"
    }
  ]
}
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

### Areas for Contribution

- New notifier implementations
- Additional *arr application support (Lidarr, Readarr, etc.)
- Enhanced duplicate detection algorithms
- Unit tests
- Documentation improvements

### Sharing Your Notifier

If you create a notifier for a popular service, please share it:

1. Create a Pull Request with your notifier in `notifiers/`
2. Add documentation to this README
3. Include example configuration

## License

MPL-2.0 License - see [LICENSE](LICENSE.txt) file for details.

## Acknowledgments

- Built for the SABnzbd community
- Designed to work seamlessly with Radarr/Sonarr
- Inspired by the need for better download management in automated setups

## Support

- **Issues**: [GitHub Issues](https://github.com/slimDebug/sabnzbd_loop_prevention/issues)
- **Discussions**: [GitHub Discussions](https://github.com/slimDebug/sabnzbd_loop_prevention/discussions)

---

## Made with ❤️ for the self-hosted media community
