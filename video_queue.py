"""Persistent queue for videos selected in the Chrome extension."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from downloader import youtube_video_id


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class SelectedVideoQueue:
    def __init__(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = state_dir / "selected_videos.sqlite3"
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS selected_videos (
                video_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS selected_videos_status ON selected_videos(status, created_at)")
            db.execute("UPDATE selected_videos SET status = 'ready', updated_at = ? WHERE status = 'downloading'", (now(),))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add(self, videos: list[dict], action: str) -> dict:
        if action not in {"list", "download"}:
            raise ValueError("Thao tác không hợp lệ.")
        if not isinstance(videos, list) or not 1 <= len(videos) <= 200:
            raise ValueError("Hãy chọn từ 1 đến 200 video.")
        normalized = {}
        for item in videos:
            if not isinstance(item, dict):
                raise ValueError("Danh sách video không hợp lệ.")
            video_id = youtube_video_id(item.get("url", "")) if isinstance(item.get("url"), str) else None
            if not video_id:
                raise ValueError("Danh sách chứa link video YouTube không hợp lệ.")
            title = item.get("title", "")
            if not isinstance(title, str):
                raise ValueError("Tiêu đề video không hợp lệ.")
            normalized[video_id] = title.strip()[:500] or video_id
        added = 0
        timestamp = now()
        with self.connection() as db:
            for video_id, title in normalized.items():
                existing = db.execute("SELECT status FROM selected_videos WHERE video_id = ?", (video_id,)).fetchone()
                if existing is None:
                    db.execute("INSERT INTO selected_videos (video_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                               (video_id, title, "ready" if action == "download" else "saved", timestamp, timestamp))
                    added += 1
                elif existing["status"] not in {"downloading", "done"}:
                    status = "ready" if action == "download" or existing["status"] == "ready" else "saved"
                    db.execute("UPDATE selected_videos SET title = ?, status = ?, error = '', updated_at = ? WHERE video_id = ?",
                               (title, status, timestamp, video_id))
        return {"received": len(normalized), "added": added}

    def list(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT video_id, title, status, error, created_at FROM selected_videos ORDER BY created_at, video_id").fetchall()
        return [dict(row) for row in rows]

    def ready_count(self) -> int:
        with self.connection() as db:
            return db.execute("SELECT COUNT(*) FROM selected_videos WHERE status = 'ready'").fetchone()[0]

    def prepare(self, video_ids: list[str] | None = None) -> int:
        with self.connection() as db:
            if video_ids is None:
                result = db.execute("UPDATE selected_videos SET status = 'ready', error = '', updated_at = ? WHERE status IN ('saved', 'failed', 'skipped')", (now(),))
                return result.rowcount
            count = 0
            for video_id in video_ids:
                result = db.execute("UPDATE selected_videos SET status = 'ready', error = '', updated_at = ? WHERE video_id = ? AND status IN ('saved', 'failed', 'skipped')", (now(), video_id))
                count += result.rowcount
            return count

    def claim_next(self) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT video_id, title FROM selected_videos WHERE status = 'ready' ORDER BY created_at, video_id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE selected_videos SET status = 'downloading', updated_at = ? WHERE video_id = ?", (now(), row["video_id"]))
            return dict(row)

    def finish(self, video_id: str, status: str, error: str = "") -> None:
        if status not in {"done", "skipped", "failed"}:
            raise ValueError("Trạng thái video không hợp lệ.")
        with self.connection() as db:
            db.execute("UPDATE selected_videos SET status = ?, error = ?, updated_at = ? WHERE video_id = ?",
                       (status, error[:1000], now(), video_id))

    def release(self, video_id: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE selected_videos SET status = 'ready', updated_at = ? WHERE video_id = ? AND status = 'downloading'", (now(), video_id))

    def remove(self, video_ids: list[str]) -> int:
        with self.connection() as db:
            count = 0
            for video_id in video_ids:
                result = db.execute("DELETE FROM selected_videos WHERE video_id = ? AND status != 'downloading'", (video_id,))
                count += result.rowcount
            return count
