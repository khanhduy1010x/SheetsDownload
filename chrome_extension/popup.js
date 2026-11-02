const tokenInput = document.querySelector("#token");
const status = document.querySelector("#status");
const button = document.querySelector("#connect");
const pickerToggle = document.querySelector("#picker-toggle");

function showPickerState(enabled) {
  pickerToggle.setAttribute("aria-pressed", String(enabled));
  pickerToggle.textContent = enabled ? "Tắt tiện ích trên YouTube" : "Bật tiện ích trên YouTube";
}

chrome.storage.local.get({ bridgeToken: "", pickerEnabled: false }).then(({ bridgeToken, pickerEnabled: enabled }) => {
  if (bridgeToken) tokenInput.value = bridgeToken;
  showPickerState(enabled === true);
  pickerToggle.disabled = false;
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

button.addEventListener("click", async () => {
  const token = tokenInput.value.trim();
  if (!token) {
    status.textContent = "Hãy nhập mã kết nối.";
    return;
  }
  button.disabled = true;
  status.textContent = "Đang kiểm tra...";
  try {
    const response = await fetch("http://127.0.0.1:39173/v1/health", {
      headers: { "Authorization": `Bearer ${token}` },
    });
    if (!response.ok) throw new Error("Mã kết nối không đúng.");
    await chrome.storage.local.set({ bridgeToken: token });
    status.textContent = "Đã kết nối. Anh có thể chọn video trên YouTube.";
  } catch (error) {
    status.textContent = error instanceof TypeError
      ? "Không thấy ứng dụng SheetsDownload đang chạy."
      : error.message;
  } finally {
    button.disabled = false;
  }
});
