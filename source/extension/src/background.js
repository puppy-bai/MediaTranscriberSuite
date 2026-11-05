const MEDIA_EXTS = [".m3u8", ".mpd", ".mp4", ".m4a", ".mp3", ".webm", ".wav", ".aac", ".ogg", ".flv", ".ts", ".mov", ".mkv"];
const MEDIA_MIMES = ["video/", "audio/", "mpegurl", "m3u8", "dash+xml", "mp4", "webm", "octet-stream"];
const capturesByTab = new Map();

function normalizeUrl(url) {
  try {
    return new URL(url).href;
  } catch {
    return "";
  }
}

function looksLikeMedia(url = "", mime = "") {
  const lowerUrl = String(url || "").toLowerCase().split("?")[0];
  const lowerMime = String(mime || "").toLowerCase();
  return MEDIA_EXTS.some((ext) => lowerUrl.endsWith(ext) || lowerUrl.includes(`${ext}/`)) ||
    MEDIA_MIMES.some((item) => lowerMime.includes(item));
}

function extFromUrl(url = "", mime = "") {
  const clean = String(url || "").toLowerCase().split("?")[0];
  const found = MEDIA_EXTS.find((ext) => clean.endsWith(ext) || clean.includes(`${ext}/`));
  if (found) return found.slice(1);
  if (String(mime).includes("audio")) return "audio";
  if (String(mime).includes("video")) return "video";
  return "media";
}

function sanitizeName(value) {
  return String(value || "media")
    .replace(/[\\/:*?"<>|\r\n\t]+/g, "_")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 90) || "media";
}

async function currentTabInfo(tabId) {
  try {
    const tab = await chrome.tabs.get(tabId);
    return {
      pageUrl: tab.url || "",
      pageTitle: tab.title || "",
      tabId,
    };
  } catch {
    return { pageUrl: "", pageTitle: "", tabId };
  }
}

async function rememberCapture(tabId, payload) {
  const url = normalizeUrl(payload.url);
  if (!url) return;
  const info = await currentTabInfo(tabId);
  const item = {
    ...payload,
    ...info,
    url,
    ext: payload.ext || extFromUrl(url, payload.mime),
    source: payload.source || "extension",
    capturedAt: new Date().toLocaleString(),
  };
  const list = capturesByTab.get(tabId) || [];
  const oldIndex = list.findIndex((old) => old.url === item.url && old.pageUrl === item.pageUrl);
  if (oldIndex >= 0) list.splice(oldIndex, 1);
  list.unshift(item);
  capturesByTab.set(tabId, list.slice(0, 120));
  chrome.action.setBadgeText({ tabId, text: String(Math.min(list.length, 99)) });
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#2563eb" });
}

async function downloadCapture(item) {
  const ext = item.ext || extFromUrl(item.url, item.mime);
  const title = sanitizeName(item.pageTitle || new URL(item.url).hostname || "media");
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const filename = `MediaDownloader/${title}_${stamp}.${ext || "media"}`;
  return chrome.downloads.download({
    url: item.url,
    filename,
    conflictAction: "uniquify",
    saveAs: false,
  });
}

chrome.webRequest.onResponseStarted.addListener(
  (details) => {
    if (details.tabId < 0 || !details.url) return;
    let mime = "";
    let size = "";
    for (const header of details.responseHeaders || []) {
      const name = (header.name || "").toLowerCase();
      if (name === "content-type") mime = header.value || "";
      if (name === "content-length") size = header.value || "";
    }
    if (!looksLikeMedia(details.url, mime)) return;
    rememberCapture(details.tabId, {
      url: details.url,
      method: details.method,
      mime,
      size,
      source: "webRequest",
    });
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (message?.type === "candidate-media" && sender.tab?.id >= 0) {
      if (looksLikeMedia(message.url, message.mime)) {
        await rememberCapture(sender.tab.id, {
          url: message.url,
          mime: message.mime || "",
          method: message.method || "GET",
          source: message.source || "content",
        });
      }
      sendResponse({ ok: true });
      return;
    }

    if (message?.type === "get-captures") {
      sendResponse({ ok: true, items: capturesByTab.get(message.tabId) || [] });
      return;
    }

    if (message?.type === "download-capture") {
      const downloadId = await downloadCapture(message.item);
      sendResponse({ ok: true, downloadId });
      return;
    }

    if (message?.type === "clear-tab") {
      capturesByTab.set(message.tabId, []);
      chrome.action.setBadgeText({ tabId: message.tabId, text: "" });
      sendResponse({ ok: true });
      return;
    }
  })().catch((error) => sendResponse({ ok: false, error: String(error.message || error) }));
  return true;
});
