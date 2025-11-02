#!/usr/bin/env python3

"""
Standalone queue blocker task - runs as detached subprocess
Handles both SABnzbd pause and Radarr/Sonarr blocking
"""

import sys
import time
import json
import ssl
import traceback
from urllib.request import Request, urlopen

# Import shared components
from loop_prevention_shared import Logger, LogLevel


def setup_ssl_context(verify_ssl):
    """Setup SSL context."""
    try:
        if verify_ssl:
            return ssl.create_default_context()
        else:
            return ssl._create_unverified_context()
    except Exception:
        return ssl._create_unverified_context()


def find_sabnzbd_nzo_id(sabnzbd_api_url, sabnzbd_api_key, nzb_name, ssl_context, logger):
    """Find NZO ID in SABnzbd queue by name, with retries."""
    max_retries = 10
    retry_delay = 1

    for attempt in range(max_retries):
        try:
            queue_url = f"{sabnzbd_api_url}?mode=queue&output=json&apikey={sabnzbd_api_key}"

            if attempt > 0:
                logger.log(f"[BLOCKER] Retry {attempt}/{max_retries-1}: Looking for {nzb_name}", LogLevel.INFO)

            req = Request(queue_url)
            response = urlopen(req, timeout=10, context=ssl_context)
            data = json.loads(response.read().decode('utf-8'))

            queue_data = data.get('queue', {})

            # The actual slots/downloads are in queue['slots']
            slots = queue_data.get('slots', [])

            logger.log(f"[BLOCKER] Attempt {attempt+1}: Found {len(slots)} slots in queue", LogLevel.INFO)

            # Look for the download in slots
            for idx, slot in enumerate(slots):
                if not isinstance(slot, dict):
                    continue

                filename = slot.get('filename', '')
                nzo_id = slot.get('nzo_id', '')

                # Log first slot on first attempt
                if attempt == 0 and idx == 0:
                    logger.log(f"[BLOCKER] First slot: filename='{filename}', nzo_id={nzo_id}", LogLevel.DEBUG)

                # Match
                if filename and (filename == nzb_name or nzb_name in filename or filename in nzb_name):
                    logger.log(f"[BLOCKER] ✅ Found: nzo_id={nzo_id}, filename={filename}", LogLevel.INFO)
                    return nzo_id

            # Not found, retry
            if attempt < max_retries - 1:
                logger.log(f"[BLOCKER] Not found, retrying in {retry_delay}s...", LogLevel.DEBUG)
                time.sleep(retry_delay)

        except Exception as e:
            logger.log(f"[BLOCKER] Error: {e}", LogLevel.ERROR)
            logger.log(f"[BLOCKER] Traceback: {traceback.format_exc()}", LogLevel.ERROR)
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            continue

    logger.log(f"[BLOCKER] Item not found after {max_retries} attempts", LogLevel.WARNING)
    return None


def get_queue_items(url, api_key, ssl_context, logger):
    """Fetch queue items from *arr instance."""
    all_records = []
    page = 1
    page_size = 1000

    while page <= 50:
        try:
            req_url = f"{url}/api/v3/queue?page={page}&pageSize={page_size}"
            req = Request(req_url)
            req.add_header('X-Api-Key', api_key)
            req.add_header('Content-Type', 'application/json')

            response = urlopen(req, timeout=10, context=ssl_context)
            data = json.loads(response.read().decode('utf-8'))
            records = data.get('records', [])

            if not records:
                break

            all_records.extend(records)

            if len(records) < page_size:
                break

            page += 1
        except Exception as e:
            logger.log(f"[BLOCKER] Error fetching queue from *arr: {e}", LogLevel.ERROR)
            break

    return all_records


def find_queue_item(queue_items, nzb_name):
    """Find queue item by name."""
    # Exact match first
    for item in queue_items:
        if item.get('title') == nzb_name or item.get('downloadId') == nzb_name:
            return item.get('id')

    # Partial match fallback
    for item in queue_items:
        item_title = item.get('title', '')
        if nzb_name in item_title or item_title in nzb_name:
            return item.get('id')

    return None


