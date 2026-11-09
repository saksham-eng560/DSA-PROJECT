"""Resume management: upload + parse master resume, edit, render PDFs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select, update

from app.api.deps import DB, CurrentUser, parse_uuid
from app.api.serializers import resume_out
from app.core.storage import get_storage, user_prefix
from app.models.resume import Resume
from app.schemas.resume import ResumeCreateFromText, ResumeUpdate
from app.schemas.resume_content import ResumeContent, normalize_resume
from app.services.embeddings import embed_text
from app.services.pdf_generator import TEMPLATES, render_resume_pdf
from app.services.resume_parser import ResumeParseError, extract_text, parse_resume_text

router = APIRouter(prefix="/resumes", tags=["resumes"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _embed(resume: Resume) -> None:
    rc = ResumeContent.model_validate(resume.parsed_content)
    resume.skills_embedding = embed_text(f"{rc.skills_text()}\n{rc.full_text()}")


def _make_master(db: DB, user_id: uuid.UUID, resume: Resume) -> None:
    db.execute(update(Resume).where(Resume.user_id == user_id, Resume.id != resume.id).values(is_master=False))
    resume.is_master = True


def _get_owned(db: DB, user_id: uuid.UUID, resume_id: str) -> Resume:
    resume = db.get(Resume, parse_uuid(resume_id))
    if resume is None or resume.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found")
    return resume


def _sync_profile(user, content: dict) -> None:  # type: ignore[no-untyped-def]
    info = content.get("personal_info") or {}
    if info.get("phone") and not user.phone:
        user.phone = info["phone"]
    if info.get("location") and not user.location:
        user.location = info["location"]
    if info.get("linkedin") and not user.linkedin_url:
        user.linkedin_url = info["linkedin"]


@router.post("/upload", status_code=201)
async def upload_resume(user: CurrentUser, db: DB, file: UploadFile = File(...), is_master: str | None = Form(None),
                        label: str | None = Form(None)) -> dict:
    # Read as text: some FastAPI / Starlette versions turn a missing ``bool = Form(True)`` field into False,
    # which silently uploaded every resume as a non-master copy. Missing or empty = make it your master resume.
    make_master = (is_master or "true").strip().lower() not in ("false", "0", "no", "off")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (max 10 MB)")
    try:
        text = extract_text(file.filename or "resume", data)
    except ResumeParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    # LLM parsing is blocking I/O; run it in a worker thread
    import anyio

    parsed, method = await anyio.to_thread.run_sync(parse_resume_text, text)
    if not parsed["personal_info"].get("email"):
        parsed["personal_info"]["email"] = user.email
    if not parsed["personal_info"].get("name"):
        parsed["personal_info"]["name"] = user.full_name
    ext = (file.filename or "resume.pdf").rsplit(".", 1)[-1].lower()[:5]
    key = get_storage().save(f"{user_prefix(user.id)}/uploads/{uuid.uuid4().hex}.{ext}", data, file.content_type)
    resume = Resume(
        user_id=user.id, label=label or f"Master resume ({file.filename})", original_file_url=key,
        original_filename=file.filename, raw_text=text, parsed_content=parsed, is_master=False,
    )
    db.add(resume)
    db.flush()
    _embed(resume)
    if make_master:
        _make_master(db, user.id, resume)
    _sync_profile(user, parsed)
    db.flush()
    return {**resume_out(resume), "parse_method": method}


@router.post("/from-text", status_code=201)
def create_from_text(body: ResumeCreateFromText, user: CurrentUser, db: DB) -> dict:
    parsed, method = parse_resume_text(body.text)
    if not parsed["personal_info"].get("email"):
        parsed["personal_info"]["email"] = user.email
    if not parsed["personal_info"].get("name"):
        parsed["personal_info"]["name"] = user.full_name
    resume = Resume(user_id=user.id, label=body.label or "Master resume", raw_text=body.text, parsed_content=parsed)
    db.add(resume)
    db.flush()
    _embed(resume)
    if body.is_master:
        _make_master(db, user.id, resume)
    _sync_profile(user, parsed)
    return {**resume_out(resume), "parse_method": method}


@router.get("")
def list_resumes(user: CurrentUser, db: DB, tailored: bool | None = None) -> dict:
    query = select(Resume).where(Resume.user_id == user.id, Resume.is_active.is_(True))
    if tailored is True:
        query = query.where(Resume.parent_resume_id.is_not(None))
    elif tailored is False:
        query = query.where(Resume.parent_resume_id.is_(None))
    resumes = db.scalars(query.order_by(Resume.is_master.desc(), Resume.created_at.desc()).limit(200)).all()
    return {"items": [resume_out(r, include_content=False) for r in resumes]}


@router.get("/master")
def get_master(user: CurrentUser, db: DB) -> dict:
    resume = db.scalar(select(Resume).where(Resume.user_id == user.id, Resume.is_master.is_(True)))
    if resume is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No master resume yet")
    return resume_out(resume)


@router.get("/{resume_id}")
def get_resume(resume_id: str, user: CurrentUser, db: DB) -> dict:
    return resume_out(_get_owned(db, user.id, resume_id))


@router.put("/{resume_id}")
def update_resume(resume_id: str, body: ResumeUpdate, user: CurrentUser, db: DB) -> dict:
    resume = _get_owned(db, user.id, resume_id)
    if body.parsed_content is not None:
        resume.parsed_content = normalize_resume(body.parsed_content)
        resume.version = (resume.version or 1) + 1
        resume.pdf_url = None
        _embed(resume)
    if body.label is not None:
        resume.label = body.label
    return resume_out(resume)


@router.post("/{resume_id}/set-master")
def set_master(resume_id: str, user: CurrentUser, db: DB) -> dict:
    resume = _get_owned(db, user.id, resume_id)
    _make_master(db, user.id, resume)
    return resume_out(resume)


@router.get("/{resume_id}/pdf")
def resume_pdf(resume_id: str, user: CurrentUser, db: DB, template: str = "classic", download: bool = False) -> Response:
    resume = _get_owned(db, user.id, resume_id)
    if template not in TEMPLATES:
        template = "classic"
    pdf = render_resume_pdf(resume.parsed_content, template=template)
    name = (resume.parsed_content.get("personal_info", {}).get("name") or "resume").replace(" ", "_")
    disposition = "attachment" if download else "inline"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'{disposition}; filename="{name}_Resume.pdf"'})


@router.delete("/{resume_id}")
def delete_resume(resume_id: str, user: CurrentUser, db: DB) -> dict:
    resume = _get_owned(db, user.id, resume_id)
    if resume.is_master:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Set another resume as master before deleting this one")
    resume.is_active = False
    return {"ok": True}
