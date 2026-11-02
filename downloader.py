"""Google Sheet driven YouTube downloader with bounded concurrent channels."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
from functools import partial
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

import requests
import yt_dlp

from dns_recovery import DnsRecovery
from http_file_fix import install_http_file_fix
from mp4_output import NormalizeMp4PP

install_http_file_fix()

VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv", ".flv", ".mpeg", ".mpg", ".ts", ".m2ts", ".3gp"}
DOWNLOAD_FORMAT = "bestvideo[height<=720]+bestaudio/best[height<=720]"
MP4_VIDEO = "bestvideo[height<=720][ext=mp4]"
MP4_AUDIO = "bestaudio[ext=m4a]"
MP4_COMBINED = "best[height<=720][ext=mp4]"
MP4_DOWNLOAD_FORMAT = f'{MP4_VIDEO}+{MP4_AUDIO}/{MP4_COMBINED}/{MP4_VIDEO}+bestaudio/{DOWNLOAD_FORMAT}'
FORMAT_SORT = ["res", "fps", "vcodec", "br"]
GIB = 1024 ** 3
LOG_COLUMNS = ["time", "stt", "channel_url", "status", "video_id", "video_title", "views", "filename", "message"]


@dataclass(frozen=True)
class ChannelRow:
    stt: str
    url: str
    sheet_row: int


def normalize_title(title: str) -> str:
    """Match YouTube titles to Windows-safe existing filenames."""
    title = unicodedata.normalize("NFKD", title).casefold()
    title = "".join(char for char in title if not unicodedata.combining(char))
    return " ".join(re.findall(r"\w+", title, flags=re.UNICODE))


def title_in_filenames(title: str, filenames: set[str]) -> bool:
    """Match the whole normalized YouTube title anywhere in a filename."""
    normalized = normalize_title(title)
    return bool(normalized) and any(normalized in name for name in filenames)


def existing_filenames(folder: Path) -> set[str]:
    found: set[str] = set()
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        found.add(normalize_title(path.stem))
    return found


def get_state_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "YouTubeChannelTool"
    base_path = Path.home() / ("AppData/Roaming" if os.name == "nt" else ".local/share")
    return base_path / "YouTubeChannelTool"


def youtube_video_id(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        host = (parsed.hostname or "").lower()
        if host == "youtu.be":
            video_id = parsed.path.strip("/")
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if parsed.path == "/watch":
                video_id = (parse_qs(parsed.query).get("v") or [""])[0]
            else:
                match = re.fullmatch(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})/?", parsed.path)
                video_id = match.group(1) if match else ""
        else:
            return None
        return video_id if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) else None
    except ValueError:
        return None


class WatchHistoryHTMLParser(HTMLParser):
    """Read export links as data; never render HTML or execute its scripts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.videos: dict[str, str] = {}
        self.records = 0
        self.active_id: str | None = None
        self.title_parts: list[str] = []

    def finish_link(self) -> None:
        if self.active_id:
            self.records += 1
            title = " ".join("".join(self.title_parts).split())
            if self.active_id not in self.videos or not self.videos[self.active_id]:
                self.videos[self.active_id] = title
        self.active_id = None
        self.title_parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.finish_link()
            self.active_id = youtube_video_id(dict(attrs).get("href") or "")

    def handle_data(self, data):
        if self.active_id:
            self.title_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a":
            self.finish_link()


def read_watch_history(path: Path) -> tuple[dict[str, str], int]:
    if path.suffix.lower() in {".html", ".htm"}:
        parser = WatchHistoryHTMLParser()
        with path.open(encoding="utf-8-sig") as stream:
            while chunk := stream.read(65536):
                parser.feed(chunk)
        parser.close()
        parser.finish_link()
        videos, records = parser.videos, parser.records
    elif path.suffix.lower() == ".json":
        try:
            with path.open(encoding="utf-8-sig") as stream:
                entries = json.load(stream)
        except json.JSONDecodeError as error:
            raise ValueError("File JSON không hợp lệ. Hãy chọn file lịch sử xem từ Google Takeout.") from error
        if not isinstance(entries, list):
            raise ValueError("Cần file danh sách lịch sử xem YouTube từ Google Takeout.")
        videos, records = {}, 0
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("titleUrl"), str):
                continue
            video_id = youtube_video_id(entry["titleUrl"])
            if video_id:
                records += 1
                title = entry.get("title")
                videos.setdefault(video_id, title if isinstance(title, str) else "")
    else:
        raise ValueError("Hãy chọn file watch-history.html hoặc watch-history.json đã giải nén.")
    if not videos:
        raise ValueError("Không tìm thấy video YouTube trong file. Hãy chọn lịch sử xem (watch-history), không phải lịch sử tìm kiếm.")
    return videos, records


