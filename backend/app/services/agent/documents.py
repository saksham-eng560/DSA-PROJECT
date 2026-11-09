"""The files an application sends and keeps: which resume goes out, its rendered PDF, and the screenshots taken
at every critical step (rule #5)."""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.storage import get_storage, user_prefix
from app.models.resume import Resume
from app.models.user import User
from app.services.pdf_generator import render_resume_pdf

RESUME_STRATEGIES = ("original", "light", "full")


def resume_strategy(user: User, master: Resume) -> str:
    """original: send your uploaded file untouched · light: reorder only · full: AI rewrite (truth-guarded).

    A resume pasted as text has no original file, so it falls back to light tweaks.
    """
    strategy = user.prefs.get("resume_strategy") or "original"
    if strategy not in RESUME_STRATEGIES:
        strategy = "original"
    if strategy == "original" and not master.original_file_url:
        return "light"
    return strategy


def store_file(user_id: uuid.UUID, folder: str, data: bytes, ext: str, content_type: str) -> str:
    key = f"{user_prefix(user_id)}/{folder}/{uuid.uuid4().hex}.{ext}"
    return get_storage().save(key, data, content_type)


def store_screenshot(user_id: uuid.UUID, png: bytes) -> str:
    return store_file(user_id, "screenshots", png, "png", "image/png")


def render_tailored_pdf(user: User, resume: Resume) -> str:
    pdf = render_resume_pdf(resume.parsed_content, template=(user.prefs.get("resume_template") or "classic"))
    key = store_file(user.id, "resumes", pdf, "pdf", "application/pdf")
    resume.pdf_url = key
    return key


def resume_filename(user: User, ext: str = ".pdf") -> str:
    safe = "".join(c for c in (user.full_name or "Resume") if c.isalnum() or c in " -_").strip().replace(" ", "_")
    return f"{safe or 'Resume'}_Resume{ext}"


@contextmanager
def temp_file(data: bytes, suffix: str, name: str | None = None) -> Iterator[str]:
    """``data`` as a file on disk (for a browser's upload field), removed afterwards."""
    directory = tempfile.mkdtemp(prefix="autoapply-")
    path = os.path.join(directory, name or f"file{suffix}")
    with open(path, "wb") as fh:
        fh.write(data)
    try:
        yield path
    finally:
        try:
            os.remove(path)
            os.rmdir(directory)
        except OSError:
            pass
