"""Loopback endpoint for the Chrome video picker."""
from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from video_queue import SelectedVideoQueue

BRIDGE_PORT = 39173
MAX_BODY_BYTES = 64 * 1024
CLIENT_HEADER = "X-SheetsDownload-Client"


def make_handler(video_queue: SelectedVideoQueue, on_download_requested: Callable[[], None] | None = None):
    class BridgeHandler(BaseHTTPRequestHandler):
        def log_message(self, format_string, *args):
            return

        def respond(self, status: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def local_request(self, *, send_videos: bool = False) -> bool:
            if self.headers.get("Host") != f"127.0.0.1:{BRIDGE_PORT}":
                self.respond(403, {"error": "Chỉ nhận kết nối trên máy này."})
                return False
            if send_videos and self.headers.get(CLIENT_HEADER) != "extension":
                self.respond(403, {"error": "Yêu cầu gửi video không hợp lệ."})
                return False
            return True

        def do_GET(self):
            if self.path != "/v1/health":
                self.respond(404, {"error": "Không tìm thấy."})
            elif self.local_request():
                self.respond(200, {"ok": True})

        def do_POST(self):
            if self.path != "/v1/videos":
                self.respond(404, {"error": "Không tìm thấy."})
                return
            if not self.local_request(send_videos=True):
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                self.respond(415, {"error": "Yêu cầu phải là JSON."})
                return
            try:
                size = int(self.headers.get("Content-Length", ""))
            except ValueError:
                size = 0
            if not 0 < size <= MAX_BODY_BYTES:
                self.respond(413, {"error": "Danh sách video quá lớn."})
                return
            try:
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("Yêu cầu không hợp lệ.")
                result = video_queue.add(payload.get("videos"), payload.get("action"))
                if payload["action"] == "download" and on_download_requested is not None:
                    on_download_requested()
            except (ValueError, TypeError) as error:
                self.respond(400, {"error": str(error)})
                return
            except sqlite3.Error:
                self.respond(500, {"error": "Không lưu được danh sách video. Hãy kiểm tra ứng dụng."})
                return
            self.respond(200, {"ok": True, **result})

    return BridgeHandler


class BrowserBridge:
    def __init__(self, video_queue: SelectedVideoQueue, on_download_requested: Callable[[], None] | None = None):
        self.server = ThreadingHTTPServer(("127.0.0.1", BRIDGE_PORT), make_handler(video_queue, on_download_requested))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="browser-bridge", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
