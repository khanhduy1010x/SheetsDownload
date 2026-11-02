"""Exercise the real HTTP downloader against local, deterministic responses."""
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from yt_dlp import YoutubeDL
from yt_dlp.downloader import get_suitable_downloader, fragment, http
from yt_dlp.networking.exceptions import TransportError

from http_file_fix import SafeHttpFD, install_http_file_fix


class HttpFileFixTests(unittest.TestCase):
    def check_transfer(self, resume_completion):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.headers.get('Range'))
                if resume_completion and len(requests) == 2:
                    self.send_response(416)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                partial = resume_completion and len(requests) == 1
                self.send_response(206 if partial else 200)
                if partial:
                    self.send_header('Content-Range', 'bytes 0-7/16')
                self.send_header('Content-Length', '8')
                self.end_headers()
                self.wfile.write(b'12345678')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'Video [test]？.mp4'
                options = {'quiet': True, 'noprogress': True, 'http_chunk_size': 8 if resume_completion else 0}
                with YoutubeDL(options) as ydl, patch.object(http.random, 'randint', return_value=8):
                    fd = SafeHttpFD(ydl, options)
                    success, _ = fd.download(str(path), {
                        'url': f'http://127.0.0.1:{server.server_port}/video.mp4', 'ext': 'mp4'})
                self.assertTrue(success)
                self.assertEqual(path.read_bytes(), b'12345678')
                self.assertFalse(Path(str(path) + '.part').exists())
                # A finished download must be immediately movable/deletable.
                moved = path.with_suffix('.moved')
                path.rename(moved)
                moved.unlink()
                self.assertEqual(fd._owned_output_streams, [])
                self.assertEqual(len(requests), 3 if resume_completion else 1)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_http_416_completion_closes_stream_before_rename(self):
        self.check_transfer(True)

    def test_normal_download_is_unchanged(self):
        self.check_transfer(False)

    def test_failed_download_releases_owned_partial_stream(self):
        with tempfile.TemporaryDirectory() as folder:
            part = Path(folder) / 'partial.mp4.part'
            opened = []

            def fail(fd, filename, info):
                stream, _ = fd.sanitize_open(str(part), 'wb')
                opened.append(stream)
                stream.write(b'partial')
                raise TransportError('network retries exhausted')

            with YoutubeDL({'quiet': True}) as ydl:
                fd = SafeHttpFD(ydl, {})
                with patch.object(http.HttpFD, 'real_download', fail):
                    with self.assertRaises(TransportError):
                        fd.real_download(str(part), {})
            self.assertTrue(opened[0].closed)
            self.assertEqual(part.read_bytes(), b'partial')
            part.unlink()

    def test_direct_and_fragment_downloads_use_fixed_class(self):
        install_http_file_fix()
        self.assertIs(get_suitable_downloader({'url': 'https://example.com/video.mp4'}), SafeHttpFD)
        self.assertIs(fragment.HttpFD, SafeHttpFD)


if __name__ == '__main__':
    unittest.main()
