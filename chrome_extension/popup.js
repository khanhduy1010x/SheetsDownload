const status = document.querySelector("#status");
const connectionStatus = document.querySelector("#connection-status");
const pickerToggle = document.querySelector("#picker-toggle");

function showPickerState(enabled) {
  pickerToggle.setAttribute("aria-pressed", String(enabled));
  pickerToggle.textContent = enabled ? "Tắt tiện ích trên YouTube" : "Bật tiện ích trên YouTube";
}

chrome.storage.local.get({ pickerEnabled: false }).then(({ pickerEnabled: enabled }) => {
  showPickerState(enabled === true);
  pickerToggle.disabled = false;
});

chrome.runtime.sendMessage({ type: "getConnectionStatus" }, (response) => {
  connectionStatus.textContent = chrome.runtime.lastError
    ? "Không kiểm tra được kết nối. Hãy mở lại extension."
    : response?.ok ? "Đã kết nối với SheetsDownload." : response?.error || "Mở ứng dụng SheetsDownload để nhận video.";
});

pickerToggle.addEventListener("click", async () => {
  const enabled = pickerToggle.getAttribute("aria-pressed") !== "true";
  pickerToggle.disabled = true;
  try {
    await chrome.storage.local.set({ pickerEnabled: enabled });
    showPickerState(enabled);
    status.textContent = enabled ? "Đã hiện nút chọn video trên YouTube." : "Đã ẩn nút chọn video trên YouTube.";
  } catch {
    status.textContent = "Không đổi được trạng thái. Hãy thử lại.";
  } finally {
    pickerToggle.disabled = false;
  }
});
