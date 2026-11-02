"""Download explicitly selected videos through the existing yt-dlp pipeline."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

from downloader import (
    ChannelRow,
    DiskBudget,
    DownloadHistory,
    LowDiskSpaceError,
    RunLog,
    download_video,
    ensure_disk_space,
    existing_filenames,
    get_state_dir,
    normalize_title,
    title_in_filenames,
    video_details,
)
from video_queue import SelectedVideoQueue


def run_selected_queue(
    selected_queue: SelectedVideoQueue,
    output_folder: Path,
    event: Callable[[dict], None],
    stop: Callable[[], bool],
    output_format: str = "mp4",
) -> None:
    if output_format not in {"original", "mp4"}:
        raise ValueError("Định dạng đầu ra không hợp lệ.")
    output_folder.mkdir(parents=True, exist_ok=True)
    state_dir = get_state_dir()
    history = DownloadHistory(state_dir)
    log = RunLog(state_dir)
    history.remember_names(existing_filenames(output_folder))
    history.import_log(output_folder / "download_log.csv")
    history.import_log(log.csv_path)
    known_names, _ = history.load()
    downloaded_ids = history.downloaded_ids()
    budget = DiskBudget(output_folder)
    ensure_disk_space(output_folder)

    while not stop():
        item = selected_queue.claim_next()
        if item is None:
            break
        video_id = item["video_id"]
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        row = ChannelRow(stt=video_id, url=video_url, sheet_row=0)
        try:
            if stop():
                selected_queue.release(video_id)
                break
            if video_id in downloaded_ids:
                selected_queue.finish(video_id, "skipped", "Video đã tải trước đó.")
                event({"type": "selected_status", "message": f"Đã bỏ qua video đã tải: {item['title']}"})
                continue
            details = video_details(video_id, output_format=output_format)
            if title_in_filenames(details["title"], known_names):
                selected_queue.finish(video_id, "skipped", "Đã có file cùng tiêu đề.")
                event({"type": "selected_status", "message": f"Đã có file: {details['title']}"})
                continue
            reservation = budget.reserve(details.get("estimated_bytes", 0))
            try:
                event({"type": "selected_status", "message": f"Đang tải: {details['title']}"})
                path = download_video(details, output_folder, video_id,
                                      lambda message: event({"type": "selected_status", "message": message}),
                                      output_format=output_format)
            finally:
                budget.release(reservation)
            history.record_download(details, path)
            downloaded_ids.add(video_id)
            known_names.add(normalize_title(details["title"]))
            known_names.add(normalize_title(path.stem))
            log.append(row, "Đã tải", details, str(path), details.get("quality", ""))
            selected_queue.finish(video_id, "done")
            event({"type": "selected_status", "message": f"Đã tải: {details['title']}"})
        except Exception as error:
            selected_queue.finish(video_id, "failed", str(error))
            log.append(row, "Lỗi", message=str(error))
            event({"type": "selected_status", "message": f"Lỗi video {video_id}: {error}"})
            if isinstance(error, (LowDiskSpaceError, sqlite3.Error)):
                raise
