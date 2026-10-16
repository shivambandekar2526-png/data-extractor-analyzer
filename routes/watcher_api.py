"""
Live Folder Watcher Routes
==========================
Server-Sent Events (SSE) streaming and real-time directory polling endpoints.
"""

import os
import json
import time
import logging
from flask import Blueprint, request, jsonify, Response

logger = logging.getLogger("app")

from services.state import folder_watcher

watcher_bp = Blueprint("watcher_api", __name__)

@watcher_bp.route("/api/live-watcher/status", methods=["GET"])
def get_live_watcher_status():
    """Returns current live filesystem watch status, version, and recent change events."""
    folder_watcher.check_for_changes()
    return jsonify({
        "success": True,
        **folder_watcher.get_status()
    })


@watcher_bp.route("/api/live-watcher/check-now", methods=["POST"])
def trigger_live_watcher_check():
    """Forces an immediate filesystem scan and returns any detected events."""
    events = folder_watcher.check_for_changes()
    return jsonify({
        "success": True,
        "events_count": len(events),
        "events": events,
        "version": folder_watcher.get_status().get("version", 0)
    })


@watcher_bp.route("/api/live-watcher/stream", methods=["GET"])
def stream_live_watcher_events():
    """
    Server-Sent Events (SSE) stream broadcasting real-time filesystem updates
    (e.g. newly dropped or modified Excel sheets, PDFs, or output directories).
    """
    def event_stream():
        last_version = folder_watcher.get_status().get("version", 0)
        yield f"event: connected\ndata: {json.dumps({'status': 'listening', 'version': last_version})}\n\n"

        while True:
            has_event = folder_watcher.wait_for_event(timeout=15.0)
            if has_event:
                events = folder_watcher.check_for_changes()
                status = folder_watcher.get_status()
                curr_version = status.get("version", 0)
                if curr_version != last_version or events:
                    last_version = curr_version
                    payload = {
                        "version": curr_version,
                        "timestamp": status.get("last_change_time"),
                        "events": events
                    }
                    yield f"event: folder_update\ndata: {json.dumps(payload)}\n\n"
            else:
                yield ": ping\n\n"

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )


# =================================================================
# End of Routes
# =================================================================




