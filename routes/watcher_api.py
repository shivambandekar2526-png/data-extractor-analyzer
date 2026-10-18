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
                    recent = status.get("recent_events", [])
                    out_events = events if events else recent[-5:]
                    payload = {
                        "version": curr_version,
                        "timestamp": status.get("last_change_time"),
                        "events": out_events
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


@watcher_bp.route("/api/live-watcher/supabase-webhook", methods=["POST"])
def supabase_storage_webhook():
    """
    Receives push notifications from Supabase Storage / Database Webhook
    when a file is uploaded, modified, or deleted in the bucket.
    Zero polling, instant live UI update.
    """
    payload = request.get_json(silent=True) or {}
    logger.info(f"[Supabase Webhook] Received event: {payload}")

    record = payload.get("record") or {}
    old_record = payload.get("old_record") or {}
    event_type = payload.get("type") or payload.get("event") or ""

    file_path = record.get("name") or old_record.get("name") or payload.get("path") or ""
    if file_path:
        from services.supabase_service import get_supabase_service
        svc = get_supabase_service()
        svc.handle_storage_event(event_type, file_path)

    return jsonify({"success": True})


# =================================================================
# End of Routes
# =================================================================




