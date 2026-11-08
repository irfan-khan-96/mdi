// Hands browser downloads to the MDI desktop app through native messaging
// (no network port: only the registered MDI host can receive them).
// - Right-click a link -> "Download with MDI"
// - Capture ON (toolbar button): http(s) downloads go to MDI instead. If MDI
//   isn't installed/registered, the browser downloads the file itself.

const HOST = "com.mdi.downloader";
const bypass = new Set(); // URLs we hand back to the browser

async function captureOn() {
  const { capture = true } = await chrome.storage.local.get("capture");
  return capture;
}

async function showState() {
  const on = await captureOn();
  chrome.action.setBadgeText({ text: on ? "ON" : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#3a7bd5" });
}

function isHttp(url) {
  try {
    return ["http:", "https:"].includes(new URL(url).protocol);
  } catch (_) {
    return false;
  }
}

// Rough "site" (registrable domain) for SameSite decisions. When unsure it
// errs towards "different site", which only means fewer cookies are sent.
function site(url) {
  try {
    const parts = new URL(url).hostname.split(".");
    const n = parts.length >= 3 && parts[parts.length - 2].length <= 3 &&
      parts[parts.length - 1].length === 2 ? 3 : 2;
    return parts.slice(-n).join(".");
  } catch (_) {
    return "";
  }
}

// Same cookies the browser itself would send for this download: Secure
// cookies only over https (chrome.cookies filters that), and SameSite=Strict
// cookies only when the download was started from the same site.
async function cookiesFor(url, referer) {
  const crossSite = referer && site(referer) !== site(url);
  try {
    const list = await chrome.cookies.getAll({ url });
    return list
      .filter((c) => !(crossSite && c.sameSite === "strict"))
      .map((c) => `${c.name}=${c.value}`)
      .join("; ");
  } catch (_) {
    return "";
  }
}

async function sendToMdi(url, referer, filename) {
  if (!isHttp(url)) throw new Error("not an http(s) link");
  const msg = {
    type: "add",
    url,
    referer: isHttp(referer || "") ? referer : "",
    cookies: await cookiesFor(url, referer),
    filename: filename || null,
  };
  const res = await chrome.runtime.sendNativeMessage(HOST, msg);
  if (!res || !res.ok) throw new Error((res && res.error) || "MDI refused");
}

function fallbackToBrowser(url) {
  bypass.add(url);
  chrome.downloads.download({ url });
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "mdi",
    title: "Download with MDI",
    contexts: ["link", "video", "audio", "image"],
  });
  showState();
});
chrome.runtime.onStartup.addListener(showState);

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.linkUrl || info.srcUrl;
  if (!url || !isHttp(url)) return;
  try {
    await sendToMdi(url, info.pageUrl || tab?.url || "");
  } catch (_) {
    fallbackToBrowser(url);
  }
});

chrome.action.onClicked.addListener(async () => {
  await chrome.storage.local.set({ capture: !(await captureOn()) });
  showState();
});

chrome.downloads.onCreated.addListener(async (item) => {
  const url = item.finalUrl || item.url;
  if (!isHttp(url) || item.byExtensionId === chrome.runtime.id) return;
  if (bypass.delete(url) || !(await captureOn())) return;
  if (item.incognito) return; // never move private-window downloads out

  await chrome.downloads.cancel(item.id).catch(() => {});
  try {
    const name = item.filename ? item.filename.split(/[\\/]/).pop() : "";
    await sendToMdi(url, item.referrer || "", name || undefined);
    chrome.downloads.erase({ id: item.id }).catch(() => {});
  } catch (_) {
    fallbackToBrowser(url);
  }
});