def block_in_instance(instance, arr_type, nzb_name, ssl_context, logger):
    """Try to block in a specific *arr instance."""
    url = instance.get("url")
    api_key = instance.get("api_key")

    if not url or not api_key:
        logger.log(f"[BLOCKER] Skipping {arr_type} instance - missing url or api_key", LogLevel.WARNING)
        return False

    try:
        logger.log(f"[BLOCKER] Fetching queue from {arr_type}: {url}", LogLevel.INFO)
        queue_items = get_queue_items(url, api_key, ssl_context, logger)

        queue_id = find_queue_item(queue_items, nzb_name)

        if not queue_id:
            logger.log(f"[BLOCKER] Item not found in {arr_type} queue", LogLevel.INFO)
            return False

        logger.log(f"[BLOCKER] Found in {arr_type} queue! Blocking (queue ID: {queue_id})", LogLevel.INFO)

        # Block in *arr
        delete_url = f"{url}/api/v3/queue/{queue_id}?removeFromClient=true&blocklist=true&skipRedownload=true"
        req = Request(delete_url, method='DELETE')
        req.add_header('X-Api-Key', api_key)
        response = urlopen(req, timeout=10, context=ssl_context)
        response_body = response.read().decode('utf-8')

        logger.log(f"[BLOCKER] {arr_type} block response: Status={response.status}, Body={response_body}", LogLevel.INFO)
        return True

    except Exception as e:
        logger.log(f"[BLOCKER] Error with {arr_type}: {e}", LogLevel.ERROR)
        return False


def main():
    """Main execution."""
    logger = None

    try:
        if len(sys.argv) < 2:
            return

        task_data = json.loads(sys.argv[1])

        sabnzbd_api_url = task_data.get("sabnzbd_api_url")
        sabnzbd_api_key = task_data.get("sabnzbd_api_key")
        radarr_instances = task_data.get("radarr_instances", [])
        sonarr_instances = task_data.get("sonarr_instances", [])
        nzb_name = task_data.get("nzb_name")
        log_file = task_data.get("log_file")
        verify_ssl = task_data.get("verify_ssl", False)
        arr_block_delay = 10

        # Initialize logger
        logger = Logger(
            log_file=log_file,
            max_size_mb=10,
            max_backups=3,
            log_level=LogLevel.ALL
        )

        ssl_context = setup_ssl_context(verify_ssl)

        logger.log(f"[BLOCKER] Started blocker task for {nzb_name}", LogLevel.INFO)

        # 1. Find and pause in SABnzbd by name (with retries)
        logger.log(f"[BLOCKER] Looking for download in SABnzbd queue", LogLevel.INFO)
        nzo_id = find_sabnzbd_nzo_id(sabnzbd_api_url, sabnzbd_api_key, nzb_name, ssl_context, logger)

        if nzo_id:
            try:
                pause_url = f"{sabnzbd_api_url}?mode=queue&name=pause&value={nzo_id}&apikey={sabnzbd_api_key}"
                logger.log(f"[BLOCKER] Pausing in SABnzbd (NZO ID: {nzo_id})", LogLevel.INFO)

                req = Request(pause_url)
                response = urlopen(req, timeout=10, context=ssl_context)
                response_body = response.read().decode('utf-8')

                logger.log(f"[BLOCKER] SABnzbd pause response: Status={response.status}", LogLevel.INFO)
            except Exception as e:
                logger.log(f"[BLOCKER] Error pausing in SABnzbd: {e}", LogLevel.ERROR)
        else:
            logger.log(f"[BLOCKER] Could not find download in SABnzbd after retries", LogLevel.WARNING)

        # 2. Wait before blocking in *arr
        logger.log(f"[BLOCKER] Waiting {arr_block_delay} seconds before blocking in *arr", LogLevel.INFO)
        time.sleep(arr_block_delay)

        # 3. Try Radarr instances first
        logger.log(f"[BLOCKER] Trying {len(radarr_instances)} Radarr instances", LogLevel.INFO)
        for idx, instance in enumerate(radarr_instances):
            if block_in_instance(instance, "Radarr", nzb_name, ssl_context, logger):
                logger.log(f"[BLOCKER] Successfully blocked in Radarr", LogLevel.INFO)
                return

        # 4. If Radarr failed, try Sonarr instances
        logger.log(f"[BLOCKER] Radarr failed, trying {len(sonarr_instances)} Sonarr instances", LogLevel.INFO)
        for idx, instance in enumerate(sonarr_instances):
            if block_in_instance(instance, "Sonarr", nzb_name, ssl_context, logger):
                logger.log(f"[BLOCKER] Successfully blocked in Sonarr", LogLevel.INFO)
                return

        logger.log(f"[BLOCKER] Could not find item in any Radarr or Sonarr instance", LogLevel.WARNING)

    except Exception as e:
        if logger:
            logger.log(f"[BLOCKER] Unexpected error: {e}", LogLevel.ERROR)
            logger.log(f"[BLOCKER] Traceback: {traceback.format_exc()}", LogLevel.ERROR)
        else:
            sys.stderr.write(f"BLOCKER ERROR: {e}\n{traceback.format_exc()}\n")


if __name__ == "__main__":
    main()
