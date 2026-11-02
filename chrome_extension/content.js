(() => {
  if (document.querySelector("#sd-video-picker")) return;

  const selected = new Map();
  let selecting = false;
  let dragStart = null;
  let dragMatches = new Map();
  let ignoreNextClick = false;
  let pickerSettingUpdated = false;
  const cardSelector = [
    "ytd-rich-grid-media", "ytd-grid-video-renderer", "ytd-video-renderer",
    "ytd-compact-video-renderer", "ytd-reel-item-renderer",
    "ytd-rich-grid-slim-media", "ytm-shorts-lockup-view-model-v2",
  ].join(",");

  const root = document.createElement("div");
  root.id = "sd-video-picker";
  root.innerHTML = `
    <button class="sd-toggle" type="button" aria-pressed="false">Bật chế độ chọn</button>
    <section class="sd-panel" aria-label="Video đã chọn" hidden>
      <div class="sd-heading"><strong>Video đã chọn <span class="sd-count">0</span></strong><button class="sd-close" type="button" aria-label="Đóng danh sách">×</button></div>
      <p class="sd-guide">Kéo một vùng qua các video hoặc bấm từng video để chọn.</p>
      <div class="sd-list" role="list"></div>
      <p class="sd-status" role="status" aria-live="polite"></p>
      <div class="sd-actions">
        <button class="sd-clear" type="button">Bỏ chọn</button>
        <button class="sd-add" type="button">Thêm vào danh sách</button>
        <button class="sd-download" type="button">Tải ngay</button>
      </div>
    </section>
    <div class="sd-marquee" hidden></div>
  `;
  root.hidden = true;
  document.documentElement.append(root);
  const toggle = root.querySelector(".sd-toggle");
  const panel = root.querySelector(".sd-panel");
  const list = root.querySelector(".sd-list");
  const count = root.querySelector(".sd-count");
  const status = root.querySelector(".sd-status");
  const marquee = root.querySelector(".sd-marquee");

  function videoFromCard(card) {
    const links = card.querySelectorAll('a[href*="/watch?"], a[href*="/shorts/"], a[href*="/live/"]');
    for (const link of links) {
      try {
        const url = new URL(link.href);
        if (url.hostname !== "www.youtube.com") continue;
        const id = url.pathname === "/watch"
          ? url.searchParams.get("v")
          : url.pathname.match(/^\/(?:shorts|live)\/([A-Za-z0-9_-]{11})\/?$/)?.[1];
        if (!/^[A-Za-z0-9_-]{11}$/.test(id || "")) continue;
        const titleNode = card.querySelector("#video-title, #video-title-link, h3, a[title]");
        const title = titleNode?.getAttribute("title") || titleNode?.textContent?.trim() || id;
        return { id, title: title.slice(0, 500), url: `https://www.youtube.com/watch?v=${id}` };
      } catch {
        continue;
      }
    }
    return null;
  }

  function visibleCards() {
    return [...document.querySelectorAll(cardSelector)].filter((card) => {
      const rect = card.getBoundingClientRect();
      return rect.width > 20 && rect.height > 20 && rect.bottom > 0 && rect.top < innerHeight;
    });
  }

  function markCards() {
    for (const card of visibleCards()) {
      const video = videoFromCard(card);
      card.classList.toggle("sd-picked", Boolean(video && selected.has(video.id)));
      card.classList.toggle("sd-preview", Boolean(video && dragMatches.has(video.id)));
    }
  }

  function render() {
    count.textContent = String(selected.size);
    list.replaceChildren();
    if (!selected.size) {
      const empty = document.createElement("p");
      empty.className = "sd-empty";
      empty.textContent = "Chưa chọn video nào.";
      list.append(empty);
    }
    for (const video of selected.values()) {
      const row = document.createElement("div");
      row.className = "sd-item";
      row.setAttribute("role", "listitem");
      const link = document.createElement("a");
      link.href = video.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = video.title;
      link.title = video.url;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Bỏ";
      remove.setAttribute("aria-label", `Bỏ chọn ${video.title}`);
      remove.addEventListener("click", () => {
        selected.delete(video.id);
        render();
      });
      row.append(link, remove);
      list.append(row);
    }
    root.querySelector(".sd-add").disabled = selected.size === 0;
    root.querySelector(".sd-download").disabled = selected.size === 0;
    markCards();
  }

  function setSelecting(next) {
    selecting = next;
    toggle.textContent = next ? "Tắt chế độ chọn" : "Bật chế độ chọn";
    toggle.setAttribute("aria-pressed", String(next));
    panel.hidden = !next && selected.size === 0;
    document.documentElement.classList.toggle("sd-selecting-mode", next);
    status.textContent = "";
  }

  function setPickerEnabled(enabled) {
    if (!enabled) {
      setSelecting(false);
      dragStart = null;
      dragMatches.clear();
      marquee.hidden = true;
      markCards();
    }
    root.hidden = !enabled;
  }

  function selectVideo(video) {
    if (!video) return;
    if (selected.has(video.id)) selected.delete(video.id);
    else if (selected.size < 200) selected.set(video.id, video);
    else status.textContent = "Mỗi lần chọn tối đa 200 video.";
    render();
  }

  function selectionRect(start, end) {
    return {
      left: Math.min(start.x, end.x), top: Math.min(start.y, end.y),
      right: Math.max(start.x, end.x), bottom: Math.max(start.y, end.y),
    };
  }

  function showDrag(end) {
    const rect = selectionRect(dragStart, end);
    marquee.hidden = false;
    Object.assign(marquee.style, {
      left: `${rect.left}px`, top: `${rect.top}px`,
      width: `${rect.right - rect.left}px`, height: `${rect.bottom - rect.top}px`,
    });
    dragMatches = new Map();
    for (const card of visibleCards()) {
      const box = card.getBoundingClientRect();
      if (box.left < rect.right && box.right > rect.left && box.top < rect.bottom && box.bottom > rect.top) {
        const video = videoFromCard(card);
        if (video) dragMatches.set(video.id, video);
      }
    }
    markCards();
  }

  toggle.addEventListener("click", () => setSelecting(!selecting));
  root.querySelector(".sd-close").addEventListener("click", () => {
    setSelecting(false);
    panel.hidden = true;
  });
  root.querySelector(".sd-clear").addEventListener("click", () => {
    selected.clear();
    status.textContent = "Đã bỏ chọn.";
    render();
  });
  for (const [selector, action] of [[".sd-add", "list"], [".sd-download", "download"]]) {
    root.querySelector(selector).addEventListener("click", () => {
      const videos = [...selected.values()];
      const buttons = root.querySelectorAll(".sd-add, .sd-download");
      buttons.forEach((button) => { button.disabled = true; });
      status.textContent = "Đang gửi sang SheetsDownload...";
      chrome.runtime.sendMessage({ type: "sendVideos", action, videos }, (response) => {
        buttons.forEach((button) => { button.disabled = selected.size === 0; });
        if (chrome.runtime.lastError || !response?.ok) {
          status.textContent = response?.error || "Không gửi được. Hãy kiểm tra kết nối với SheetsDownload.";
        } else {
          status.textContent = action === "list"
            ? `Đã thêm ${response.received} video vào danh sách trong SheetsDownload.`
            : `Đã gửi ${response.received} video vào hàng chờ tải ngay.`;
        }
      });
    });
  }

  document.addEventListener("pointerdown", (event) => {
    if (!selecting || event.button !== 0 || root.contains(event.target)) return;
    dragStart = { x: event.clientX, y: event.clientY };
    dragMatches.clear();
    ignoreNextClick = true;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  document.addEventListener("pointermove", (event) => {
    if (!dragStart) return;
    if (Math.hypot(event.clientX - dragStart.x, event.clientY - dragStart.y) < 6) return;
    showDrag({ x: event.clientX, y: event.clientY });
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  document.addEventListener("pointerup", (event) => {
    if (!dragStart) return;
    if (marquee.hidden) {
      const card = event.target.closest(cardSelector);
      selectVideo(card && videoFromCard(card));
    } else {
      for (const video of dragMatches.values()) {
        if (selected.size >= 200) break;
        selected.set(video.id, video);
      }
      render();
    }
    dragStart = null;
    dragMatches.clear();
    marquee.hidden = true;
    markCards();
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  document.addEventListener("pointercancel", () => {
    dragStart = null;
    dragMatches.clear();
    marquee.hidden = true;
    ignoreNextClick = false;
    markCards();
  }, true);
  document.addEventListener("click", (event) => {
    if (!ignoreNextClick) return;
    if (root.contains(event.target)) {
      ignoreNextClick = false;
      return;
    }
    ignoreNextClick = false;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && selecting) setSelecting(false);
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "pickerEnabledChanged") {
      pickerSettingUpdated = true;
      setPickerEnabled(message.enabled === true);
    }
  });

  chrome.runtime.sendMessage({ type: "getPickerEnabled" }, (response) => {
    if (!pickerSettingUpdated && !chrome.runtime.lastError) setPickerEnabled(response?.enabled === true);
  });

  render();
})();
