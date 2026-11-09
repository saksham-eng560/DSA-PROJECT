"""Internshala apply bot (opt-in): fills, and once you approve, submits an Internshala application under
your own Internshala login (the cookies the browser extension syncs).

Internshala's terms don't allow automated access, so this only runs when you turn it on in Settings, at
most ``internshala_daily_limit`` applications a day (60–180 s apart, see ``rate_limiter``), and every
application waits for your click unless you also turn on "Submit automatically".

Flows handled:

* detail page → **Apply now** → easy-apply modal (``#easy_apply_modal``, same URL);
* detail page → **Apply now** → ``/student/resume`` interstitial → **Proceed to application** →
  ``/application/form``;
* external listings ("You will be redirected to another website"): not applied here, the external URL is
  returned so you can apply there;
* already applied, applications closed, profile incomplete, logged out: a clear error.

The form is read the way Internshala builds it (a Quill cover-letter editor, availability radios with
hidden inputs, ``.additional_question`` groups, a relocation checkbox) and answered by the same machinery
as every other form: saved answers first, then the answer engine with the job as context. Your Internshala
profile resume is what Internshala attaches; the agent never replaces it.

Every Internshala selector lives in ``SELECTORS`` below, so a markup change is a one-place fix.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from app.automation import human
from app.automation.browser import BrowserSession
from app.services.question_answerer import choose_option
from app.services.text_utils import clip, normalize_text
from app.submitters.base import BaseSubmitter, CandidatePacket, SessionExpired, SubmissionError, SubmissionResult
from app.submitters.form_engine import FormField, extract_fields, fill_field

logger = logging.getLogger(__name__)

BASE_URL = "https://internshala.com"
COVER_LETTER_MAX = 2000  # characters; Internshala's editor takes more, but reviewers read short letters
SESSION_COOKIES = frozenset({"PHPSESSID", "l", "sessionToken", "persistentSession"})

# ----------------------------------------------------------------------------------------------- selectors
# Researched from public automation repos (2025–2026), not verified against the live site. If Internshala
# changes its markup, this is the only place to update.
SELECTORS: dict[str, Any] = {
    # pages
    "dashboard_path": "/student/dashboard",          # logged in: stays here; logged out: redirects to /login
    "login_paths": ("/login",),
    # A logged-out visitor's "Apply now" leads to the candidate sign-up page ("Sign-up and apply for free")
    "signup_paths": ("/registration", "/signup", "/sign-up", "/register"),
    "login_modal": ("#login-modal", "#login_modal", ".login-modal", "form#login-form",
                    "text=/Sign-?up and apply for free/i", "text=/Already registered\\?/i"),
    "logged_in": "div.profile_icon_right",
    # detail page
    "apply_text": re.compile(r"^\s*apply( now)?\s*$", re.I),  # role=button / link name, tried first
    "apply_buttons": ("#top_easy_apply_button", ".top_apply_now_cta button", "a.top_apply_now_cta",
                      "#easy_apply_button", "#apply_now_button", "a.mobile_top_apply_now_cta"),
    "already_applied": ("button:has-text('Already Applied')", "a:has-text('Already Applied')",
                        ".apply_now_btn:disabled", ".apply_now_btn.disabled", "text=/you have already applied/i"),
    "closed": ("text=/Applications are closed/i",),
    # after "Apply now"
    "external_link": ("a.proceed-cta",),            # "You will be redirected to another website"
    "external_close": (".modal-content button.close",),
    "proceed": ("button.proceed-btn", "#layout_table .proceed-btn-container button", ".education_incomplete.proceed-btn",
                "button:has-text('Proceed to application')"),
    "profile_gate_paths": ("/student/personal_details", "/student/resume", "/student/profile"),
    "form_page_path": "/application/form",
    "form_roots": ("#easy_apply_modal", "#application-form"),
    # form
    "cover_letter_editor": "#cover_letter_holder .ql-editor",
    "cover_letter_holder": "#cover_letter_holder",
    "cover_letter_textarea": "textarea#cover_letter",
    "availability": "#confirm_availability_container",
    "availability_heading": ".availability_heading, .heading_6, h4, h5, legend, .form-label",
    "availability_other": "#confirm_availability_textarea",
    "question": ".form-group.additional_question",
    "question_label": ".assessment_question label",
    "relocation": "input[name='location_single']",
    "chosen": ".chosen-container",                 # chosen.js widgets wrap hidden <select>s
    "group": ".form-group",
    # submit + confirmation
    "submit": ("input#submit", "button#submit", "button:has-text('Submit')"),
    "validation_error": ("text=/This field is required/i",),
    "success": ("#continue_container", "#success_modal", "#success_modal_dual_button",
                "text=/application (has been )?submitted successfully/i"),
    "recommended_skip": ("#recommended_internships_modal button:has-text('Skip')", ".modal button:has-text('Skip')",
                         ".modal a:has-text('Skip')"),
}

NOT_SYNCED = ("Internshala login not synced — log into Internshala in Chrome and click “Sync Internshala session” "
              "in the AutoApply extension")
EXPIRED = ("Internshala didn't accept your synced login (it showed its sign-up / login page) — log into internshala.com "
           "in Chrome and click “Sync Internshala session” in the extension")
ALREADY_APPLIED = "You already applied to this internship on Internshala"
PROFILE_GATE = "Complete your Internshala profile first (Internshala asks for it before you can apply)"

# ------------------------------------------------------------------------------------------------ cookies
_SAME_SITE = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict"}


def internshala_cookies(cookies: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Chrome extension cookies (``chrome.cookies.Cookie``) → Playwright cookies.

    Host-only cookies (``PHPSESSID``, ``csrf_cookie_name``, ``l``…) keep a bare domain so they're only
    sent to internshala.com itself; domain cookies (``.internshala.com``: ``is_logged_in``, ``lc``) keep the
    leading dot. Expired cookies are dropped; session cookies stay session cookies.
    """
    out: list[dict[str, Any]] = []
    now = time.time()
    for c in cookies or []:
        name, value = str(c.get("name") or ""), str(c.get("value") or "")
        domain = str(c.get("domain") or "").strip().lower()
        if not name or not domain.lstrip("."):
            continue
        host_only = c.get("hostOnly")
        if host_only is None:
            host_only = not domain.startswith(".")
        bare = domain.lstrip(".")
        cookie: dict[str, Any] = {
            "name": name, "value": value, "domain": bare if host_only else f".{bare}",
            "path": str(c.get("path") or "/"), "httpOnly": bool(c.get("httpOnly")), "secure": bool(c.get("secure")),
        }
        same_site = _SAME_SITE.get(str(c.get("sameSite") or "").lower(), "Lax")  # "unspecified" behaves as Lax
        if same_site == "None" and not cookie["secure"]:
            same_site = "Lax"  # Chromium drops SameSite=None cookies that aren't Secure
        cookie["sameSite"] = same_site
        expires = c.get("expirationDate", c.get("expires"))
        if expires not in (None, "", -1):
            try:
                expires = float(expires)
            except (TypeError, ValueError):
                expires = None
            if expires is not None:
                if expires < now:
                    continue
                cookie["expires"] = expires
        out.append(cookie)
    return out


