const $ = (id) => document.getElementById(id);

// No answer from the background worker means Chrome is still running an older copy of the extension
// (it picks up new background code and permissions only on a reload).
const RELOAD = "Reload the extension: open chrome://extensions, click ↻ on AutoApply AI, then open this popup again.";

async function ask(message) {
  try {
    return (await chrome.runtime.sendMessage(message)) || { ok: false, error: RELOAD };
  } catch (err) {
    return { ok: false, error: `${RELOAD} (${err.message || err})` };
  }
}

function setMessage(text, ok, id = "message") {
  const el = $(id);
  el.textContent = text || "";
  el.className = ok ? "ok" : "err";
  el.style.marginTop = text ? "6px" : "0";
}

function showLogin(id, loggedIn) {
  $(id).textContent = loggedIn ? "✓ logged in" : "not logged in";
  $(id).className = loggedIn ? "ok" : "err";
}

async function refresh() {
  const status = await ask({ type: "status" });
  if (status.dashboardUrl) $("dashboardUrl").value = status.dashboardUrl;
  $("token").placeholder = status.token ? "•••••••• (saved — paste to replace)" : $("token").placeholder;
  showLogin("login", status.loggedIn);
  $("lastSync").textContent = status.lastSync ? new Date(status.lastSync).toLocaleString() : "never";
  if (status.lastError || status.error) setMessage(status.lastError || status.error, false);
  if (!status.internshala) {  // a background from before Internshala support
    $("internshalaLogin").textContent = "reload needed";
    $("internshalaLogin").className = "err";
    setMessage(RELOAD, false, "internshalaMessage");
    return;
  }
  const { internshala } = status;
  showLogin("internshalaLogin", internshala.loggedIn);
  $("internshalaLastSync").textContent = internshala.lastSync ? new Date(internshala.lastSync).toLocaleString() : "never";
  if (internshala.lastError) setMessage(internshala.lastError, false, "internshalaMessage");
}

$("config").addEventListener("submit", async (event) => {
  event.preventDefault();
  const dashboardUrl = $("dashboardUrl").value.trim().replace(/\/+$/, "");
  let origin;
  try {
    origin = new URL(dashboardUrl).origin;
  } catch {
    setMessage("Enter a valid URL, e.g. https://autoapply.example.com", false);
    return;
  }
  // Ask for access to the dashboard origin only (optional host permission).
  const granted = await chrome.permissions.request({ origins: [`${origin}/*`] });
  if (!granted) {
    setMessage("Permission to contact your dashboard was denied.", false);
    return;
  }
  const update = { dashboardUrl };
  const token = $("token").value.trim();
  if (token) update.token = token;
  await chrome.storage.local.set(update);
  $("token").value = "";
  setMessage("Settings saved.", true);
  refresh();
});

$("sync").addEventListener("click", async () => {
  $("sync").disabled = true;
  $("sync").textContent = "Syncing…";
  const result = await ask({ type: "sync" });
  $("sync").disabled = false;
  $("sync").textContent = "Sync LinkedIn session";
  setMessage(result.ok ? "Synced! Easy Apply is ready in your dashboard." : result.error, result.ok);
  refresh();
});

$("syncInternshala").addEventListener("click", async () => {
  $("syncInternshala").disabled = true;
  $("syncInternshala").textContent = "Syncing…";
  const result = await ask({ type: "sync-internshala" });
  $("syncInternshala").disabled = false;
  $("syncInternshala").textContent = "Sync Internshala session";
  setMessage(result.ok ? "Synced! Turn on the Internshala bot in Settings → Integrations." : result.error, result.ok,
    "internshalaMessage");
  refresh();
});

refresh();
