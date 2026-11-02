# SheetsDownload

Ứng dụng desktop Python/Tkinter tải video YouTube theo danh sách kênh trong Google Sheets.

## Tính năng

- Đọc STT ở cột A và link kênh YouTube ở cột E; Sheet cần cho phép xem bằng liên kết.
- Duyệt danh sách Popular theo thứ tự YouTube trả về, tải tối đa một video thành công cho mỗi kênh trong một lượt chạy.
- Chọn MP4 hoặc Tự động; độ phân giải tối đa 720p. MP4 ưu tiên nguồn MP4 sẵn có, chỉ chuyển mã khi cần.
- Lọc theo lượt xem tối thiểu và thời lượng tối thiểu tính bằng phút. Lượt xem hiển thị theo dạng `100.000`; đặt `0` để không giới hạn. Thời lượng hỗ trợ `1,5` hoặc `1.5` phút.
- Tải đồng thời từ 1 đến 8 kênh, kiểm tra và dự trù dung lượng trống.
- Ghi nhớ ID video đã tải thành công, kể cả khi file được xóa hoặc di chuyển.
- So khớp tiêu đề với tên các video có sẵn trong thư mục và thư mục con.
- Nhập lịch sử xem YouTube từ file Google Takeout HTML/JSON để bỏ qua các video đã xem.
- Sau một lần thử lại không thành công, ghi nhớ video lỗi theo ID rồi xét video kế tiếp trong cùng kênh. Các lần chạy sau không thử lại ID đã lỗi.
- Video không đạt bộ lọc không bị đưa vào danh sách lỗi; có thể xét lại sau khi đổi ngưỡng.
- Sửa lỗi giữ tệp mở ở nhánh hoàn tất HTTP 416 của yt-dlp và giải phóng tệp khi lượt tải thất bại.

## Chạy từ mã nguồn

Cần Python 3.11 trở lên, FFmpeg/FFprobe và Node.js trong `PATH`.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

Trên Windows có thể chạy `start_windows.bat`. Dán link Sheet, chọn thư mục video, định dạng, số luồng và bộ lọc rồi bấm **Bắt đầu tải**.

## Hai cách tải trong cùng ứng dụng

- **Tải theo Sheet / Kênh:** cách tải ban đầu, nhập link Google Sheet rồi chọn bộ lọc và bấm **Bắt đầu tải**.
- **Tải video đã chọn:** nhận video được chọn trực tiếp trên YouTube bằng extension Chrome. Chọn thư mục lưu một lần trong tab này.

## Cài ứng dụng và extension Chrome

1. Cài `VideoChannelDownloader-Setup.exe` và mở ứng dụng.
2. Cài extension trong thư mục `chrome_extension` đi kèm ứng dụng. Với bản chưa phát hành trên Chrome Web Store, mở `chrome://extensions`, bật **Chế độ dành cho nhà phát triển**, chọn **Tải tiện ích đã giải nén** và trỏ đến thư mục đó.
3. Bấm biểu tượng extension để bật công cụ trên YouTube. Khi cần chọn video, bấm **Bật chế độ chọn** ở thanh bên trái, kéo qua các ô hoặc bấm từng video. Sau đó chọn **Thêm vào danh sách** hoặc **Tải ngay**.

Ứng dụng và extension tự kết nối qua `127.0.0.1` khi ứng dụng đang mở; không cần sao chép hay nhập mã. **Thêm vào danh sách** giữ video trong tab **Tải video đã chọn** để tải sau. **Tải ngay** đưa video vào hàng chờ tải tự động; nếu ứng dụng đang tải từ Google Sheet thì hàng chờ này bắt đầu khi lượt Sheet kết thúc. Bấm **Tắt tiện ích trên YouTube** trong popup khi muốn ẩn nút nổi.

Hàng chờ được lưu trong `%APPDATA%\YouTubeChannelTool\selected_videos.sqlite3` trên Windows. Video đã tải được kiểm tra theo lịch sử hiện có để tránh tải trùng; việc chọn thủ công không áp dụng bộ lọc lượt xem/thời lượng của chế độ Google Sheet. Cầu nối chỉ lắng nghe trên `127.0.0.1`; extension gửi video trực tiếp khi ứng dụng đang mở.

Nút dừng sẽ chờ các lượt đang tải kết thúc. Đóng cửa sổ cũng chờ các luồng giải phóng tài nguyên trước khi thoát.

## Lịch sử và lỗi

Trên Windows, lịch sử SQLite, thiết lập và log được lưu trong `%APPDATA%\YouTubeChannelTool`, tách khỏi thư mục video. Thiết lập thời lượng được lưu bằng giây và hiển thị bằng phút để tương thích dữ liệu cũ.

Video đã tải, lịch sử xem đã nhập và video lỗi được lưu thành các nhóm riêng. Không xóa thư mục lịch sử nếu muốn giữ khả năng chống tải trùng và bỏ qua video lỗi.

Lỗi thiếu dung lượng hoặc không ghi được lịch sử sẽ dừng xếp thêm kênh. Hủy xử lý DNS không bị ghi thành lỗi vĩnh viễn của video.

Phiên bản này còn cơ chế thử `ipconfig /flushdns` khi gặp thông báo xác minh bot trên Windows, có thể hiện UAC. Xóa DNS không đổi IP và không bảo đảm khắc phục chặn bot. Bản này chưa tích hợp cookie, proxy hay PO Token Provider. Chỉ dùng cho nội dung bạn có quyền tải.

## Kiểm thử

```powershell
.venv\Scripts\python.exe -m unittest discover
```

Các kiểm thử media chạy bằng FFmpeg thật nếu tìm được `ffmpeg` trong `PATH`. Hoặc đặt `TEST_FFMPEG_LOCATION` thành thư mục chứa `ffmpeg.exe` và `ffprobe.exe` trước khi chạy. Kiểm thử HTTP dùng máy chủ cục bộ, không cần tải video YouTube thật. Các kiểm thử media sẽ được bỏ qua khi không có FFmpeg.

## Đóng gói Windows

Chạy `build_windows.bat` trên máy đã cài Python, FFmpeg/FFprobe và Node.js. Script cài thư viện build vào môi trường riêng và tạo `dist/VideoChannelDownloader.exe`, kèm các runtime cần thiết. Nếu có Inno Setup 6, script tạo thêm bộ cài từ `windows_installer.iss`.

Repository chỉ chứa mã nguồn, extension Chrome, icon, tài liệu và kiểm thử. Không chứa file EXE, cookie, mã kết nối, khóa riêng của extension, thiết lập cá nhân, lịch sử tải hay dữ liệu Google Sheets.
