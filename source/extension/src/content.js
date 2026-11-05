(function () {
  const mediaExts = [".m3u8", ".mpd", ".mp4", ".m4a", ".mp3", ".webm", ".wav", ".aac", ".ogg", ".flv", ".ts", ".mov"];

  function looksLikeUrl(value) {
    if (!value || typeof value !== "string") return false;
    const lower = value.toLowerCase().split("?")[0];
    return mediaExts.some((ext) => lower.endsWith(ext) || lower.includes(`${ext}/`));
  }

  function report(url, source, method = "GET", mime = "") {
    if (!looksLikeUrl(url)) return;
    try {
      chrome.runtime.sendMessage({ type: "candidate-media", url, source, method, mime });
    } catch {
      // Extension context may be invalid during reload.
    }
  }

  function absolute(url) {
    try {
      return new URL(url, location.href).href;
    } catch {
      return "";
    }
  }

  const observer = new MutationObserver(() => {
    for (const el of document.querySelectorAll("video[src], audio[src], source[src]")) {
      report(absolute(el.currentSrc || el.src), "dom-media");
    }
  });
  observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["src"] });

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.data?.source !== "media-text-suite-page-hook") return;
    report(event.data.url, event.data.kind || "page-hook", event.data.method || "GET", event.data.mime || "");
  });

  const script = document.createElement("script");
  script.textContent = `
    (() => {
      const exts = ${JSON.stringify(mediaExts)};
      const looks = (value) => {
        if (!value || typeof value !== "string") return false;
        const lower = value.toLowerCase().split("?")[0];
        return exts.some((ext) => lower.endsWith(ext) || lower.includes(ext + "/"));
      };
      const post = (url, kind, method, mime) => {
        if (looks(url)) window.postMessage({ source: "media-text-suite-page-hook", url, kind, method, mime }, "*");
      };
      const oldFetch = window.fetch;
      window.fetch = async function(input, init) {
        const url = typeof input === "string" ? input : input && input.url;
        post(url, "fetch", (init && init.method) || "GET", "");
        const res = await oldFetch.apply(this, arguments);
        const type = res.headers && res.headers.get("content-type");
        if (type && /video|audio|mpegurl|dash\\+xml|m3u8/i.test(type)) post(res.url || url, "fetch-response", "GET", type);
        return res;
      };
      const oldOpen = XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open = function(method, url) {
        this.__mtsUrl = url;
        this.__mtsMethod = method || "GET";
        post(url, "xhr", method || "GET", "");
        return oldOpen.apply(this, arguments);
      };
      const oldSend = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.send = function() {
        this.addEventListener("load", () => {
          const type = this.getResponseHeader && this.getResponseHeader("content-type");
          if (type && /video|audio|mpegurl|dash\\+xml|m3u8/i.test(type)) post(this.responseURL || this.__mtsUrl, "xhr-response", this.__mtsMethod, type);
        });
        return oldSend.apply(this, arguments);
      };
    })();
  `;
  (document.documentElement || document.head).appendChild(script);
  script.remove();
})();
