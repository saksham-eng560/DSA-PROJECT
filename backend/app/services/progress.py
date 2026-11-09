"""Progress digest: every application you've made, where it stands, and what to do next.

Sent once a day (or week) at about 20:00 in your time zone to your Gmail, the dashboard and any
Discord / Slack webhook: what changed since the last digest, where everything stands, applications
still waiting for a reply after a week (a good moment for a polite follow-up) and upcoming interviews.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.application import Application, ApplicationStatusHistory
from app.models.enums import STATUS_RANK, ApplicationStatus
from app.models.interview import Interview
from app.models.user import Notification, User
from app.services.notifier import notify

logger = logging.getLogger(__name__)

DIGEST_HOUR = 20      # local time
FOLLOW_UP_DAYS = 7    # no reply after this many days -> suggest a follow-up
WAITING = (ApplicationStatus.APPLIED, ApplicationStatus.ACKNOWLEDGED)
GROUPS = [
    ("Waiting for a reply", {ApplicationStatus.APPLIED, ApplicationStatus.ACKNOWLEDGED}),
    ("In process", {ApplicationStatus.SCREENING, ApplicationStatus.ASSESSMENT, ApplicationStatus.INTERVIEW,
                    ApplicationStatus.FINAL_ROUND}),
    ("Offers", {ApplicationStatus.OFFER, ApplicationStatus.ACCEPTED}),
    ("Closed", {ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN}),
]


def _tz(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(user.prefs.get("timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _label(status: ApplicationStatus | None) -> str:
    return (status.value if status else "—").replace("_", " ")


def tracked_applications(db: Session, user: User) -> list[Application]:
    """Everything you've applied to (by the agent or on your own), newest first."""
    applied_rank = STATUS_RANK[ApplicationStatus.APPLIED]
    statuses = [s for s, rank in STATUS_RANK.items() if rank >= applied_rank and s != ApplicationStatus.FAILED]
    return list(db.scalars(select(Application).where(Application.user_id == user.id, Application.status.in_(statuses))
                           .order_by(Application.submitted_at.desc().nulls_last())).all())


def build_digest(db: Session, user: User, days: int = 1, now: datetime | None = None) -> tuple[str, str, int]:
    """Return (title, body, number of tracked applications)."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    apps = tracked_applications(db, user)
    if not apps:
        return "", "", 0
    by_id = {a.id: a for a in apps}
    changes = db.scalars(
        select(ApplicationStatusHistory)
        .where(ApplicationStatusHistory.application_id.in_(list(by_id)), ApplicationStatusHistory.created_at >= since)
        .order_by(ApplicationStatusHistory.created_at)
    ).all()
    counts = Counter(a.status for a in apps)
    period = "today" if days == 1 else f"in the last {days} days"
    local = now.astimezone(_tz(user))

    lines = [f"You're tracking {len(apps)} application{'s' if len(apps) != 1 else ''}. Here's where things stand "
             f"on {local.strftime('%a %d %b')}.", ""]
    lines.append(f"What changed {period}:")
    if changes:
        for h in changes[-15:]:
            app = by_id[h.application_id]
            who = " (you applied yourself)" if h.changed_by == "user" and h.new_status == ApplicationStatus.APPLIED else ""
            lines.append(f"  • {app.job.company_name} — {app.job.role_title}: {_label(h.old_status)} → {_label(h.new_status)}{who}")
    else:
        lines.append("  • No new replies yet. The agent keeps watching your inbox.")
    lines += ["", "Where everything stands:"]
    for name, statuses in GROUPS:
        n = sum(counts.get(s, 0) for s in statuses)
        if n:
            lines.append(f"  • {name}: {n}")

    stale = [a for a in apps if a.status in WAITING and a.submitted_at
             and a.submitted_at <= now - timedelta(days=FOLLOW_UP_DAYS)]
    if stale:
        lines += ["", f"No reply after {FOLLOW_UP_DAYS}+ days (a short, polite follow-up often helps):"]
        for a in stale[:10]:
            waited = (now - a.submitted_at).days
            lines.append(f"  • {a.job.company_name} — {a.job.role_title} (applied {waited} days ago)")

    upcoming = db.scalars(
        select(Interview).where(Interview.application_id.in_(list(by_id)), Interview.scheduled_at > now,
                                Interview.scheduled_at < now + timedelta(days=7)).order_by(Interview.scheduled_at)
    ).all()
    if upcoming:
        lines += ["", "Interviews this week:"]
        for i in upcoming:
            app = by_id[i.application_id]
            when = i.scheduled_at.astimezone(_tz(user)).strftime("%a %d %b, %H:%M")
            lines.append(f"  • {when} — {app.job.company_name} ({app.job.role_title})")

    moved = len({h.application_id for h in changes})
    title = (f"Progress: {moved} application{'s' if moved != 1 else ''} moved {period}" if moved
             else f"Progress: {len(apps)} applications tracked, no changes {period}")
    return title, "\n".join(lines), len(apps)


def send_due_digests(now: datetime | None = None, force: bool = False, db: Session | None = None) -> int:
    """Hourly: send each user's digest when it's DIGEST_HOUR in their time zone (daily, or Sundays if weekly)."""
    from app.core.database import session_scope

    now = now or datetime.now(UTC)
    sent = 0

    def run(session: Session) -> int:
        count = 0
        for user in session.scalars(select(User).where(User.is_active.is_(True))).all():
            mode = user.prefs.get("progress_digest") or "daily"
            if mode == "off":
                continue
            local = now.astimezone(_tz(user))
            if not force:
                if local.hour != DIGEST_HOUR or (mode == "weekly" and local.weekday() != 6):
                    continue
                already = session.scalar(select(Notification.id).where(
                    Notification.user_id == user.id, Notification.event_type == "progress_digest",
                    Notification.created_at >= min(now, datetime.now(UTC)) - timedelta(hours=20)).limit(1))
                if already:
                    continue
            title, body, tracked = build_digest(session, user, days=7 if mode == "weekly" else 1, now=now)
            if not tracked:
                continue
            notify(session, user, "progress_digest", title, body, link="/dashboard/applied")
            count += 1
        return count

    if db is not None:
        return run(db)
    with session_scope() as session:
        sent = run(session)
    return sent


def send_now(db: Session, user: User, days: int = 7) -> bool:
    """On demand ("Email me a progress report"): the same digest, right away."""
    title, body, tracked = build_digest(db, user, days=days)
    if not tracked:
        return False
    notify(db, user, "progress_digest", title, body, link="/dashboard/applied")
    return True
