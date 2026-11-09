/**
 * AutoApply AI extension service worker.
 *
 * LinkedIn: reads the `li_at` session cookie (only when you're logged in).
 * Internshala: reads your internshala.com cookies, httpOnly ones included (PHPSESSID, l, csrf_cookie_name…),
 * only when you're logged in and only after you've clicked "Sync Internshala session" once.
 *
 * Both go to YOUR AutoApply AI dashboard over HTTPS, authenticated with a limited-scope extension token,
 * and the backend stores them encrypted (AES-256-GCM). Nothing is sent anywhere else.
 */

const SYNC_ALARM = "autoapply-linkedin-sync";
const SYNC_EVERY_MINUTES = 12 * 60;
const INTERNSHALA_DOMAIN = "internshala.com";
// Cookies that change when you log in or Internshala renews your session.
const INTERNSHALA_SESSION_COOKIES = ["PHPSESSID", "l", "is_logged_in", "sessionToken", "persistentSession"];

async function getConfig() {
  const { dashboardUrl, token, profileUrl, lastSync, lastError } = await chrome.storage.local.get([
    "dashboardUrl", "token", "profileUrl", "lastSync", "lastError",
  ]);
  return { dashboardUrl: (dashboardUrl || "").replace(/\/+$/, ""), token, profileUrl, lastSync, lastError };
}

async function readLinkedInCookie() {
  const cookie = await chrome.cookies.get({ url: "https://www.linkedin.com", name: "li_at" });
  return cookie ? cookie.value : null;
}

async function syncSession(reason = "manual") {
  const config = await getConfig();
  if (!config.dashboardUrl || !config.token) {
    return { ok: false, error: "Set your dashboard URL and extension token first." };
  }
  const liAt = await readLinkedInCookie();
  if (!liAt) {
    const error = "You're not logged into LinkedIn in this browser.";
    await chrome.storage.local.set({ lastError: error });
    return { ok: false, error };
  }
  try {
    const response = await fetch(`${config.dashboardUrl}/api/v1/users/me/integrations/linkedin-cookie`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${config.token}` },
      body: JSON.stringify({ li_at: liAt, profile_url: config.profileUrl || null }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const error = response.status === 401
        ? "Token rejected — generate a new extension token in Settings → Integrations."
        : `Sync failed (${response.status}) ${body.detail || ""}`.trim();
      await chrome.storage.local.set({ lastError: error });
      return { ok: false, error };
    }
    const lastSync = new Date().toISOString();
    await chrome.storage.local.set({ lastSync, lastError: null, lastReason: reason });
    await chrome.action.setBadgeText({ text: "" });
    return { ok: true, lastSync };
  } catch (err) {
    const error = `Could not reach ${config.dashboardUrl}: ${err.message || err}`;
    await chrome.storage.local.set({ lastError: error });
    return { ok: false, error };
  }
}

function isInternshalaDomain(domain) {
  const bare = (domain || "").replace(/^\./, "");
  return bare === INTERNSHALA_DOMAIN || bare.endsWith(`.${INTERNSHALA_DOMAIN}`);
}

// Every internshala.com cookie, httpOnly ones included, with what the server needs to replay it
// (host-only cookies and .internshala.com domain cookies are kept apart).
async function readInternshalaCookies() {
  const cookies = await chrome.cookies.getAll({ domain: INTERNSHALA_DOMAIN });
  return cookies
    .filter((c) => isInternshalaDomain(c.domain))
    .map(({ name, value, domain, path, secure, httpOnly, sameSite, expirationDate, hostOnly }) => (
      { name, value, domain, path, secure, httpOnly, sameSite, expirationDate, hostOnly }
    ));
}

// Same rule as the server: logged in = is_logged_in=1, or a session plus the remember-me cookie.
function internshalaLoggedIn(cookies) {
  const value = (name) => (cookies.find((c) => c.name === name) || {}).value;
  return value("is_logged_in") === "1" || (!!value("PHPSESSID") && !!value("l"));
}

async function syncInternshala(reason = "manual") {
  const config = await getConfig();
  if (!config.dashboardUrl || !config.token) {
    return { ok: false, error: "Set your dashboard URL and extension token first." };
  }
  const { internshalaEnabled } = await chrome.storage.local.get(["internshalaEnabled"]);
  if (reason !== "manual" && !internshalaEnabled) {
    return { ok: false, error: "Click “Sync Internshala session” once to turn on Internshala sync." };
  }
  const cookies = await readInternshalaCookies();
  if (!internshalaLoggedIn(cookies)) {
    const error = "You're not logged into Internshala in this browser.";
    await chrome.storage.local.set({ lastInternshalaError: error });
    return { ok: false, error };
  }
  try {
    const response = await fetch(`${config.dashboardUrl}/api/v1/users/me/integrations/internshala-session`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${config.token}` },
      // The bot presents this same browser to Internshala, which can tie a login to the browser it was made in.
      body: JSON.stringify({ cookies, reason, user_agent: navigator.userAgent }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const error = response.status === 401
        ? "Token rejected — generate a new extension token in Settings → Integrations."
        : `Sync failed (${response.status}) ${body.detail || ""}`.trim();
      // 409: you disconnected Internshala in the dashboard. Stop syncing it until you click Sync again.
      await chrome.storage.local.set({ lastInternshalaError: error, ...(response.status === 409 ? { internshalaEnabled: false } : {}) });
      return { ok: false, error };
    }
    const lastInternshalaSync = new Date().toISOString();
    await chrome.storage.local.set({ lastInternshalaSync, lastInternshalaError: null, internshalaEnabled: true });
    return { ok: true, lastSync: lastInternshalaSync };
  } catch (err) {
    const error = `Could not reach ${config.dashboardUrl}: ${err.message || err}`;
    await chrome.storage.local.set({ lastInternshalaError: error });
    return { ok: false, error };
  }
}

