"""Queue and local bridge contract tests; no YouTube network calls."""
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from browser_bridge import BRIDGE_PORT, CLIENT_HEADER, make_handler
from video_queue import SelectedVideoQueue


VIDEO = {"url": "https://www.youtube.com/shorts/dQw4w9WgXcQ", "title": "Video thử"}


class QueueTests(unittest.TestCase):
    def test_queue_deduplicates_and_keeps_saved_videos_until_requested(self):
        with tempfile.TemporaryDirectory() as folder:
            queue = SelectedVideoQueue(Path(folder))
            result = queue.add([VIDEO, {**VIDEO, "title": "Tên mới"}], "list")
            self.assertEqual(result, {"received": 1, "added": 1})
            self.assertEqual(queue.list()[0]["status"], "saved")
            self.assertEqual(queue.ready_count(), 0)
            self.assertEqual(queue.prepare(), 1)
            self.assertEqual(queue.claim_next()["video_id"], "dQw4w9WgXcQ")
            queue.finish("dQw4w9WgXcQ", "done")
            self.assertEqual(queue.add([VIDEO], "download")["added"], 0)
            self.assertEqual(queue.list()[0]["status"], "done")

    def test_queue_rejects_untrusted_links(self):
        with tempfile.TemporaryDirectory() as folder:
            queue = SelectedVideoQueue(Path(folder))
            for url in ("file:///tmp/video", "https://youtube.com.evil/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/playlist?list=x"):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    queue.add([{"url": url, "title": "x"}], "download")
            self.assertEqual(queue.list(), [])

    def test_inflight_video_is_requeued_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            queue = SelectedVideoQueue(Path(folder))
            queue.add([VIDEO], "download")
            queue.claim_next()
            restarted = SelectedVideoQueue(Path(folder))
            self.assertEqual(restarted.ready_count(), 1)


class BridgeTests(unittest.TestCase):
    def test_bridge_connects_without_pairing_and_accepts_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            queue = SelectedVideoQueue(Path(folder))
            resumed = threading.Event()
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(queue, resumed.set))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = http.client.HTTPConnection("127.0.0.1", server.server_port)
                client.request("GET", "/v1/health", headers={"Host": f"127.0.0.1:{BRIDGE_PORT}"})
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                client.close()
                for client_header, expected in ((None, 403), ("extension", 200)):
                    with self.subTest(client_header=client_header):
                        headers = {"Host": f"127.0.0.1:{BRIDGE_PORT}", "Content-Type": "application/json"}
                        if client_header:
                            headers[CLIENT_HEADER] = client_header
                        client = http.client.HTTPConnection("127.0.0.1", server.server_port)
                        client.request("POST", "/v1/videos", json.dumps({"action": "list", "videos": [VIDEO]}),
                                       headers)
                        response = client.getresponse()
                        self.assertEqual(response.status, expected)
                        response.read()
                        client.close()
                self.assertEqual(queue.list()[0]["status"], "saved")
                client = http.client.HTTPConnection("127.0.0.1", server.server_port)
                client.request("POST", "/v1/videos", json.dumps({"action": "download", "videos": [VIDEO]}),
                               {"Host": f"127.0.0.1:{BRIDGE_PORT}", CLIENT_HEADER: "extension", "Content-Type": "application/json"})
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                client.close()
                self.assertTrue(resumed.is_set())
                self.assertEqual(queue.ready_count(), 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_bridge_rejects_wrong_host_and_browser_preflight(self):
        with tempfile.TemporaryDirectory() as folder:
            queue = SelectedVideoQueue(Path(folder))
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(queue))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = http.client.HTTPConnection("127.0.0.1", server.server_port)
                client.request("GET", "/v1/health", headers={"Host": "example.com"})
                response = client.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                client.close()
                client = http.client.HTTPConnection("127.0.0.1", server.server_port)
                client.request("OPTIONS", "/v1/videos", headers={
                    "Host": f"127.0.0.1:{BRIDGE_PORT}", "Origin": "https://example.com",
                    "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": CLIENT_HEADER.lower(),
                })
                response = client.getresponse()
                self.assertNotEqual(response.status, 200)
                response.read()
                client.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
