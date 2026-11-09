"""Generic form-filling engine used by every platform submitter.

1. ``extract_fields`` inspects the live DOM and returns every fillable control with its best
   human-readable label, type, options and a stable ``data-aa-id`` handle.
2. ``classify_field`` maps a control to a profile value / resume upload / cover letter / question.
3. ``fill_field`` writes a value with the right interaction for the control type (text, select,
   radio, checkbox, file, React comboboxes), using human-like typing when enabled.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.automation.human import human_click, human_type, short_pause
from app.services.question_answerer import choose_option
from app.services.text_utils import clip, normalize_text

logger = logging.getLogger(__name__)

EXTRACT_JS = r"""
(rootSelector) => {
  const root = (rootSelector && document.querySelector(rootSelector)) || document;
  const clean = (t) => (t || '').replace(/\s+/g, ' ').replace(/\*/g, '').trim();
  const visible = (el) => {
    if (el.type === 'file') return true;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return (r.width > 0 && r.height > 0) || el.getAttribute('role') === 'combobox';
  };
  // Raw label text (asterisks kept, so "First name *" can mark a field as required).
  const rawLabel = (el) => {
    if (el.id) {
      const l = root.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && l.innerText.trim()) return l.innerText;
    }
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = by.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean).map(n => n.innerText).join(' ');
      if (t.trim()) return t;
    }
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const wrap = el.closest('label');
    if (wrap && wrap.innerText.trim()) return wrap.innerText;
    let node = el.parentElement;
    for (let i = 0; i < 5 && node; i++, node = node.parentElement) {
      const cand = node.querySelector('label, legend, .label, [class*="label"], [class*="question"], [data-automation-id*="formLabel"]');
      if (cand && !cand.contains(el) && cand.innerText.trim()) return cand.innerText;
    }
    return el.getAttribute('placeholder') || el.name || el.id || '';
  };
  const labelFor = (el) => clean(rawLabel(el));
  const groupLabel = (el) => {
    const fs = el.closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg && clean(lg.innerText)) return clean(lg.innerText); }
    let node = el.parentElement;
    for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
      const cand = node.querySelector('legend, .label, [class*="label"]:not(label), [class*="question"], [data-automation-id*="formLabel"]');
      if (cand && !cand.contains(el) && clean(cand.innerText)) return clean(cand.innerText);
    }
    return clean(el.name || '');
  };
  const isRequired = (el, label) => {
    if (el.required || el.getAttribute('aria-required') === 'true') return true;
    if (/\*|\(required\)/i.test(label || '')) return true;
    // A small wrapper around this one field (not the whole form) that shows an asterisk
    const box = el.closest('.field, .form-group, .form-field, .application-question, li, fieldset, [class*="field"], [class*="question"], div');
    const text = box ? (box.innerText || '') : '';
    return text.length < 400 && /\*/.test(text);
  };
  // Handles stay the same when the form is read again, so fields revealed later get fresh ones.
  let next = window.__aaNext || 0;
  const fields = [];
  const seenGroups = new Set();
  const els = root.querySelectorAll('input, select, textarea, [role="combobox"], [contenteditable="true"]');
  for (const el of els) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || (tag === 'select' ? 'select' : tag === 'textarea' ? 'textarea' : el.getAttribute('role') === 'combobox' ? 'combobox' : el.getAttribute('contenteditable') === 'true' ? 'contenteditable' : 'text')).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type)) continue;
    if (tag === 'input' && el.getAttribute('role') === 'combobox' && type !== 'text') continue;
    if (!visible(el) || el.disabled || el.readOnly && type !== 'file') continue;
    if (el.closest('[aria-hidden="true"]') && type !== 'file') continue;
    if (type === 'radio' || type === 'checkbox') {
      const key = el.name || el.id;
      const group = el.name ? Array.from(root.querySelectorAll(`input[type="${type}"][name="${CSS.escape(el.name)}"]`)) : [el];
      if (group.length > 1 || type === 'radio') {
        if (seenGroups.has(key)) continue;
        seenGroups.add(key);
        const handle = group[0].getAttribute('data-aa-group') || `aa-${next++}`;
        const options = group.map(o => { o.setAttribute('data-aa-group', handle); return labelFor(o) || o.value; });
        const legend = groupLabel(el);
        const fsText = (el.closest('fieldset') || {}).innerText || '';
        const required = group.some(o => o.required || o.getAttribute('aria-required') === 'true') || /\*/.test(legend) || (fsText.length < 400 && /\*/.test(fsText));
        const value = group.filter(o => o.checked).map(o => labelFor(o) || o.value).join(', ');
        fields.push({ handle, tag, type, name: el.name, id: el.id, label: legend, required, options, value, autocomplete: '' });
        continue;
      }
    }
    const handle = el.getAttribute('data-aa-id') || `aa-${next++}`;
    el.setAttribute('data-aa-id', handle);
    const raw = rawLabel(el);
    let options = [];
    if (tag === 'select') options = Array.from(el.options).filter(o => o.value !== '' || o.text.trim()).map(o => o.text.trim()).filter(t => t && !/^(select|choose|--|please select)/i.test(t));
    let value = type === 'file' ? '' : (el.value || '');
    if (tag === 'select' && el.selectedIndex >= 0 && el.options[el.selectedIndex] && el.options[el.selectedIndex].value === '') value = '';
    if (type === 'checkbox') value = el.checked ? 'Yes' : '';
    const maxLength = (tag === 'input' || tag === 'textarea') && el.maxLength > 0 ? el.maxLength : null;
    fields.push({
      handle, tag, type, name: el.name || '', id: el.id || '', label: clean(raw), required: isRequired(el, raw), options,
      value, autocomplete: el.getAttribute('autocomplete') || '',
      accept: el.getAttribute('accept') || '', multiple: !!el.multiple, max_length: maxLength,
      placeholder: el.getAttribute('placeholder') || '',
    });
  }
  window.__aaNext = next;
  return fields;
}
"""


@dataclass
class FormField:
    handle: str
    tag: str
    type: str
    label: str
    name: str = ""
    id: str = ""
    required: bool = False
    options: list[str] = field(default_factory=list)
    value: str = ""
    autocomplete: str = ""
    accept: str = ""
    multiple: bool = False
    max_length: int | None = None
    placeholder: str = ""

    @property
    def selector(self) -> str:
        if self.type in ("radio", "checkbox") and self.options:
            return f'[data-aa-group="{self.handle}"]'
        return f'[data-aa-id="{self.handle}"]'

    @property
    def descriptor(self) -> str:
        return normalize_text(f"{self.label} {self.name} {self.id} {self.autocomplete}")

    def as_question(self) -> dict[str, Any]:
        field_type = {"select": "select", "radio": "radio", "checkbox": "checkbox", "number": "number",
                      "combobox": "select"}.get(self.type, "text")
        question = {"question": self.label or self.name, "field_type": field_type, "options": self.options,
                    "required": self.required, "field_id": self.handle}
        if self.type in ("date", "month"):
            question["field_type"] = "date"
        if self.max_length:
            question["max_length"] = self.max_length
        return question


def extract_fields(page: Any, root_selector: str | None = None) -> list[FormField]:
    raw = page.evaluate(EXTRACT_JS, root_selector)
    fields = []
    for item in raw:
        fields.append(FormField(**{k: item.get(k) for k in FormField.__dataclass_fields__ if k in item}))
    return fields


# --------------------------------------------------------------------------- classification
PROFILE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("resume", re.compile(r"resume|\bcv\b|curriculum")),
    ("cover_letter", re.compile(r"cover.?letter|motivation letter")),
    ("first_name", re.compile(r"first.?name|given.?name|\bfname\b|given-name|legalnamesection_firstname|preferred first")),
    ("last_name", re.compile(r"last.?name|family.?name|surname|\blname\b|family-name|legalnamesection_lastname")),
    ("full_name", re.compile(r"^(your )?(full |legal )?name\b|^name$|full.?name|\bname$")),
    ("email", re.compile(r"e-?mail")),
    ("phone", re.compile(r"phone|mobile|\btel\b|cell")),
    ("linkedin", re.compile(r"linked.?in")),
    ("github", re.compile(r"github")),
    ("portfolio", re.compile(r"portfolio|personal (web)?site|website|\burl\b|urls\[other\]")),
    ("current_company", re.compile(r"current (company|employer)|^org$|\borg\b|company name|most recent (company|employer)")),
    ("current_title", re.compile(r"current (job )?title|current role|job title")),
    ("location", re.compile(r"^location|current location|candidate-location|city,? state|where are you (located|based)")),
]


def classify_field(f: FormField) -> str | None:
    desc = f.descriptor
    if f.type == "file":
        if re.search(r"cover", desc):
            return "cover_letter"
        return "resume"  # any other file upload on an application form is the resume/CV
    if f.type in ("radio", "checkbox", "select"):
        return None  # "Do you have a LinkedIn profile?" is a question, not the place for the URL
    for key, pattern in PROFILE_PATTERNS:
        if key in ("resume",) and f.type != "file":
            continue
        if pattern.search(desc):
            if key == "cover_letter" and f.type not in ("textarea", "file", "text", "contenteditable"):
                continue
            if key == "full_name" and re.search(r"company|school|university|reference|emergency|manager|recruiter", desc):
                continue
            if key in ("email", "phone") and re.search(r"reference|emergency", desc):
                continue
            return key
    if f.autocomplete in ("given-name", "family-name", "email", "tel", "name"):
        return {"given-name": "first_name", "family-name": "last_name", "email": "email", "tel": "phone", "name": "full_name"}[f.autocomplete]
    return None


# --------------------------------------------------------------------------- values
_DATE_HINT = re.compile(r"date|\bdob\b|birth|start|graduat|available|joining|from|until|\bto\b", re.I)


def parse_when(text: str, today: date | None = None, day_first: bool = True) -> date | None:
    """'2027-06-01', 'June 2027', '15/06/2027', 'immediately', '2 weeks' -> a date.

    An ambiguous '01/02/2004' is read day first (1 Feb) unless ``day_first`` is False (US forms)."""
    today = today or datetime.now(UTC).date()
    t = normalize_text(text)
    if not t:
        return None
    if re.search(r"immediate|asap|right away|\bnow\b|\btoday\b", t):
        return today
    m = re.search(r"(\d+)\s*(day|week|month)", t)
    if m and not re.search(r"\d{4}", t):
        return today + timedelta(days=int(m.group(1)) * {"d": 1, "w": 7, "m": 30}[m.group(2)[0]])
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", t)
    if m:  # 15/06/2027 can only be day first; 06/15/2027 only month first; otherwise follow day_first
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        day, month = (a, b) if a > 12 or (day_first and b <= 12) else (b, a)
        try:
            return date(year, month, day)
        except ValueError:
            return None
    try:
        from dateutil import parser as dateparser

        return dateparser.parse(text, default=datetime(today.year, today.month, 1)).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _format_like(d: date, placeholder: str) -> str | None:
    p = placeholder.lower()
    sep = next((c for c in "/.-" if c in p), "/")
    if re.search(r"dd\W?mm\W?yyyy", p):
        return d.strftime(f"%d{sep}%m{sep}%Y")
    if re.search(r"mm\W?dd\W?yyyy", p):
        return d.strftime(f"%m{sep}%d{sep}%Y")
    if re.search(r"yyyy\W?mm\W?dd", p):
        return d.strftime(f"%Y{sep}%m{sep}%d")
    if re.search(r"mm\W?yyyy", p):
        return d.strftime(f"%m{sep}%Y")
    return None


def coerce_value(f: FormField, value: Any) -> str | None:
    """Shape a value for the control: digits for number inputs, ISO dates for date inputs,
    the placeholder's date format for text date fields, and the field's length limit."""
    text = str(value).strip()
    if f.type == "number":
        m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
        return m.group(0) if m else None
    if f.type in ("date", "month", "datetime-local"):
        d = parse_when(text)
        if d is None:
            return None
        return {"month": d.strftime("%Y-%m"), "datetime-local": f"{d.isoformat()}T09:00"}.get(f.type, d.isoformat())
    if f.type in ("text", "textarea") and f.placeholder and _DATE_HINT.search(f.label or f.name):
        month_first = bool(re.search(r"mm\W?dd\W?yyyy", f.placeholder.lower()))
        already = re.fullmatch(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{4}", text) and re.search(r"(dd|mm)\W?(dd|mm)\W?yyyy",
                                                                                     f.placeholder.lower())
        if not already:  # a date already written the way the box asks for is typed as is
            d = parse_when(text, day_first=not month_first) if re.search(r"\d|immediate|asap|week|month|day", text, re.I) else None
            formatted = _format_like(d, f.placeholder) if d else None
            if formatted:
                text = formatted
    if f.max_length and len(text) > f.max_length:
        text = clip(text, f.max_length)
    return text


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def value_stuck(f: FormField, actual: str, wanted: str) -> bool:
    """Did the page keep what we typed? (Masked / reformatted inputs count as kept.)"""
    if f.type == "tel" or re.search(r"phone|mobile", f.descriptor):
        want = _digits(wanted)
        return bool(want) and want[-7:] in _digits(actual)
    a, w = normalize_text(actual), normalize_text(wanted)
    if not a:
        return False
    if f.type == "number":
        try:
            return float(a) == float(w)
        except ValueError:
            return a == w
    return a == w or a.startswith(w[:40]) or w.startswith(a[:40])


_SET_VALUE_JS = """(el, v) => {
  const proto = Object.getPrototypeOf(el);
  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
  if (desc && desc.set) desc.set.call(el, v); else el.value = v;
  for (const type of ['input', 'change', 'blur']) el.dispatchEvent(new Event(type, { bubbles: true }));
}"""


def _input_value(loc: Any) -> str:
    try:
        return loc.input_value() or ""
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- filling
def fill_field(page: Any, f: FormField, value: Any) -> bool:
    """Fill one control and check that the page kept the value. Returns True when it did."""
    if value is None or value == "":
        return False
    try:
        if f.type == "file":
            page.locator(f.selector).first.set_input_files(value)
            return True
        if f.type == "select":
            return _fill_select(page, f, str(value))
        if f.type == "radio":
            choice = choose_option(str(value), f.options)
            if choice not in f.options:
                return False
            option = page.locator(f.selector).nth(f.options.index(choice))
            try:
                option.check(force=True)
            except Exception:  # noqa: BLE001
                human_click(page, option)
            return _checked(option)
        if f.type == "checkbox":
            if f.options and len(f.options) > 1:
                wanted = [v.strip() for v in str(value).split(",")] if isinstance(value, str) else list(value)
                done = False
                for w in wanted:
                    choice = choose_option(w, f.options)
                    if choice in f.options:
                        page.locator(f.selector).nth(f.options.index(choice)).check(force=True)
                        done = True
                return done
            truthy = normalize_text(str(value)) in ("yes", "true", "1", "checked", "agree", "i agree", "y")
            loc = page.locator(f.selector).first
            if truthy:
                loc.check(force=True)
            else:
                loc.uncheck(force=True)
            return _checked(loc) == truthy
        if f.type == "combobox" or (f.tag == "input" and f.type == "text" and _is_combobox(page, f)):
            return _fill_combobox(page, f, str(value))
        text = coerce_value(f, value)
        if not text:
            return False
        loc = page.locator(f.selector).first
        if f.type == "contenteditable":
            loc.click()
            page.keyboard.press("Control+A")
            page.keyboard.type(text)
            return normalize_text(text[:30]) in normalize_text(loc.inner_text())
        if f.type in ("date", "month", "datetime-local", "number", "range", "color"):
            loc.fill(text)  # typing into these is locale-dependent; fill() sets them exactly
        else:
            human_type(page, loc, text)
        if value_stuck(f, _input_value(loc), text):
            return True
        # Controlled (React / Vue) inputs sometimes drop typed text: set it the way they listen for.
        try:
            loc.fill(text)
        except Exception:  # noqa: BLE001
            pass
        if not value_stuck(f, _input_value(loc), text):
            loc.evaluate(_SET_VALUE_JS, text)
        return value_stuck(f, _input_value(loc), text)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to fill %s (%s): %s", f.label, f.selector, exc)
        return False


def _checked(loc: Any) -> bool:
    try:
        return bool(loc.is_checked())
    except Exception:  # noqa: BLE001
        return False


def _fill_select(page: Any, f: FormField, value: str) -> bool:
    loc = page.locator(f.selector).first
    choice = choose_option(value, f.options) if f.options else value
    if f.options and choice not in f.options:
        return False  # never pick a different option just to fill the field
    try:
        loc.select_option(label=choice)
    except Exception:  # noqa: BLE001
        loc.select_option(value=choice)
    selected = loc.evaluate("el => el.selectedIndex >= 0 ? el.options[el.selectedIndex].text.trim() : ''")
    return normalize_text(selected) == normalize_text(choice) or (not f.options and bool(selected))


def _is_combobox(page: Any, f: FormField) -> bool:
    try:
        return page.locator(f.selector).first.get_attribute("role") == "combobox"
    except Exception:  # noqa: BLE001
        return False


_OPTION_SELECTOR = '[role="option"], [class*="select__option"], [data-automation-id="promptOption"]'


def _combobox_options(page: Any) -> list[str]:
    options = page.locator(_OPTION_SELECTOR)
    try:
        count = options.count()
        return [options.nth(i).inner_text().strip() for i in range(min(count, 50))]
    except Exception:  # noqa: BLE001
        return []


def _fill_combobox(page: Any, f: FormField, value: str) -> bool:
    """React-select / ARIA comboboxes (Greenhouse, Workday, location autocompletes): type, then pick the
    matching suggestion. If nothing matches, try a shorter search; if still nothing, leave it for you."""
    loc = page.locator(f.selector).first
    loc.click()
    short_pause()
    words = re.findall(r"[\w'+#.]+", value)
    searches = [value[:40]]
    if len(words) > 1:
        searches.append(words[0] if len(words[0]) >= 3 else " ".join(words[:2]))
    suggested = False
    for query in searches:
        _type_into(page, loc, query)
        texts = _combobox_options(page)
        if not texts:
            continue
        suggested = True
        choice = choose_option(value, texts)
        if choice not in texts and _shares_word(value, texts[0]):
            choice = texts[0]  # autocomplete: the top suggestion for what we typed
        if choice in texts:
            page.locator(_OPTION_SELECTOR).nth(texts.index(choice)).click()
            return True
    if not suggested:  # a free-text combobox: keep what we typed
        _type_into(page, loc, value[:40])
        page.keyboard.press("Enter")
        return bool(_input_value(loc).strip()) or loc.get_attribute("aria-expanded") != "true"
    try:  # suggestions, but none of them is right: leave it for you rather than pick a wrong one
        page.keyboard.press("Escape")
        loc.fill("")
    except Exception:  # noqa: BLE001
        pass
    return False


def _type_into(page: Any, loc: Any, text: str) -> None:
    try:
        loc.fill("")
        loc.type(text, delay=40)
    except Exception:  # noqa: BLE001
        page.keyboard.type(text, delay=40)
    page.wait_for_timeout(700)


def _shares_word(a: str, b: str) -> bool:
    words = lambda t: {w for w in re.findall(r"[a-z0-9]+", normalize_text(t)) if len(w) >= 3}  # noqa: E731
    return bool(words(a) & words(b))


def visible_errors(page: Any) -> list[str]:
    """Collect validation error messages currently shown on the page."""
    try:
        return page.evaluate(
            r"""() => Array.from(document.querySelectorAll('[aria-invalid="true"], .error, .field-error, [class*="error-message"], [role="alert"], [data-automation-id="errorMessage"]'))
                  .map(e => (e.innerText || e.getAttribute('aria-label') || e.name || '').trim())
                  .filter(t => t && t.length < 300).slice(0, 10)"""
        )
    except Exception:  # noqa: BLE001
        return []


CONFIRMATION_PATTERN = re.compile(
    r"thank(s| you) for (applying|your application|your interest)|application (has been |was )?(submitted|received|sent|complete)"
    r"|we('|’)ve received your application|your application was sent|successfully (submitted|applied)|application submitted",
    re.IGNORECASE,
)


def looks_submitted(page: Any) -> tuple[bool, str | None]:
    try:
        url = page.url.lower()
        text = page.inner_text("body")[:20000]
    except Exception:  # noqa: BLE001
        return False, None
    m = CONFIRMATION_PATTERN.search(text)
    if m or re.search(r"confirm|thank|success|submitted", url):
        num = re.search(r"(?:confirmation|reference|application)\s*(?:number|#|id)[:\s#]*([A-Z0-9-]{4,})", text, re.I)
        return True, num.group(1) if num else None
    return False, None
