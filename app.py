"""Manual desktop interface for the channel downloader."""
from __future__ import annotations

import json
import logging
import os
import queue
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import sys
import threading
import time
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from downloader import DownloadHistory, get_state_dir, run

APP_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
if getattr(sys, "frozen", False):
    os.environ["PATH"] = str(RESOURCE_DIR) + os.pathsep + os.environ.get("PATH", "")
SETTINGS_FILE = get_state_dir() / "settings.json"
FORMAT_OPTIONS = {'MP4': 'mp4', 'Tự động': 'original'}


def parse_views(value):
    value = value.strip() or '0'
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)', value):
        raise ValueError('Lượt xem không hợp lệ')
    return int(value.replace('.', ''))


def format_views(value):
    return f'{int(value):,}'.replace(',', '.')


def minutes_to_seconds(value):
    try:
        minutes = Decimal(value.strip().replace(',', '.') or '0')
        if not minutes.is_finite() or minutes < 0:
            raise ValueError('Thời lượng không hợp lệ')
        return int((minutes * 60).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except InvalidOperation as error:
        raise ValueError('Thời lượng không hợp lệ') from error


def seconds_to_minutes(value):
    return format(Decimal(str(value)) / 60, '.6f').rstrip('0').rstrip('.') or '0'


class DownloaderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Tải video YouTube theo Google Sheet")
        self.minsize(850, 560)
        self.geometry("1050x650")
        self.events: queue.Queue[dict] = queue.Queue()
        self.stop_requested = threading.Event()
        self.worker: threading.Thread | None = None
        self.closing = False
        self.protocol("WM_DELETE_WINDOW", self.request_close)
        settings = self.read_settings()
        icon_path = RESOURCE_DIR / "assets" / "icon.png"
        if icon_path.is_file():
            self.app_icon = tk.PhotoImage(file=str(icon_path))
            self.iconphoto(True, self.app_icon)
        windows_icon = RESOURCE_DIR / "assets" / "icon.ico"
        if windows_icon.is_file():
            try:
                self.iconbitmap(str(windows_icon))
            except tk.TclError:
                pass
        self.sheet_url = tk.StringVar(value=settings.get("sheet_url", ""))
        self.folder = tk.StringVar(value=settings.get("folder", ""))
        self.max_workers = tk.StringVar(value=str(settings.get("max_workers", 1)))
        self.min_views = tk.StringVar(value=format_views(settings.get('min_views', 0)))
        self.min_views.trace_add('write', self.format_min_views)
        self.min_duration = tk.StringVar(value=seconds_to_minutes(settings.get('min_duration', 0)))
        saved_format = settings.get('output_format', 'mp4')
        self.output_format = tk.StringVar(value=next((label for label, value in FORMAT_OPTIONS.items() if value == saved_format), next(iter(FORMAT_OPTIONS))))
        self.completed_count = 0
        self.summary = tk.StringVar(value="Sẵn sàng")
        self.progress = tk.StringVar(value="")
        self.build_ui()
        self.after(100, self.poll_events)

    def report_callback_exception(self, exception, value, traceback):
        logging.getLogger(__name__).error("Lỗi giao diện", exc_info=(exception, value, traceback))
        self.summary.set("Gặp lỗi giao diện; xem application.log trong thư mục lịch sử.")

    def format_min_views(self, *_):
        text = self.min_views.get()
        if text and re.fullmatch(r'[0-9.]+', text) and text.replace('.', ''):
            formatted = format_views(int(text.replace('.', '')))
            if formatted != text:
                self.min_views.set(formatted)

    def request_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.stop_requested.set()
        self.start_button.configure(state="disabled")
        self.import_history_button.configure(state="disabled")
        self.worker_selector.configure(state="disabled")
        self.format_selector.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.summary.set("Đang đóng: chờ các lượt đang tải hoàn tất...")
        self.finish_close()

    def finish_close(self) -> None:
        # Keep Tcl's event loop alive until workers release their callbacks.
        if self.worker is not None and self.worker.is_alive():
            self.after(100, self.finish_close)
            return
        logging.getLogger(__name__).info("Ứng dụng đã đóng sau khi các luồng kết thúc.")
        self.destroy()

    @staticmethod
    def read_settings() -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def build_ui(self) -> None:
        container = ttk.Frame(self, padding=18)
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(6, weight=1)

        ttk.Label(container, text="Link Google Sheet").grid(row=0, column=0, sticky="w", pady=7)
        ttk.Entry(container, textvariable=self.sheet_url).grid(row=0, column=1, columnspan=2, sticky="ew", padx=(14, 0), pady=7)

        ttk.Label(container, text="Thư mục video").grid(row=1, column=0, sticky="w", pady=7)
        ttk.Entry(container, textvariable=self.folder).grid(row=1, column=1, sticky="ew", padx=(14, 8), pady=7)
        ttk.Button(container, text="Chọn thư mục", command=self.choose_folder).grid(row=1, column=2, sticky="e", pady=7)

        ttk.Label(container, text='Định dạng đầu ra').grid(row=2, column=0, sticky='w', pady=7)
        format_controls = ttk.Frame(container)
        format_controls.grid(row=2, column=1, columnspan=2, sticky='ew', padx=(14, 0), pady=7)
        self.format_selector = ttk.Combobox(format_controls, textvariable=self.output_format, values=list(FORMAT_OPTIONS), state='readonly', width=12)
        self.format_selector.pack(side='left')

        filters = ttk.Frame(container)
        filters.grid(row=3, column=0, columnspan=3, sticky='ew', pady=7)
        ttk.Label(filters, text='Lượt xem tối thiểu').pack(side='left')
        ttk.Entry(filters, textvariable=self.min_views, width=14).pack(side='left', padx=(10, 24))
        ttk.Label(filters, text='Thời lượng tối thiểu (phút)').pack(side='left')
        ttk.Entry(filters, textvariable=self.min_duration, width=12).pack(side='left', padx=10)

        controls = ttk.Frame(container)
        controls.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(14, 6))
        ttk.Label(controls, text="Tải đồng thời").pack(side="left", padx=(0, 7))
        self.worker_selector = ttk.Combobox(controls, textvariable=self.max_workers, values=[str(count) for count in range(1, 9)], state="readonly", width=3)
        self.worker_selector.pack(side="left", padx=(0, 16))
        self.start_button = ttk.Button(controls, text="Bắt đầu tải", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="Dừng sau các lượt đang tải", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(controls, textvariable=self.summary).pack(side="left", padx=16)

        ttk.Label(container, textvariable=self.progress).grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 8))

        columns = ("position", "stt", "status", "title", "channel")
        self.table = ttk.Treeview(container, columns=columns, show="headings", selectmode="browse")
        for column, text, width in [
            ("position", "Dòng", 65),
            ("stt", "STT", 65),
            ("status", "Trạng thái", 155),
            ("title", "Tiêu đề video / lỗi", 435),
            ("channel", "Link kênh", 330),
        ]:
            self.table.heading(column, text=text)
            self.table.column(column, width=width, minwidth=60, stretch=column in {"title", "channel"})
        self.table.grid(row=6, column=0, columnspan=3, sticky="nsew")
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=6, column=3, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)
        history_controls = ttk.Frame(container)
        history_controls.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Button(history_controls, text="Mở thư mục lịch sử", command=self.open_history_folder).pack(side="right")
        self.import_history_button = ttk.Button(history_controls, text="Nhập lịch sử YouTube", command=self.import_watch_history)
        self.import_history_button.pack(side="right", padx=(8, 8))

    def import_watch_history(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Chọn lịch sử xem YouTube đã xuất từ Google Takeout",
            filetypes=[("Lịch sử YouTube", ("*.html", "*.htm", "*.json"))],
        )
        if not chosen:
            return
        self.start_button.configure(state="disabled")
        self.import_history_button.configure(state="disabled")
        self.worker_selector.configure(state="disabled")
        self.summary.set("Đang nhập lịch sử xem...")
        self.format_selector.configure(state='disabled')
        self.progress.set("")
        self.worker = threading.Thread(target=self.run_import_worker, args=(Path(chosen),), daemon=True)
        self.worker.start()

    def run_import_worker(self, path: Path) -> None:
        try:
            result = DownloadHistory(get_state_dir()).import_watch_history(path)
            self.events.put({"type": "history_imported", "records": result.records, "unique": result.unique_videos, "added": result.added_videos})
        except Exception as error:
            self.events.put({"type": "import_error", "message": str(error)})
        finally:
            self.events.put({"type": "idle"})

    def open_history_folder(self) -> None:
        folder = get_state_dir()
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)
        else:
            messagebox.showinfo("Thư mục lịch sử", str(folder))

    def choose_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Chọn thư mục chứa video cũ và lưu video mới")
        if chosen:
            self.folder.set(chosen)

    def start(self) -> None:
        if self.closing:
            return
        sheet = self.sheet_url.get().strip()
        folder = Path(self.folder.get().strip()).expanduser()
        if not sheet or not self.folder.get().strip():
            messagebox.showerror("Thiếu thông tin", "Hãy nhập link Google Sheet và chọn thư mục video.")
            return
        if not folder.is_dir():
            messagebox.showerror("Thư mục không tồn tại", "Hãy chọn một thư mục video đã tồn tại.")
            return
        workers = int(self.max_workers.get())
        try:
            min_views = parse_views(self.min_views.get())
            min_duration = minutes_to_seconds(self.min_duration.get())
            if min_views < 0 or min_duration < 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror('Bộ lọc không hợp lệ', 'Lượt xem phải là số nguyên không âm; thời lượng nhập bằng số phút không âm.')
            return
        output_format = FORMAT_OPTIONS[self.output_format.get()]
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps({"sheet_url": sheet, "folder": str(folder), "max_workers": workers, 'output_format': output_format, 'min_views': min_views, 'min_duration': min_duration}, ensure_ascii=False, indent=2), encoding="utf-8")
        for item in self.table.get_children():
            self.table.delete(item)
        self.stop_requested.clear()
        self.completed_count = 0
        self.start_button.configure(state="disabled")
        self.import_history_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.worker_selector.configure(state="disabled")
        self.summary.set("Đang đọc Sheet...")
        self.format_selector.configure(state='disabled')
        self.worker = threading.Thread(target=self.run_worker, args=(sheet, folder, workers, output_format, min_views, min_duration), daemon=True)
        self.worker.start()

    def run_worker(self, sheet: str, folder: Path, workers: int, output_format: str = 'original', min_views: int = 0, min_duration: int = 0) -> None:
        try:
            run(sheet, folder, self.events.put, self.stop_requested.is_set, workers, output_format=output_format, min_views=min_views, min_duration=min_duration)
        except Exception as error:
            self.events.put({"type": "fatal", "message": str(error)})
        finally:
            self.events.put({"type": "idle"})

    def stop(self) -> None:
        self.stop_requested.set()
        self.summary.set("Sẽ dừng sau các lượt đang tải...")
        self.stop_button.configure(state="disabled")

    def poll_events(self) -> None:
        started = time.monotonic()
        try:
            # Give Tk time to repaint and handle clicks even when eight
            # downloads continuously enqueue progress notifications.
            for _ in range(100):
                if time.monotonic() - started >= 0.015:
                    break
                event = self.events.get_nowait()
                kind = event["type"]
                if kind == "start":
                    self.summary.set(f"Tìm thấy {event['total']} kênh · {event['workers']} luồng")
                elif kind == "row":
                    stt = event["stt"]
                    title = event.get("title", "")
                    if event.get("quality"):
                        title = f"{title} · {event['quality']}"
                    values = (event["position"], stt, event["status"], title, event["channel"])
                    if self.table.exists(stt):
                        self.table.item(stt, values=values)
                    else:
                        self.table.insert("", "end", iid=stt, values=values)
                    self.table.see(stt)
                    if event.get("completed"):
                        self.completed_count += 1
                        self.summary.set(f"Đã xử lý {self.completed_count}/{event['total']} kênh")
                elif kind == "progress":
                    self.progress.set(event["message"])
                    if self.table.exists(event["stt"]):
                        values = list(self.table.item(event["stt"], "values"))
                        values[2] = event["message"]
                        self.table.item(event["stt"], values=values)
                elif kind == "finished":
                    self.summary.set(f"Hoàn tất {event['total']} kênh")
                    self.progress.set("")
                elif kind == "stopped":
                    self.summary.set(f"Đã dừng sau {event['done']} kênh")
                    self.progress.set("")
                elif kind == "fatal":
                    self.summary.set("Đã dừng vì lỗi")
                    messagebox.showerror("Lỗi", event["message"])
                elif kind == "history_imported":
                    count = f"{event['unique']:,}".replace(",", ".")
                    added = f"{event['added']:,}".replace(",", ".")
                    records = f"{event['records']:,}".replace(",", ".")
                    self.summary.set(f"Đã nhập lịch sử: {count} video")
                    messagebox.showinfo("Đã nhập lịch sử YouTube", f"Đọc được {records} lượt xem, tương ứng {count} video khác nhau.\nThêm mới vào lịch sử xem: {added} video.\n\nTool sẽ bỏ qua các mã video này khi tải. Không cần giữ file HTML/JSON sau khi nhập.")
                elif kind == "import_error":
                    self.summary.set("Không nhập được lịch sử xem")
                    messagebox.showerror("Nhập lịch sử YouTube", event["message"])
                elif kind == "idle":
                    if not self.closing:
                        self.start_button.configure(state="normal")
                        self.import_history_button.configure(state="normal")
                        self.stop_button.configure(state="disabled")
                        self.worker_selector.configure(state="readonly")
                        self.format_selector.configure(state='readonly')
        except queue.Empty:
            pass
        except Exception:
            logging.getLogger(__name__).exception("Không cập nhật được một sự kiện giao diện")
        finally:
            # One malformed event must not permanently stop UI updates.
            if self.winfo_exists():
                self.after(100, self.poll_events)


if __name__ == "__main__":
    get_state_dir().mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(get_state_dir() / "application.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
    try:
        logging.info("Khởi động ứng dụng")
        DownloaderApp().mainloop()
    except Exception:
        logging.exception("Lỗi khởi động hoặc vòng lặp giao diện")
        raise