@dataclass(frozen=True)
class WatchHistoryImport:
    records: int
    unique_videos: int
    added_videos: int


class DownloadHistory:
    """Keep completed downloads and scanned names outside the video folder."""

    def __init__(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = state_dir / "history.sqlite3"
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS matches (value TEXT PRIMARY KEY)")
            db.execute("""CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                filename TEXT NOT NULL, completed_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS watched_videos (
                video_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                imported_at TEXT NOT NULL)""")
            db.execute('''CREATE TABLE IF NOT EXISTS failed_videos (
                video_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                message TEXT NOT NULL, failed_at TEXT NOT NULL)''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def remember_names(self, names: set[str]) -> None:
        with self.connection() as db:
            db.executemany("INSERT OR IGNORE INTO matches VALUES (?)", [(name,) for name in names if name])

    @staticmethod
    def store_record(db, video_id: str, title: str, filename: str, completed_at: str) -> None:
        names = {normalize_title(title)}
        if filename:
            names.add(normalize_title(Path(filename).stem))
        db.executemany("INSERT OR IGNORE INTO matches VALUES (?)", [(name,) for name in names if name])
        if video_id:
            db.execute("INSERT OR IGNORE INTO videos VALUES (?, ?, ?, ?)", (video_id, title, filename, completed_at))

    def record_download(self, video: dict, path: Path) -> None:
        with self.connection() as db:
            self.store_record(db, video.get("id", ""), video["title"], str(path), datetime.now().astimezone().isoformat(timespec="seconds"))

    def record_failure(self, video: dict, message: str) -> None:
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO failed_videos VALUES (?, ?, ?, ?)',
                       (video['id'], video.get('title', ''), message,
                        datetime.now().astimezone().isoformat(timespec='seconds')))

    def load_failed_ids(self) -> set[str]:
        with self.connection() as db:
            return {row[0] for row in db.execute('SELECT video_id FROM failed_videos')}

    def import_log(self, csv_path: Path) -> None:
        """Import successful legacy records even when the video was deleted."""
        if not csv_path.is_file():
            return
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            records = [row for row in csv.DictReader(stream) if row.get("status") == "Đã tải"]
        with self.connection() as db:
            for record in records:
                self.store_record(db, record.get("video_id", ""), record.get("video_title", ""), record.get("filename", ""), record.get("time", ""))

    def import_watch_history(self, path: Path) -> WatchHistoryImport:
        videos, records = read_watch_history(path)
        imported_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.connection() as db:
            before = db.total_changes
            db.executemany("INSERT OR IGNORE INTO watched_videos VALUES (?, ?, ?)", [(video_id, title, imported_at) for video_id, title in videos.items()])
            added = db.total_changes - before
        return WatchHistoryImport(records, len(videos), added)

    def load(self) -> tuple[set[str], set[str]]:
        with self.connection() as db:
            names = {row[0] for row in db.execute("SELECT value FROM matches")}
            video_ids = {row[0] for row in db.execute("SELECT video_id FROM videos UNION SELECT video_id FROM watched_videos")}
        return names, video_ids

    def downloaded_ids(self) -> set[str]:
        """Return completed IDs without applying the channel-only watch filter."""
        with self.connection() as db:
            return {row[0] for row in db.execute("SELECT video_id FROM videos")}


def sheet_csv_url(sheet_url: str) -> str:
    parsed = urlparse(sheet_url.strip())
    if parsed.netloc not in {"docs.google.com", "www.docs.google.com"}:
        raise ValueError("Cần link Google Sheets dạng https://docs.google.com/spreadsheets/d/...")
    match = re.match(r"^/spreadsheets/d/([\w-]+)", parsed.path)
    if not match:
        raise ValueError("Không tìm thấy ID Google Sheet trong đường link.")
    fragment = parse_qs(parsed.fragment)
    query = parse_qs(parsed.query)
    gid = (fragment.get("gid") or query.get("gid") or ["0"])[0]
    if not gid.isdigit():
        raise ValueError("GID của tab Sheet không hợp lệ.")
    return f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?{urlencode({'format': 'csv', 'gid': gid})}"


def load_sheet_rows(sheet_url: str) -> list[ChannelRow]:
    response = requests.get(sheet_csv_url(sheet_url), timeout=45)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "html" in content_type or response.text.lstrip().lower().startswith("<!doctype html"):
        raise ValueError("Sheet chưa cho phép xem bằng link hoặc cần đăng nhập Google.")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    channels = []
    for row_number, cells in enumerate(rows, start=1):
        stt = cells[0].strip() if cells else ""
        url = cells[4].strip() if len(cells) > 4 else ""
        if not stt.isdigit() or not url:
            continue
        if not re.match(r"^https?://(www\.)?(youtube\.com|youtu\.be)/", url, re.I):
            continue
        channels.append(ChannelRow(stt=stt, url=url, sheet_row=row_number))
    if not channels:
        raise ValueError("Không tìm thấy dòng có STT ở cột A và link YouTube ở cột E.")
    ids = [row.stt for row in channels]
    if len(ids) != len(set(ids)):
        raise ValueError("Cột STT có giá trị trùng; cần STT duy nhất cho mỗi kênh.")
    return channels


class RunLog:
    def __init__(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = state_dir / "download_log.csv"
        self.lock = threading.Lock()

    def append(self, row: ChannelRow, status: str, video: dict | None = None, filename: str = "", message: str = "") -> None:
        video = video or {}
        with self.lock:
            exists = self.csv_path.exists()
            with self.csv_path.open("a", encoding="utf-8" if exists else "utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=LOG_COLUMNS)
                if not exists:
                    writer.writeheader()
                writer.writerow({
                    "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "stt": row.stt,
                    "channel_url": row.url,
                    "status": status,
                    "video_id": video.get("id", ""),
                    "video_title": video.get("title", ""),
                    "views": video.get("view_count", ""),
                    "filename": filename,
                    "message": message,
                })


def channel_videos(channel_url: str) -> list[dict]:
    """Return YouTube's Popular videos playlist in its own order."""
    clean = channel_url.split("?", 1)[0].rstrip("/")
    for suffix in ("/videos", "/shorts", "/streams", "/featured"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
            break
    channel_match = re.search(r"/channel/(UC[\w-]{22})$", clean)
    channel_id = channel_match.group(1) if channel_match else None
    with yt_dlp.YoutubeDL({"js_runtimes": {"node": {}}, "extract_flat": True, "skip_download": True, "quiet": True, "no_warnings": True}) as downloader:
        if channel_id is None:
            channel = downloader.extract_info(clean + "/videos", download=False, process=False)
            channel_id = channel.get("channel_id") or channel.get("id")
        if not channel_id or not re.fullmatch(r"UC[\w-]{22}", channel_id):
            raise ValueError("Không xác định được mã kênh YouTube để mở mục Popular.")
        popular_playlist_id = "UULP" + channel_id[2:]
        popular = downloader.extract_info(f"https://www.youtube.com/playlist?list={popular_playlist_id}", download=False)
    if popular.get("id") != popular_playlist_id:
        raise ValueError("Không đọc được danh sách Popular của kênh.")
    return [entry for entry in popular.get("entries", []) if entry and entry.get("id") and entry.get("title")]


def video_details(video_id: str, output_format: str = 'original') -> dict:
    """Read the original video title; channel lists can contain translated titles."""
    with yt_dlp.YoutubeDL({"js_runtimes": {"node": {}}, "format": MP4_DOWNLOAD_FORMAT if output_format == 'mp4' else DOWNLOAD_FORMAT, "format_sort": FORMAT_SORT, "format_sort_force": True, "skip_download": True, "quiet": True, "no_warnings": True}) as downloader:
        result = downloader.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    formats = result.get("requested_formats", [result])
    estimated_bytes = sum(item.get("filesize") or item.get("filesize_approx") or 0 for item in formats)
    video_format = next((item for item in formats if item.get("vcodec") and item["vcodec"] != "none"), result)
    height = video_format.get("height") or result.get("height")
    fps = video_format.get("fps") or result.get("fps")
    quality = f"{height}p" if height else "Không rõ độ phân giải"
    if fps:
        quality += f" {round(fps)}fps"
    return {"id": result["id"], "title": result["title"], "view_count": result.get("view_count"), "duration": result.get('duration'), "estimated_bytes": estimated_bytes, "quality": quality, "height": height}


class LowDiskSpaceError(RuntimeError):
    pass


class RecoveryAbortedError(RuntimeError):
    pass


def ensure_disk_space(folder: Path, estimated_bytes: int = 0) -> None:
    free_bytes = shutil.disk_usage(folder).free
    required_bytes = max(2 * GIB, int(estimated_bytes * 1.5) + GIB)
    if free_bytes < required_bytes:
        raise LowDiskSpaceError(f"Thiếu dung lượng trống: còn {free_bytes / GIB:.1f} GB, cần ít nhất {required_bytes / GIB:.1f} GB trước khi tải.")


class DiskBudget:
    """Reserve estimated space so parallel downloads cannot all pass independently."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.lock = threading.Lock()
        self.reserved_bytes = 0

    def reserve(self, estimated_bytes: int) -> int:
        reservation = max(estimated_bytes, GIB)
        with self.lock:
            free_bytes = shutil.disk_usage(self.folder).free - self.reserved_bytes
            required_bytes = max(2 * GIB, int(estimated_bytes * 1.5) + GIB)
            if free_bytes < required_bytes:
                raise LowDiskSpaceError(f"Thiếu dung lượng cho các lượt tải đồng thời: còn {free_bytes / GIB:.1f} GB khả dụng, cần {required_bytes / GIB:.1f} GB.")
            self.reserved_bytes += reservation
        return reservation

    def release(self, reservation: int) -> None:
        with self.lock:
            self.reserved_bytes -= reservation


def download_video(video: dict, target_folder: Path, stt: str, progress: Callable[[str], None], output_format: str = 'original') -> Path:
    if output_format not in {'original', 'mp4'}:
        raise ValueError('Định dạng đầu ra không hợp lệ.')
    target_folder.mkdir(parents=True, exist_ok=True)
    video_url = f"https://www.youtube.com/watch?v={video['id']}"
    output = str(target_folder / f"{stt} %(title).180B.%(ext)s")
    completed_paths: list[str] = []
    last_progress = 0.0

    def progress_hook(data: dict) -> None:
        nonlocal last_progress
        if data.get("status") == "downloading":
            now = time.monotonic()
            if now - last_progress < 0.25:
                return
            percent = data.get("_percent_str", "").strip()
            if percent:
                last_progress = now
                progress(f"{stt}: {percent}")

    with yt_dlp.YoutubeDL({
        "js_runtimes": {"node": {}},
        "format": MP4_DOWNLOAD_FORMAT if output_format == 'mp4' else DOWNLOAD_FORMAT,
        **({'merge_output_format': 'mp4'} if output_format == 'mp4' else {}),
        "format_sort": FORMAT_SORT,
        "format_sort_force": True,
        "outtmpl": output,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "noplaylist": True,
        "windowsfilenames": True,
        "progress_hooks": [progress_hook],
        "post_hooks": [completed_paths.append],
    }) as downloader:
        if output_format == 'mp4':
            downloader.add_post_processor(NormalizeMp4PP(downloader, lambda message: progress(f'{stt}: {message}'), preserve_native_mp4=True))
        result = downloader.extract_info(video_url, download=True)
        filename = Path(downloader.prepare_filename(result))
    # post_hooks receives the final path after merging and moving the video.
    # prepare_filename can still describe the name before post-processing.
    candidates = [Path(path) for path in reversed(completed_paths)]
    if result.get("filepath"):
        candidates.append(Path(result["filepath"]))
    candidates.append(filename)
    allowed_suffixes = {'.mp4'} if output_format == 'mp4' else VIDEO_SUFFIXES
    for path in candidates:
        if path.suffix.lower() in allowed_suffixes and path.is_file():
            return path.resolve()
    # Compare names literally: titles containing [] are patterns when passed
    # to glob(), so a successfully downloaded file could previously be missed.
    for path in target_folder.iterdir():
        if path.stem == filename.stem and path.suffix.lower() in allowed_suffixes and path.is_file():
            return path.resolve()
    raise RuntimeError(f"Không tìm thấy file sau tải: {filename.name}")


def run(
    sheet_url: str,
    output_folder: Path,
    event: Callable[[dict], None],
    stop: Callable[[], bool] = lambda: False,
    max_workers: int = 1,
    output_format: str = 'original',
    min_views: int = 0,
    min_duration: int = 0,
) -> None:
    if not isinstance(min_views, int) or not isinstance(min_duration, int) or min_views < 0 or min_duration < 0:
        raise ValueError('Lượt xem và thời lượng tối thiểu phải là số nguyên không âm.')
    if output_format not in {'original', 'mp4'}:
        raise ValueError('Định dạng đầu ra không hợp lệ.')
    if not 1 <= max_workers <= 8:
        raise ValueError("Số luồng tải phải từ 1 đến 8.")
    output_folder.mkdir(parents=True, exist_ok=True)
    state_dir = get_state_dir()
    history = DownloadHistory(state_dir)
    log = RunLog(state_dir)
    history.remember_names(existing_filenames(output_folder))
    history.import_log(output_folder / "download_log.csv")
    history.import_log(log.csv_path)
    existing, known_video_ids = history.load()
    failed_video_ids = history.load_failed_ids()
    ensure_disk_space(output_folder)
    rows = load_sheet_rows(sheet_url)
    title_condition = threading.Condition()
    active_titles: set[str] = set()
    budget = DiskBudget(output_folder)
    halt = threading.Event()
    halt_reasons: list[str] = []
    dns_recovery = DnsRecovery()
    event({"type": "start", "total": len(rows), "workers": max_workers})

    def with_retry(row: ChannelRow, operation, *args):
        try:
            return operation(*args)
        except Exception as error:
            if stop():
                raise
            if isinstance(error, (LowDiskSpaceError, sqlite3.Error)):
                raise
            # Accept both the ASCII and typographic apostrophe YouTube uses.
            detail = str(error).replace("’", "'").casefold()
            is_bot = isinstance(error, yt_dlp.utils.DownloadError) and "sign in to confirm you're not a bot" in detail
            if (not is_bot and (operation.func if isinstance(operation, partial) else operation) is video_details
                    and isinstance(error, yt_dlp.utils.DownloadError)
                    and any(text in detail for text in ("not available", "video unavailable", "private video"))):
                # Keep looking for another available video in this channel.
                raise
            if is_bot:
                log.append(row, "Phát hiện lỗi bot", message=str(error))
                event({"type": "progress", "stt": row.stt, "message": "Gặp lỗi bot: đang yêu cầu quyền admin để xóa DNS. Hãy xác nhận UAC nếu xuất hiện."})
                success, message = dns_recovery.repair()
                log.append(row, "Xóa DNS" if success else "Lỗi xóa DNS", message=message)
                event({"type": "progress", "stt": row.stt, "message": message})
                if not success:
                    raise RecoveryAbortedError(f"{error}\nXử lý DNS: {message}") from error
            message = f"Gặp lỗi: {error}. Đang thử lại bước vừa lỗi (1/1)."
            log.append(row, "Thử lại 1/1", message=message)
            event({"type": "progress", "stt": row.stt, "message": message})
            # Outside the try block: the second failure must propagate.
            return operation(*args)

    def process_row(position: int, row: ChannelRow) -> None:
        base = {"position": position, "total": len(rows), "stt": row.stt, "channel": row.url}
        claimed_title: str | None = None
        try:
            videos = with_retry(row, channel_videos, row.url)
            failed_count = 0
            unavailable_count = 0

            def remember_failure(video, error):
                history.record_failure(video, str(error))
                with title_condition:
                    failed_video_ids.add(video['id'])
                log.append(row, 'Lỗi video', video, message=str(error))
                event({'type': 'progress', 'stt': row.stt,
                       'message': f"Video lỗi, đã ghi nhớ và chuyển video kế tiếp: {video.get('title', video['id'])}"})

            for video in videos:
                if stop() or halt.is_set():
                    event({'type': 'row', **base, 'status': 'Đã dừng', 'title': '', 'completed': True})
                    return
                with title_condition:
                    if video['id'] in known_video_ids or video['id'] in failed_video_ids:
                        continue
                try:
                    details = with_retry(row, partial(video_details, output_format=output_format) if output_format == 'mp4' else video_details, video['id'])
                except (LowDiskSpaceError, sqlite3.Error, RecoveryAbortedError):
                    raise
                except Exception as error:
                    if stop():
                        event({'type': 'row', **base, 'status': 'Đã dừng', 'title': '', 'completed': True})
                        return
                    remember_failure(video, error)
                    if any(text in str(error).lower() for text in ('not available', 'video unavailable', 'private video')):
                        unavailable_count += 1
                    else:
                        failed_count += 1
                    continue
                if (min_views and (details.get('view_count') is None or details['view_count'] < min_views)
                        or min_duration and (details.get('duration') is None or details['duration'] < min_duration)):
                    log.append(row, 'Không đủ điều kiện', details)
                    continue
                title = normalize_title(details['title'])
                with title_condition:
                    while title and any(title in active for active in active_titles) and not stop() and not halt.is_set():
                        title_condition.wait(timeout=0.2)
                    if stop() or halt.is_set():
                        event({'type': 'row', **base, 'status': 'Đã dừng', 'title': '', 'completed': True})
                        return
                    if (video['id'] in known_video_ids or video['id'] in failed_video_ids
                            or title_in_filenames(details['title'], existing)):
                        continue
                    active_titles.add(title)
                    claimed_title = title
                try:
                    reservation = budget.reserve(details.get('estimated_bytes', 0))
                    try:
                        event({'type': 'row', **base, 'status': 'Đang tải', 'title': details['title'], 'completed': False})
                        try:
                            path = with_retry(
                                row, partial(download_video, output_format=output_format) if output_format == 'mp4' else download_video,
                                details, output_folder, row.stt,
                                lambda note: event({'type': 'progress', 'stt': row.stt, 'message': note}))
                        except (LowDiskSpaceError, sqlite3.Error, RecoveryAbortedError):
                            raise
                        except Exception as error:
                            if stop():
                                event({'type': 'row', **base, 'status': 'Đã dừng', 'title': '', 'completed': True})
                                return
                            remember_failure(details, error)
                            failed_count += 1
                            continue
                    finally:
                        budget.release(reservation)
                    history.record_download(details, path)
                    with title_condition:
                        existing.add(normalize_title(details['title']))
                        existing.add(normalize_title(path.stem))
                        known_video_ids.add(details['id'])
                    log.append(row, 'Đã tải', details, str(path), details.get('quality', ''))
                    event({'type': 'row', **base, 'status': 'Đã tải', 'title': details['title'], 'file': str(path), 'quality': details.get('quality', ''), 'completed': True})
                    return
                finally:
                    with title_condition:
                        active_titles.discard(title)
                        claimed_title = None
                        title_condition.notify_all()
            status = 'Lỗi' if failed_count else 'Không còn video mới'
            message = f'Không còn video phù hợp; {failed_count} video lỗi, {unavailable_count} video không khả dụng.'
            log.append(row, status, message=message)
            event({'type': 'row', **base, 'status': status, 'title': message, 'completed': True})
        except Exception as error:
            message = str(error)
            if isinstance(error, sqlite3.Error):
                message = f"Không lưu được lịch sử tải: {error}. Hãy giữ lại file video và kiểm tra thư mục lịch sử."
            log.append(row, "Lỗi", message=message)
            event({"type": "row", **base, "status": "Lỗi", "title": message, "completed": True})
            if isinstance(error, (LowDiskSpaceError, sqlite3.Error)):
                halt_reasons.append(message)
                halt.set()
        finally:
            if claimed_title is not None:
                with title_condition:
                    active_titles.discard(claimed_title)
                    title_condition.notify_all()

    next_position = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        pending = set()

        def schedule_next() -> None:
            nonlocal next_position
            while len(pending) < max_workers and next_position < len(rows) and not stop() and not halt.is_set():
                row = rows[next_position]
                event({"type": "row", "position": next_position + 1, "total": len(rows), "stt": row.stt, "channel": row.url, "status": "Đang kiểm tra", "title": "", "completed": False})
                pending.add(pool.submit(process_row, next_position + 1, row))
                next_position += 1

        schedule_next()
        while pending:
            finished, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for future in finished:
                future.result()
                completed += 1
            schedule_next()

    if halt.is_set():
        event({"type": "fatal", "message": halt_reasons[0]})
    elif stop():
        event({"type": "stopped", "done": completed})
    else:
        event({"type": "finished", "total": len(rows)})
