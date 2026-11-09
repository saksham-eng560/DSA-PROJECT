/**
 * Runs on linkedin.com: finds the signed-in member's public profile URL (from the "Me" menu)
 * so the dashboard can sync your profile. It never reads messages or other page content.
 */
(function detectProfileUrl() {
  const find = () => {
    const link = document.querySelector(
      "a.global-nav__primary-link-me-menu-trigger[href*='/in/'], a[data-control-name='identity_welcome_message'][href*='/in/'], .feed-identity-module a[href*='/in/']",
    );
    if (link && link.href) {
      chrome.runtime.sendMessage({ type: "profile-url", url: link.href });
      return true;
    }
    return false;
  };
  if (!find()) {
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (find() || attempts > 10) clearInterval(timer);
    }, 1500);
  }
})();
