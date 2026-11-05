async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

function shortUrl(url) {
  try {
    const parsed = new URL(url);
    return `${parsed.hostname}${parsed.pathname}`.slice(0, 92);
  } catch {
    return String(url || "").slice(0, 92);
  }
}

function sizeText(value) {
  const size = Number(value || 0);
  if (!size) return "";
  if (size > 1024 * 1024 * 1024) return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`;
  if (size > 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
  if (size > 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${size} B`;
}

async function renderList() {
  const tab = await activeTab();
  const list = document.getElementById("list");
  const status = document.getElementById("statusText");
  const result = await chrome.runtime.sendMessage({ type: "get-captures", tabId: tab.id });
  const items = result.items || [];
  list.innerHTML = "";
  status.textContent = items.length ? `已捕获 ${items.length} 个媒体资源` : "播放页面视频后自动捕获资源";
  if (!items.length) {
    list.innerHTML = '<div class="empty">还没发现媒体资源。播放一下视频，或刷新页面后再打开弹窗。</div>';
    return;
  }
  for (const item of items) {
    const el = document.createElement("div");
    el.className = "item";
    el.innerHTML = `
      <div class="title">${shortUrl(item.url)}</div>
      <div class="meta">${item.mime || item.ext || "media"} ${sizeText(item.size)} · ${item.source || ""}</div>
      <div class="item-actions">
        <button class="download">下载</button>
        <button class="copy secondary">复制链接</button>
      </div>
    `;
    el.querySelector(".download").addEventListener("click", async () => {
      const res = await chrome.runtime.sendMessage({ type: "download-capture", item });
      status.textContent = res.ok ? "已添加到浏览器下载任务" : `下载失败：${res.error || "未知错误"}`;
    });
    el.querySelector(".copy").addEventListener("click", async () => {
      await navigator.clipboard.writeText(item.url);
      status.textContent = "链接已复制";
    });
    list.appendChild(el);
  }
}

document.getElementById("refreshBtn").addEventListener("click", renderList);

document.getElementById("clearBtn").addEventListener("click", async () => {
  const tab = await activeTab();
  await chrome.runtime.sendMessage({ type: "clear-tab", tabId: tab.id });
  await renderList();
});

renderList();
