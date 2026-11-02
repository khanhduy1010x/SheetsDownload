const BRIDGE_URL = "http://127.0.0.1:39173";

async function bridgeAvailable() {
  const response = await fetch(`${BRIDGE_URL}/v1/health`, { cache: "no-store" });
  if (!response.ok) throw new Error("Không kết nối được SheetsDownload. Hãy mở ứng dụng trên máy.");
  const result = await response.json();
  if (result?.ok !== true) throw new Error("Cổng kết nối đang được ứng dụng khác sử dụng.");
}

chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !changes.pickerEnabled) return;
  const enabled = changes.pickerEnabled.newValue === true;
  chrome.tabs.query({ url: "https://www.youtube.com/*" }).then((tabs) => {
    for (const tab of tabs) {
      chrome.tabs.sendMessage(tab.id, { type: "pickerEnabledChanged", enabled }).catch(() => {});
    }
  }).catch(() => {});
});

function youtubeId(rawUrl) {
  try {
    const url = new URL(rawUrl);
    if (url.protocol !== "https:" || url.hostname !== "www.youtube.com") return null;
    const id = url.pathname === "/watch"
      ? url.searchParams.get("v")
      : url.pathname.match(/^\/(?:shorts|live)\/([A-Za-z0-9_-]{11})\/?$/)?.[1];
    return /^[A-Za-z0-9_-]{11}$/.test(id || "") ? id : null;
  } catch {
    return null;
  }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.type === "getConnectionStatus") {
    bridgeAvailable().then(() => respond({ ok: true })).catch((error) => {
      respond({ ok: false, error: error instanceof TypeError
        ? "Mở ứng dụng SheetsDownload để nhận video."
        : error.message });
    });
    return true;
  }
  if (message?.type === "getPickerEnabled") {
    chrome.storage.local.get({ pickerEnabled: false }).then(({ pickerEnabled }) => {
      respond({ enabled: pickerEnabled === true });
    });
    return true;
  }
  if (message?.type !== "sendVideos") return;
  const source = sender.url || sender.tab?.url || "";
  if (!source.startsWith("https://www.youtube.com/")) {
    respond({ ok: false, error: "Chỉ nhận video từ trang YouTube." });
    return;
  }
  if (!["list", "download"].includes(message.action) || !Array.isArray(message.videos)
      || message.videos.length < 1 || message.videos.length > 200) {
    respond({ ok: false, error: "Danh sách video không hợp lệ." });
    return;
  }
  const videos = [];
  const seen = new Set();
  for (const item of message.videos) {
    const id = youtubeId(item?.url);
    if (!id || typeof item.title !== "string") {
      respond({ ok: false, error: "Có link video không hợp lệ." });
      return;
    }
    if (!seen.has(id)) {
      seen.add(id);
      videos.push({ url: `https://www.youtube.com/watch?v=${id}`, title: item.title.slice(0, 500) });
    }
  }
  (async () => {
    const response = await fetch(`${BRIDGE_URL}/v1/videos`, {
      method: "POST",
      headers: { "X-SheetsDownload-Client": "extension", "Content-Type": "application/json" },
      body: JSON.stringify({ action: message.action, videos }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "SheetsDownload từ chối danh sách video.");
    respond(result);
  })().catch((error) => {
    const detail = error instanceof TypeError
      ? "Không kết nối được SheetsDownload. Hãy mở ứng dụng trên máy."
      : error.message;
    respond({ ok: false, error: detail });
  });
  return true;
});