_TO_CHROME_SAME_SITE = {"None": "no_restriction", "Lax": "lax", "Strict": "strict"}


def extension_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Playwright cookies -> the extension's format (what ``internshala_cookies`` reads), Internshala's only."""
    out = []
    for c in cookies:
        domain = str(c.get("domain") or "")
        if not domain.lstrip(".").endswith("internshala.com"):
            continue
        expires = c.get("expires")
        out.append({"name": c["name"], "value": c.get("value", ""), "domain": domain, "path": c.get("path") or "/",
                    "secure": bool(c.get("secure")), "httpOnly": bool(c.get("httpOnly")),
                    "sameSite": _TO_CHROME_SAME_SITE.get(str(c.get("sameSite")), "unspecified"),
                    "expirationDate": float(expires) if expires and float(expires) > 0 else None,
                    "hostOnly": not domain.startswith(".")})
    return out


def has_login(cookies: list[dict[str, Any]] | None) -> bool:
    """The cookies of a logged-in Internshala account (the extension uses the same rule)."""
    values = {str(c.get("name")): str(c.get("value") or "") for c in cookies or []}
    return values.get("is_logged_in") == "1" or bool(values.get("PHPSESSID") and values.get("l"))


def _on_login_page(url: str) -> bool:
    """Internshala's login or candidate sign-up page: where it sends anyone it doesn't see as logged in."""
    path = urlparse(url).path.lower()
    return any(p in path for p in SELECTORS["login_paths"]) or path.startswith(SELECTORS["signup_paths"])


def browser_kwargs(cookies: list[dict[str, Any]] | None, user_agent: str | None) -> dict[str, Any]:
    """The bot's browser for your Internshala account: your cookies, presented by the same browser they
    came from (sites can tie a login to it) and from your own connection, never a scraping proxy."""
    return {"cookies": internshala_cookies(cookies), "timezone_id": "Asia/Kolkata", "user_agent": user_agent,
            "use_proxy": False}


def check_session(cookies: list[dict[str, Any]] | None, base_url: str = BASE_URL,
                  session_factory: Any = None, user_agent: str | None = None) -> bool:
    """Open ``/student/dashboard`` with your cookies in a real browser. Logged in when Internshala keeps
    you there (or shows your profile icon); a logged-out visitor is redirected to ``/login``."""
    factory = session_factory or BrowserSession
    with factory(**browser_kwargs(cookies, user_agent)) as session:
        page = session.page
        page.goto(f"{base_url.rstrip('/')}{SELECTORS['dashboard_path']}", wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001 - long-polling pages never go idle
            pass
        if _on_login_page(page.url):
            return False
        return SELECTORS["dashboard_path"] in urlparse(page.url).path or _first_visible(page, SELECTORS["logged_in"]) is not None


# -------------------------------------------------------------------------------------------- text helpers
_SALUTATION = re.compile(r"^(dear|hi|hello|to whom it may concern|respected)\b.{0,80}$", re.I)
_SIGN_OFF = re.compile(r"^(sincerely|best regards|kind regards|warm regards|regards|best|thanks|thank you|"
                       r"yours (sincerely|truly|faithfully)|cheers)[,.!]?$", re.I)


def internshala_cover_letter(text: str | None, limit: int = COVER_LETTER_MAX) -> str:
    """Plain text for "Why should you be hired for this role?": no salutation or signature, at most ``limit``."""
    lines = [line.strip() for line in (text or "").replace("\r", "").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    if lines and _SALUTATION.match(lines[0]):
        lines.pop(0)
    for idx, line in enumerate(lines):
        if _SIGN_OFF.match(line):
            lines = lines[:idx]
            break
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    body = "\n\n".join(re.sub(r"\s+", " ", p) for p in paragraphs).strip()
    return clip(body, limit) if len(body) > limit else body


_IMMEDIATE = re.compile(r"immediate|asap|right away|\bnow\b|\b0 days?\b|^yes\b|available to join")
_ON_NOTICE = re.compile(r"currently (on|serving)( a| my)? notice")


def availability_choice(text: str, options: list[str]) -> tuple[int, str | None]:
    """(option index, free text for "Other") for your availability answer. Index -1: no option fits."""
    norm = normalize_text(text)
    normalized = [normalize_text(o) for o in options]
    find = lambda pattern: next((i for i, o in enumerate(normalized) if re.search(pattern, o)), -1)  # noqa: E731
    other = find(r"\bother\b")
    if norm in normalized:
        idx = normalized.index(norm)
        return idx, None
    if _IMMEDIATE.search(norm):
        return find(r"immediate"), None
    if _ON_NOTICE.search(norm):
        return find(r"currently on notice"), None
    return other, (text.strip() if other >= 0 else None)


# ------------------------------------------------------------------------------------------- page scripts
EXTRACT_JS = r"""
(args) => {
  const S = args.sel;
  const root = document.querySelector(args.root) || document.body;
  const clean = (t) => (t || '').replace(/\s+/g, ' ').replace(/\*/g, '').trim();
  const visible = (el) => {
    if (!el) return false;
    const s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  let next = window.__aaNext || 0;
  const consumed = [];
  const mark = (el) => {
    let h = el.getAttribute('data-aa-id');
    if (!h) { h = `aa-${next++}`; el.setAttribute('data-aa-id', h); }
    consumed.push(h);
    return h;
  };
  // Every control inside a container we handle ourselves is left out of the generic pass.
  const consume = (box, handle) => {
    if (!box) return;
    box.querySelectorAll('input, select, textarea, [contenteditable="true"], [role="combobox"]').forEach((el) => {
      mark(el);
      if (el.type === 'radio' || el.type === 'checkbox') {
        if (!el.getAttribute('data-aa-group')) el.setAttribute('data-aa-group', handle);
        consumed.push(el.getAttribute('data-aa-group'));
      }
    });
  };
  const labelOf = (input) => {
    if (input.id) {
      const l = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
      if (l) return l;
    }
    const wrap = input.closest('label');
    if (wrap) return wrap;
    const sib = input.nextElementSibling;
    if (sib && sib.tagName === 'LABEL') return sib;
    const radio = input.closest('.radio, .checkbox');
    return radio ? radio.querySelector('label') : null;
  };
  // Options of a radio / checkbox group: clicks go to the visible label (Internshala hides the inputs).
  const optionsOf = (inputs, handle) => inputs.map((input, i) => {
    const label = labelOf(input);
    input.setAttribute('data-aa-input', `${handle}-${i}`);
    (label && visible(label) ? label : input).setAttribute('data-aa-option', `${handle}-${i}`);
    return clean(label ? label.innerText : '') || input.value;
  });
  const rows = [];

  root.querySelectorAll(S.chosen).forEach((c) => consume(c, 'aa-chosen'));

  const editor = root.querySelector(S.cover_letter_editor);
  if (editor) {
    const holder = editor.closest(S.cover_letter_holder) || editor.parentElement;
    const group = holder.closest(S.group) || holder.parentElement;
    const handle = mark(editor);
    const textarea = root.querySelector(S.cover_letter_textarea);
    if (textarea) mark(textarea);
    consume(holder, handle);
    consume(group, handle);
    const label = group.querySelector('label');
    rows.push({ kind: 'cover_letter', handle, tag: 'div', type: 'contenteditable', required: true, options: [],
                label: clean(label ? label.innerText : '') || 'Cover letter', value: clean(editor.innerText) });
  }

  const availability = root.querySelector(S.availability);
  if (availability) {
    const radios = Array.from(availability.querySelectorAll('input[type="radio"]'));
    if (radios.length) {
      const handle = `aa-${next++}`;
      const options = optionsOf(radios, handle);
      consume(availability, handle);
      const other = availability.querySelector(S.availability_other) || root.querySelector(S.availability_other);
      const heading = availability.querySelector(S.availability_heading);
      const checked = radios.findIndex((r) => r.checked);
      rows.push({ kind: 'availability', handle, tag: 'input', type: 'radio', required: true, options,
                  label: clean(heading ? heading.innerText : '') || 'Confirm your availability',
                  value: checked >= 0 ? options[checked] : '', checked, other_handle: other ? mark(other) : null });
    }
  }

  root.querySelectorAll(S.question).forEach((box) => {
    const labelEl = box.querySelector(S.question_label) || box.querySelector('label, legend');
    const rawLabel = labelEl ? labelEl.innerText : '';
    const label = clean(rawLabel) || clean(box.innerText).slice(0, 200);
    const required = !/\boptional\b/i.test(rawLabel);
    const handle = `aa-${next++}`;
    const radios = Array.from(box.querySelectorAll('input[type="radio"]'));
    const checks = Array.from(box.querySelectorAll('input[type="checkbox"]'));
    const select = box.querySelector('select');
    const number = box.querySelector('input[type="number"]');
    const text = box.querySelector('textarea, input[type="text"], input:not([type])');
    let row = null;
    if (radios.length) {
      const options = optionsOf(radios, handle);
      const checked = radios.findIndex((r) => r.checked);
      row = { handle, tag: 'input', type: 'radio', options, value: checked >= 0 ? options[checked] : '' };
    } else if (checks.length) {
      const options = optionsOf(checks, handle);
      row = { handle, tag: 'input', type: 'checkbox', options,
              value: checks.map((c, i) => c.checked ? options[i] : '').filter(Boolean).join(', ') };
    } else if (select) {
      const options = Array.from(select.options).filter((o) => o.value !== '' && clean(o.text)).map((o) => clean(o.text));
      const current = select.selectedIndex >= 0 && select.options[select.selectedIndex].value !== ''
        ? clean(select.options[select.selectedIndex].text) : '';
      row = { handle: mark(select), tag: 'select', type: 'select', options, value: current, hidden: !visible(select) };
    } else if (number) {
      row = { handle: mark(number), tag: 'input', type: 'number', options: [], value: number.value || '' };
    } else if (text) {
      row = { handle: mark(text), tag: text.tagName.toLowerCase(), type: text.tagName === 'TEXTAREA' ? 'textarea' : 'text',
              options: [], value: text.value || '', max_length: text.maxLength > 0 ? text.maxLength : null };
    }
    consume(box, handle);
    if (row) rows.push({ kind: 'question', label, required, ...row });
  });

  const relocation = root.querySelector(S.relocation);
  if (relocation && !relocation.closest(S.question)) {
    const handle = mark(relocation);
    const label = labelOf(relocation);
    relocation.setAttribute('data-aa-input', `${handle}-0`);
    (label && visible(label) ? label : relocation).setAttribute('data-aa-option', `${handle}-0`);
    if (!relocation.getAttribute('data-aa-group')) relocation.setAttribute('data-aa-group', handle);
    consumed.push(relocation.getAttribute('data-aa-group'));
    rows.push({ kind: 'relocation', handle, tag: 'input', type: 'checkbox', options: [],
                label: clean(label ? label.innerText : '') || 'Willing to relocate?',
                required: !!relocation.required, value: relocation.checked ? 'Yes' : '' });
  }
  window.__aaNext = next;
  return { rows, consumed };
}
"""

SET_EDITOR_JS = r"""
(args) => {
  const el = document.querySelector(`[data-aa-id="${args.handle}"]`);
  if (!el) return false;
  el.innerHTML = '';
  for (const para of args.text.split('\n')) {
    const p = document.createElement('p');
    if (para.trim()) p.textContent = para; else p.appendChild(document.createElement('br'));
    el.appendChild(p);
  }
  el.dispatchEvent(new Event('input', { bubbles: true }));
  const textarea = args.textarea && document.querySelector(args.textarea);
  if (textarea) {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(textarea, args.text);
    for (const type of ['input', 'change']) textarea.dispatchEvent(new Event(type, { bubbles: true }));
  }
  return true;
}
"""

SET_SELECT_JS = r"""
(args) => {
  const el = document.querySelector(`[data-aa-id="${args.handle}"]`);
  if (!el) return '';
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const option = Array.from(el.options).find((o) => clean(o.text) === args.choice);
  if (!option) return '';
  el.value = option.value;
  option.selected = true;
  el.dispatchEvent(new Event('change', { bubbles: true }));
  if (window.jQuery) { try { window.jQuery(el).trigger('chosen:updated'); } catch (e) { /* no chosen.js */ } }
  return el.selectedIndex >= 0 ? clean(el.options[el.selectedIndex].text) : '';
}
"""

IS_CHECKED_JS = "(sel) => { const el = document.querySelector(sel); return !!(el && el.checked); }"
FORCE_CHECK_JS = "(a) => { const el = document.querySelector(a.sel); if (el && el.checked !== a.on) el.click(); return !!el && el.checked === a.on; }"

_FIELD_KEYS = ("handle", "tag", "type", "label", "required", "options", "value", "max_length")
_TRUTHY = ("yes", "true", "1", "checked", "agree", "i agree", "y")


def _field(row: dict[str, Any]) -> FormField:
    return FormField(**{k: row[k] for k in _FIELD_KEYS if row.get(k) is not None})


def _selectors(value: str | tuple[str, ...]) -> tuple[str, ...]:
    return (value,) if isinstance(value, str) else tuple(value)


def _first_visible(page: Any, selectors: str | tuple[str, ...]) -> Any | None:
    for selector in _selectors(selectors):
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        except Exception:  # noqa: BLE001 - a selector the page can't evaluate
            continue
    return None


def _js_selectors() -> dict[str, str]:
    return {k: v for k, v in SELECTORS.items() if isinstance(v, str)}


class InternshalaSubmitter(BaseSubmitter):
    platform = "internshala"
    submit_selectors = tuple(SELECTORS["submit"])

    def __init__(self, session_factory: Any = None) -> None:
        super().__init__(session_factory)
        self._outcome: tuple[str, str | None] | None = None  # set when a listing can't be applied to here
        self._login_verified = False  # this run checked your dashboard: you're logged in

    # ------------------------------------------------------------------ lifecycle
    def session_kwargs(self, packet: CandidatePacket) -> dict[str, Any]:
        if not packet.internshala_session:
            raise SessionExpired(NOT_SYNCED)
        return browser_kwargs(packet.internshala_session, packet.internshala_user_agent)

    def _run(self, packet: CandidatePacket, submit: bool) -> SubmissionResult:
        self._outcome = None
        self._login_verified = False
        self.form_root = None
        try:
            result = super()._run(packet, submit)
        except SessionExpired as exc:  # nothing synced: no browser was opened
            return SubmissionResult(False, "failed", error=str(exc), session_expired=True)
        if self._outcome and not result.success:
            # "already_applied" (the orchestrator marks it applied) or "unavailable" (closed / external / profile)
            result.stage, result.final_url = self._outcome
        return result

    def _settle(self, page: Any) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001 - long-polling pages never go idle
            pass

    def _check_login(self, page: Any) -> None:
        """A login / sign-up page or prompt *may* mean Internshala doesn't see you as logged in. Logged-in
        students land on /registration/... pages too (e.g. to finish a profile) and pages can carry sign-up
        text, so before blaming the login the bot looks at your dashboard in the same browser."""
        on_login_path = _on_login_page(page.url)
        if not on_login_path and (self._login_verified or _first_visible(page, SELECTORS["login_modal"]) is None):
            return
        if not self._login_verified:
            if not self._still_logged_in(page):
                raise SessionExpired(EXPIRED)
            self._login_verified = True
        if on_login_path:
            path = urlparse(page.url).path
            self._stop("unavailable", f"Internshala opened {path} instead of the application form. Your login works: "
                       "open the posting to see what Internshala asks for (often: finish your Internshala profile).",
                       page.url)
        # Sign-up text on a page while you're logged in isn't about your login: carry on

    @staticmethod
    def _still_logged_in(page: Any) -> bool:
        """Open your Internshala dashboard in a new tab of the same browser: logged out, it sends you to /login."""
        origin = "{0.scheme}://{0.netloc}".format(urlparse(page.url))
        probe = page.context.new_page()
        try:
            probe.goto(f"{origin}{SELECTORS['dashboard_path']}", wait_until="domcontentloaded")
            return SELECTORS["dashboard_path"] in urlparse(probe.url).path and not _on_login_page(probe.url)
        except Exception:  # noqa: BLE001 - can't tell (network): don't blame the login
            return True
        finally:
            probe.close()

    def after_run(self, session: Any, result: SubmissionResult) -> None:
        """Internshala renews the login cookies as you browse (PHPSESSID, l, sessionToken): keep the renewed
        ones, as a browser would, so the next run doesn't start from a stale copy."""
        if result.session_expired:
            return
        renewed = extension_cookies(session.context.cookies())
        if has_login(renewed):
            result.session_cookies = renewed

    def _stop(self, outcome: str, message: str, url: str | None) -> None:
        self._outcome = (outcome, url)
        raise SubmissionError(message)

    # ------------------------------------------------------------------ open
    def open_application(self, page: Any, packet: CandidatePacket) -> None:
        page.goto(packet.application_url, wait_until="domcontentloaded")
        self._settle(page)
        self._check_login(page)
        human.human_scroll(page, 400)
        if _first_visible(page, SELECTORS["closed"]) is not None:
            self._stop("unavailable", "Applications are closed for this internship on Internshala", page.url)
        if _first_visible(page, SELECTORS["already_applied"]) is not None:
            self._stop("already_applied", ALREADY_APPLIED, page.url)
        button = self._apply_button(page)
        if button is None:
            raise SubmissionError("Could not find the Apply button on this Internshala page")
        human.human_click(page, button)
        self._reach_form(page)

    def _apply_button(self, page: Any) -> Any | None:
        for role in ("button", "link"):  # the visible text first, then Internshala's ids and classes
            try:
                loc = page.get_by_role(role, name=SELECTORS["apply_text"])
                for i in range(min(loc.count(), 5)):
                    if loc.nth(i).is_visible() and loc.nth(i).is_enabled():
                        return loc.nth(i)
            except Exception:  # noqa: BLE001
                continue
        return _first_visible(page, SELECTORS["apply_buttons"])

    def _find_form_root(self, page: Any) -> str | None:
        for root in SELECTORS["form_roots"]:
            if _first_visible(page, root) is None:
                continue
            if any(_first_visible(page, f"{root} {s}") is not None for s in SELECTORS["submit"]):
                return root
        return None

    def _reach_form(self, page: Any, timeout_s: float = 20.0) -> None:
        """After "Apply now": the modal, the resume interstitial, an external listing or a profile gate."""
        deadline = time.monotonic() + timeout_s
        gated_since: float | None = None
        while time.monotonic() < deadline:
            self._check_login(page)
            external = _first_visible(page, SELECTORS["external_link"])
            if external is not None:
                href = external.get_attribute("href") or ""
                close = _first_visible(page, SELECTORS["external_close"])
                if close is not None:
                    close.click()
                self._stop("unavailable", f"External application: apply at {href}" if href
                           else "External application: apply on the company's website", href or None)
            if _first_visible(page, SELECTORS["already_applied"]) is not None:
                self._stop("already_applied", ALREADY_APPLIED, page.url)
            root = self._find_form_root(page)
            if root is not None:
                self.form_root = root
                return
            proceed = _first_visible(page, SELECTORS["proceed"])
            if proceed is not None:  # /student/resume?detail_source=resume_intermediate
                human.human_click(page, proceed)
                self._settle(page)
                gated_since = None
                continue
            if any(p in urlparse(page.url).path for p in SELECTORS["profile_gate_paths"]):
                gated_since = gated_since or time.monotonic()
                if time.monotonic() - gated_since > 3:
                    self._stop("unavailable", PROFILE_GATE, page.url)
            page.wait_for_timeout(300)
        raise SubmissionError("The Internshala application form did not open")

    # ------------------------------------------------------------------ fill
    def fill(self, page: Any, packet: CandidatePacket) -> SubmissionResult:
        root = self.form_root or "body"
        human.human_scroll(page, 300)
        data = page.evaluate(EXTRACT_JS, {"root": root, "sel": _js_selectors()})
        consumed = set(data["consumed"])
        report: list[dict[str, Any]] = []
        answers: list[dict[str, Any]] = []
        questions: list[tuple[FormField, dict[str, Any]]] = []
        for row in data["rows"]:
            f = _field(row)
            if row["kind"] == "cover_letter":
                self._fill_cover_letter(page, packet, f, report, answers)
            elif row["kind"] == "availability":
                self._fill_availability(page, packet, f, row, report, answers)
            else:
                questions.append((f, row))
            human.short_pause()

        asked = []
        for f, row in questions:
            question = f.as_question()
            if row["kind"] == "relocation" and "relocat" not in normalize_text(f.label):
                question["question"] = f"Willing to relocate? ({f.label})"  # your saved answer applies
            asked.append(question)
        resolved = self.resolve(packet, asked)
        by_handle = {a.get("field_id"): a for a in resolved}
        for f, row in questions:
            ans = by_handle.get(f.handle)
            override = packet.override_for(f.label)  # your correction wins, even a blank one
            if override is not None and not override.strip():
                ok = self._clear_question(page, f, row)
                entry = self._report(f, "question", "", ok)
                entry["status"] = "skipped" if not f.required else "unmapped"
                report.append(entry)
                continue
            value = override if override is not None else (ans.get("answer") if ans else None)
            ok = self._fill_question(page, f, row, value) if value not in (None, "") else False
            if not ok and f.value and value in (None, ""):
                ok, value = True, f.value  # nothing to change: Internshala already has a value here
            entry = self._report(f, "question", value, ok)
            if ans:
                entry["confidence"] = ans.get("confidence")
                entry["needs_user_review"] = ans.get("needs_user_review")
            report.append(entry)
            human.short_pause()
        answers.extend(resolved)

        # Anything else on the form (rare) goes through the generic engine. Never the resume upload:
        # Internshala attaches your profile resume and the agent doesn't replace it.
        leftovers = [f for f in extract_fields(page, root) if f.handle not in consumed and f.type != "file"]
        if leftovers:
            more_report, more_answers = self._fill_pass(page, packet, leftovers)
            report.extend(more_report)
            answers.extend(more_answers)
        resume = FormField(handle="internshala-resume", tag="div", type="file", label="Resume")
        report.append(self._report(resume, "resume", "Your Internshala profile resume (Internshala attaches it)", True))
        return self._summarize(report, answers, True)

    def _fill_cover_letter(self, page: Any, packet: CandidatePacket, f: FormField,
                           report: list[dict[str, Any]], answers: list[dict[str, Any]]) -> None:
        text = internshala_cover_letter(packet.cover_letter_text)
        answer: dict[str, Any] | None = None
        if not text:  # cover letters turned off: answer "Why should you be hired?" like any other question
            resolved = self.resolve(packet, [{"question": f.label, "field_type": "text", "field_id": f.handle,
                                              "required": True, "max_length": COVER_LETTER_MAX}])
            answer = resolved[0] if resolved else None
            text = internshala_cover_letter(str(answer.get("answer") or "")) if answer else ""
        ok = bool(text) and self._set_editor(page, f, text)
        entry = self._report(f, "cover_letter", text, ok)
        if answer:
            entry["confidence"] = answer.get("confidence")
            entry["needs_user_review"] = answer.get("needs_user_review")
            answers.append(answer)
        report.append(entry)

    def _set_editor(self, page: Any, f: FormField, text: str) -> bool:
        editor = page.locator(f.selector).first
        try:
            try:
                human.human_click(page, editor)
            except Exception:  # noqa: BLE001 - an editor that can't take a click is still filled below
                pass
            page.evaluate(SET_EDITOR_JS, {"handle": f.handle, "text": text, "textarea": SELECTORS["cover_letter_textarea"]})
            # A real keystroke so Quill's own listeners run and clear "This field is required"
            editor.focus()
            page.keyboard.press("Control+End")
            page.keyboard.type(" ")
            page.keyboard.press("Backspace")
            return normalize_text(text[:40]) in normalize_text(editor.inner_text())
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not fill the Internshala cover letter: %s", exc)
            return False

    def _fill_availability(self, page: Any, packet: CandidatePacket, f: FormField, row: dict[str, Any],
                           report: list[dict[str, Any]], answers: list[dict[str, Any]]) -> None:
        """Keep "available immediately" unless your saved notice period (or your edit) says otherwise."""
        options = f.options
        stored = next((a for a in packet.answers if normalize_text(a.get("question") or "") == normalize_text(f.label)
                       and str(a.get("answer") or "").strip()), None)
        text, source = (str(stored["answer"]), stored.get("source") or "user") if stored else (None, "rule")
        if (packet.override_for(f.label) or "").strip():  # your correction in the review queue wins
            text, source = packet.override_for(f.label), "user"
        if text is None:
            saved = self.resolve(packet, [{"question": "Notice period / earliest start date", "field_type": "text",
                                           "field_id": "availability"}])
            if saved and saved[0].get("source") == "rule" and float(saved[0].get("confidence") or 0) >= 0.9:
                text = str(saved[0].get("answer") or "").strip() or None  # your saved answer, not the default
        if text is None:
            current = int(row.get("checked", -1))
            idx, other_text = (current if current >= 0 else availability_choice("immediately", options)[0]), None
        else:
            idx, other_text = availability_choice(text, options)
        ok = idx >= 0 and self._set_option(page, f.handle, idx)
        if ok and other_text is not None:
            ok = bool(row.get("other_handle")) and fill_field(
                page, FormField(handle=row["other_handle"], tag="textarea", type="textarea", label=f.label), other_text)
        value = other_text if other_text is not None else (options[idx] if idx >= 0 else text)
        report.append(self._report(f, "question", value, ok))
        answers.append({"question": f.label, "field_type": "radio", "answer": value or "", "options": options,
                        "confidence": 0.95, "needs_user_review": not ok, "source": source, "required": True,
                        "field_id": f.handle})

    def _fill_question(self, page: Any, f: FormField, row: dict[str, Any], value: Any) -> bool:
        text = str(value).strip()
        if f.type == "radio":
            choice = choose_option(text, f.options)
            return choice in f.options and self._set_option(page, f.handle, f.options.index(choice))
        if f.type == "checkbox" and f.options:
            done = False
            for wanted in [w.strip() for w in text.split(",") if w.strip()]:
                choice = choose_option(wanted, f.options)
                if choice in f.options:
                    done = self._set_option(page, f.handle, f.options.index(choice)) or done
            return done
        if f.type == "checkbox":  # a single checkbox (relocation)
            return self._set_option(page, f.handle, 0, on=normalize_text(text) in _TRUTHY)
        if f.type == "select":
            choice = choose_option(text, f.options) if f.options else text
            if f.options and choice not in f.options:
                return False  # never pick a different option just to fill the field
            if not row.get("hidden"):
                return fill_field(page, f, choice)
            selected = page.evaluate(SET_SELECT_JS, {"handle": f.handle, "choice": choice})  # hidden chosen.js select
            return normalize_text(selected) == normalize_text(choice)
        return fill_field(page, f, text)  # text, textarea, number (digits only)

    def _clear_question(self, page: Any, f: FormField, row: dict[str, Any]) -> bool:
        """You blanked this answer in the review queue: untick / empty it instead of sending ours."""
        if f.type in ("radio", "checkbox"):
            return all(self._set_option(page, f.handle, i, on=False) for i in range(max(1, len(f.options))))
        if f.type == "select":
            return True  # a dropdown can't be emptied; leave Internshala's default
        try:
            page.locator(f.selector).first.fill("")
            return True
        except Exception:  # noqa: BLE001
            return False

    def _set_option(self, page: Any, handle: str, idx: int, on: bool = True) -> bool:
        """Tick option ``idx`` the way a person does: click its label (the inputs are hidden)."""
        input_sel = f'[data-aa-input="{handle}-{idx}"]'
        if bool(page.evaluate(IS_CHECKED_JS, input_sel)) == on:
            return True
        target = page.locator(f'[data-aa-option="{handle}-{idx}"]').first
        try:
            if target.count() and target.is_visible():
                human.human_click(page, target)
        except Exception:  # noqa: BLE001
            pass
        if bool(page.evaluate(IS_CHECKED_JS, input_sel)) != on:
            page.evaluate(FORCE_CHECK_JS, {"sel": input_sel, "on": on})
        return bool(page.evaluate(IS_CHECKED_JS, input_sel)) == on

    # ------------------------------------------------------------------ submit
    def click_submit(self, page: Any) -> None:
        scope = f"{self.form_root} " if self.form_root else ""
        for selector in SELECTORS["submit"]:
            button = _first_visible(page, f"{scope}{selector}") or _first_visible(page, selector)
            if button is not None:
                human.human_click(page, button)
                return
        raise SubmissionError("Could not find Internshala's Submit button")

    def wait_for_confirmation(self, page: Any, baseline: dict[str, Any] | None = None,
                              timeout_ms: int = 20000) -> tuple[bool, str | None]:
        start_url = (baseline or {}).get("url") or ""
        waited = 0
        while waited <= timeout_ms:
            self._check_login(page)
            if _first_visible(page, SELECTORS["success"]) is not None:
                return True, None
            skip = _first_visible(page, SELECTORS["recommended_skip"])
            if skip is not None:  # the "recommended internships" popup only follows a sent application
                human.human_click(page, skip)
                return True, None
            if SELECTORS["form_page_path"] in start_url and SELECTORS["form_page_path"] not in page.url:
                return True, None
            if waited >= 1500 and _first_visible(page, SELECTORS["validation_error"]) is not None:
                return False, None
            page.wait_for_timeout(500)
            waited += 500
        return False, None