// Periodic re-sync keeps the backend's copy fresh (LinkedIn rotates sessions).
chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(SYNC_ALARM, { periodInMinutes: SYNC_EVERY_MINUTES });
});
chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(SYNC_ALARM, { periodInMinutes: SYNC_EVERY_MINUTES });
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === SYNC_ALARM) {
    syncSession("scheduled");
    syncInternshala("scheduled");
  }
});

// Re-sync shortly after LinkedIn issues a new session cookie (login / rotation).
let debounce;
chrome.cookies.onChanged.addListener(({ cookie, removed }) => {
  if (cookie.name !== "li_at" || !cookie.domain.includes("linkedin.com") || removed) return;
  clearTimeout(debounce);
  debounce = setTimeout(() => syncSession("cookie-changed"), 5000);
});

// Re-sync shortly after Internshala logs you in or renews your session (only once you've synced it yourself).
let internshalaDebounce;
chrome.cookies.onChanged.addListener(({ cookie, removed }) => {
  if (removed || !isInternshalaDomain(cookie.domain) || !INTERNSHALA_SESSION_COOKIES.includes(cookie.name)) return;
  clearTimeout(internshalaDebounce);
  internshalaDebounce = setTimeout(() => syncInternshala("cookie-changed"), 5000);
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "sync") {
    syncSession("manual").then(sendResponse);
    return true;
  }
  if (message?.type === "sync-internshala") {
    syncInternshala("manual").then(sendResponse);
    return true;
  }
  if (message?.type === "profile-url" && typeof message.url === "string" && message.url.includes("linkedin.com/in/")) {
    chrome.storage.local.set({ profileUrl: message.url.split("?")[0] });
  }
  if (message?.type === "status") {
    Promise.all([
      getConfig(),
      readLinkedInCookie(),
      readInternshalaCookies(),
      chrome.storage.local.get(["lastInternshalaSync", "lastInternshalaError", "internshalaEnabled"]),
    ]).then(([config, cookie, internshalaCookies, internshala]) =>
      sendResponse({
        ...config,
        token: config.token ? "set" : null,
        loggedIn: !!cookie,
        internshala: {
          loggedIn: internshalaLoggedIn(internshalaCookies),
          lastSync: internshala.lastInternshalaSync || null,
          lastError: internshala.lastInternshalaError || null,
          enabled: !!internshala.internshalaEnabled,
        },
      }),
    );
    return true;
  }
  return false;
});
