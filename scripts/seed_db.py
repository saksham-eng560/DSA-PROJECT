#!/usr/bin/env python3
"""Seed a demo account with realistic data so every dashboard view has something to show.

    python scripts/seed_db.py                      # demo@example.com / demo-password-123
    python scripts/seed_db.py --email me@x.com --password secret123 --reset
"""

from __future__ import annotations

import argparse
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
# Repo checkout: <root>/backend/app ; Docker image: /app/app
sys.path.insert(0, str(_ROOT / "backend" if (_ROOT / "backend" / "app").is_dir() else _ROOT))

from sqlalchemy import select

from app.config import settings
from app.core.database import create_all, session_scope
from app.core.security import hash_password
from app.models import (
    AgentRun,
    Application,
    ApplicationStatus,
    ApplicationStatusHistory,
    ATSPlatform,
    Communication,
    EmailDirection,
    EmailIntent,
    Interview,
    InterviewType,
    Job,
    JobType,
    Resume,
    User,
    UserFieldMapping,
)
from app.models.user import default_preferences
from app.services.embeddings import embed_text
from app.services.job_matcher import heuristic_evaluation
from app.services.privacy import delete_user_data
from app.services.resume_parser import heuristic_parse
from app.services.text_utils import dedupe_key, extract_skills

RESUME = """ALEX RIVERA
alex.rivera@example.com | +1 (555) 010-2030 | linkedin.com/in/alexrivera | github.com/alexrivera
Austin, TX

SUMMARY
Software engineer with 5 years of experience building Python and TypeScript services, data pipelines and developer tooling.

EXPERIENCE
Senior Software Engineer | Northwind Analytics | Austin, TX   Mar 2022 - Present
• Designed REST APIs in Python and FastAPI handling 5M requests/day with 99.95% uptime
• Led migration from MySQL to PostgreSQL, cutting query latency by 35%
• Built event pipelines with Kafka and Airflow feeding the analytics warehouse
Software Engineer | Contoso Cloud | Remote   Jun 2019 - Feb 2022
• Shipped React and TypeScript dashboards used by 20,000 customers
• Automated deployments with Docker, Kubernetes and GitHub Actions

EDUCATION
University of Texas at Austin | B.S. Computer Science   2015 - 2019

PROJECTS
OpenQueue | Python, Redis
• Open-source task queue with 1.2k GitHub stars

SKILLS
Python, TypeScript, SQL, FastAPI, React, PostgreSQL, Redis, Kafka, Airflow, Docker, Kubernetes, AWS, Git
"""

JOBS = [
    ("Stripe", "Backend Engineer, Payments", "San Francisco, CA", ATSPlatform.GREENHOUSE, "Python, PostgreSQL, Kafka and distributed systems. 4+ years.", (180000, 240000)),
    ("Airbnb", "Senior Software Engineer, Data Platform", "Remote", ATSPlatform.GREENHOUSE, "Airflow, Kafka, Spark, Python. 5+ years building data pipelines.", (190000, 250000)),
    ("Netflix", "Software Engineer, Developer Productivity", "Los Gatos, CA", ATSPlatform.LEVER, "Kubernetes, Docker, CI/CD, Python or Go.", (200000, 300000)),
    ("Figma", "Full-Stack Engineer", "New York, NY", ATSPlatform.ASHBY, "React, TypeScript, Node.js, PostgreSQL.", (170000, 220000)),
    ("NVIDIA", "Software Engineer - Cloud Infrastructure", "Santa Clara, CA", ATSPlatform.WORKDAY, "AWS, Kubernetes, Terraform, Python.", (160000, 230000)),
    ("Globex", "Platform Engineer", "Austin, TX", ATSPlatform.LINKEDIN, "Docker, Kubernetes, AWS, observability with Grafana.", (150000, 190000)),
    ("Initech", "Python Developer", "Remote", ATSPlatform.INDEED, "Python, Django, PostgreSQL, REST APIs.", (130000, 160000)),
    ("Hooli", "Senior iOS Engineer", "Palo Alto, CA", ATSPlatform.GLASSDOOR, "Swift, SwiftUI, Objective-C. 7+ years.", (190000, 240000)),
    ("Pied Piper", "Founding Backend Engineer", "San Francisco, CA", ATSPlatform.WELLFOUND, "Python, FastAPI, Postgres at an early-stage startup.", (160000, 200000)),
    ("Umbrella", "Data Engineer", "Remote", ATSPlatform.GREENHOUSE, "Airflow, dbt, Snowflake, SQL, Python.", (150000, 185000)),
]

# Swipe Review deck: internships that passed the filters and wait for a keep / skip
DECK = [
    ("Figma", "Software Engineer Intern", "San Francisco, CA; New York, NY", ATSPlatform.GREENHOUSE, "Offers Sponsorship"),
    ("Ramp", "Software Engineering Intern, Backend", "New York, NY", ATSPlatform.ASHBY, "Offers Sponsorship"),
    ("Notion", "Software Engineer Intern", "San Francisco, CA", ATSPlatform.ASHBY, None),
    ("Anthropic", "Research Engineer Intern", "San Francisco, CA", ATSPlatform.GREENHOUSE, "Offers Sponsorship"),
    ("Vercel", "Software Engineer Intern, Frontend", "Remote in USA", ATSPlatform.GREENHOUSE, None),
    ("Scale AI", "Machine Learning Engineer Intern", "San Francisco, CA", ATSPlatform.GREENHOUSE, "Does Not Offer Sponsorship"),
    ("Replit", "Full Stack Engineer Intern", "Foster City, CA", ATSPlatform.ASHBY, None),
    ("Perplexity", "AI Engineer Intern", "San Francisco, CA", ATSPlatform.ASHBY, "Offers Sponsorship"),
    ("Palantir", "Forward Deployed Software Engineer Intern", "New York, NY", ATSPlatform.LEVER, "U.S. Citizenship is Required"),
    ("Cloudflare", "Software Engineer Intern, Workers", "Austin, TX", ATSPlatform.GREENHOUSE, None),
    ("Modal", "Infrastructure Engineer Intern", "New York, NY", ATSPlatform.ASHBY, "Offers Sponsorship"),
    ("Robinhood", "Backend Software Engineer Intern", "Menlo Park, CA", ATSPlatform.GREENHOUSE, None),
    ("Decagon", "Software Engineer Intern", "San Francisco, CA", ATSPlatform.ASHBY, None),
    ("Zoox", "Software Engineer Intern, Simulation", "Foster City, CA", ATSPlatform.LEVER, "Does Not Offer Sponsorship"),
]
DECK_STACK = ["Python", "TypeScript", "React", "PostgreSQL", "Kubernetes", "Go", "Rust", "PyTorch", "AWS", "Kafka", "SQL", "Docker"]

STATUS_PLAN = [
    ApplicationStatus.PENDING_APPROVAL, ApplicationStatus.PENDING_APPROVAL, ApplicationStatus.INTERVIEW,
    ApplicationStatus.SCREENING, ApplicationStatus.APPLIED, ApplicationStatus.ACKNOWLEDGED, ApplicationStatus.REJECTED,
    ApplicationStatus.SKIPPED, ApplicationStatus.OFFER, ApplicationStatus.APPLIED,
]
PIPELINE = [ApplicationStatus.MATCHED, ApplicationStatus.PREPARING, ApplicationStatus.PENDING_APPROVAL,
            ApplicationStatus.APPROVED, ApplicationStatus.APPLIED]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--password", default="demo-password-123")
    parser.add_argument("--reset", action="store_true", help="delete the account first if it exists")
    args = parser.parse_args()
    if settings.is_sqlite:
        create_all()
    rnd = random.Random(42)
    now = datetime.now(UTC)

    with session_scope() as db:
        existing = db.scalar(select(User).where(User.email == args.email))
        if existing and args.reset:
            delete_user_data(db, existing)
            db.flush()
        elif existing:
            print(f"{args.email} already exists (use --reset to recreate)")
            return
        prefs = default_preferences()
        prefs.update({"target_roles": ["Software Engineer", "Backend Engineer", "Platform Engineer"],
                      "target_locations": ["Austin", "Remote", "San Francisco"], "salary_min": 150000,
                      "salary_max": 220000, "auto_apply_threshold": 65, "timezone": "America/Chicago",
                      "sources": {**prefs["sources"], "greenhouse_boards": ["stripe", "airbnb"], "lever_companies": ["netflix"]}})
        user = User(email=args.email, full_name="Alex Rivera", hashed_password=hash_password(args.password),
                    phone="+1 (555) 010-2030", location="Austin, TX", linkedin_url="https://linkedin.com/in/alexrivera",
                    preferences=prefs, last_scan_at=now - timedelta(hours=2))
        db.add(user)
        db.flush()
        for name, value in (("work_authorization", "Yes"), ("requires_sponsorship", "No"), ("willing_to_relocate", "Yes"),
                            ("notice_period", "2 weeks")):
            db.add(UserFieldMapping(user_id=user.id, field_name=name, field_value=value, field_type="text"))
        content = heuristic_parse(RESUME)
        master = Resume(user_id=user.id, label="Master resume", parsed_content=content, raw_text=RESUME, is_master=True,
                        skills_embedding=embed_text(RESUME))
        db.add(master)
        db.flush()

        for idx, (company, title, location, platform, desc, salary) in enumerate(JOBS):
            description = f"{company} is hiring a {title}. {desc} We value ownership, clear communication and craft."
            job = db.scalar(select(Job).where(Job.source_url == f"https://example.com/{company.lower()}/{idx}"))
            if job is None:
                job = Job(company_name=company, role_title=title, description=description, location=location,
                          is_remote=location == "Remote", source_platform=platform, job_type=JobType.FULL_TIME,
                          source_url=f"https://example.com/{company.lower()}/{idx}", salary_min=salary[0], salary_max=salary[1],
                          application_url=f"https://example.com/{company.lower()}/{idx}/apply",
                          dedupe_key=dedupe_key(company, title, location), extracted_skills=extract_skills(f"{title} {desc}"),
                          posted_date=(now - timedelta(days=rnd.randint(1, 12))).date(),
                          description_embedding=embed_text(description))
                db.add(job)
                db.flush()
            status = STATUS_PLAN[idx]
            ev = heuristic_evaluation(content, job, prefs, 65)
            created = now - timedelta(days=rnd.randint(3, 25), hours=rnd.randint(0, 23))
            app = Application(user_id=user.id, job_id=job.id, status=status, match_score=ev["match_score"],
                              match_reasoning=ev["reasoning"], match_details=ev, ats_platform=platform, created_at=created,
                              updated_at=created + timedelta(days=1),
                              cover_letter=f"Dear Hiring Team,\n\nI'm excited to apply for the {title} role at {company}...\n\nSincerely,\nAlex Rivera",
                              custom_answers=[{"question": "Are you legally authorized to work in the US?", "answer": "Yes",
                                               "confidence": 0.95, "needs_user_review": False, "source": "rule"},
                                              {"question": f"Why {company}?", "answer": f"{company}'s engineering culture and scale excite me.",
                                               "confidence": 0.55, "needs_user_review": True, "source": "llm"}])
            if status not in (ApplicationStatus.PENDING_APPROVAL, ApplicationStatus.SKIPPED):
                app.submitted_at = created + timedelta(hours=6)
                app.approved_at = created + timedelta(hours=5)
            if status in (ApplicationStatus.INTERVIEW, ApplicationStatus.SCREENING, ApplicationStatus.ACKNOWLEDGED,
                          ApplicationStatus.REJECTED, ApplicationStatus.OFFER):
                app.first_response_at = created + timedelta(days=rnd.randint(2, 6))
            db.add(app)
            db.flush()
            previous = None
            for step in [s for s in PIPELINE if status not in (ApplicationStatus.SKIPPED,)][: (4 if status == ApplicationStatus.PENDING_APPROVAL else 5)]:
                db.add(ApplicationStatusHistory(application_id=app.id, old_status=previous, new_status=step, changed_by="agent",
                                                created_at=created))
                previous = step
            if status in (ApplicationStatus.INTERVIEW, ApplicationStatus.SCREENING, ApplicationStatus.OFFER,
                          ApplicationStatus.REJECTED, ApplicationStatus.ACKNOWLEDGED):
                intent = {ApplicationStatus.INTERVIEW: EmailIntent.INTERVIEW_INVITE, ApplicationStatus.SCREENING: EmailIntent.INTERVIEW_INVITE,
                          ApplicationStatus.OFFER: EmailIntent.OFFER, ApplicationStatus.REJECTED: EmailIntent.REJECTION,
                          ApplicationStatus.ACKNOWLEDGED: EmailIntent.ACKNOWLEDGMENT}[status]
                db.add(ApplicationStatusHistory(application_id=app.id, old_status=previous, new_status=status,
                                                changed_by="email_parser", notes=f"Detected '{intent.value}' e-mail"))
                db.add(Communication(user_id=user.id, application_id=app.id, gmail_message_id=f"demo-{uuid.uuid4().hex[:10]}",
                                     direction=EmailDirection.INBOUND, sender_email=f"recruiting@{company.lower().replace(' ', '')}.com",
                                     sender_name=f"{company} Recruiting", subject=f"{title} — next steps",
                                     body_text=f"Hi Alex,\n\nThanks for applying to {company}. (Demo e-mail: {intent.value.replace('_', ' ')}.)\n\nBest,\n{company} Recruiting",
                                     detected_intent=intent, intent_confidence=0.9, urgency="high" if intent != EmailIntent.ACKNOWLEDGMENT else "low",
                                     is_action_required=intent in (EmailIntent.INTERVIEW_INVITE, EmailIntent.OFFER),
                                     suggested_reply="Hi,\n\nThank you — I'd love to continue the conversation.\n\nBest regards,\nAlex Rivera"
                                     if intent != EmailIntent.REJECTION else None, received_at=app.first_response_at))
            if status in (ApplicationStatus.INTERVIEW, ApplicationStatus.SCREENING):
                db.add(Interview(application_id=app.id, interview_type=InterviewType.TECHNICAL if status == ApplicationStatus.INTERVIEW
                                 else InterviewType.PHONE_SCREEN, scheduled_at=now + timedelta(days=rnd.randint(1, 6), hours=3),
                                 duration_minutes=60, timezone="America/Chicago", meeting_link="https://zoom.us/j/1234567890",
                                 meeting_platform="zoom", outcome="pending",
                                 prep_notes=f"ROLE: {title} at {company}\nHighlight: FastAPI APIs at 5M req/day, Kafka pipelines.",
                                 likely_questions=[{"question": "Walk me through a system you designed.",
                                                    "answer_outline": "Northwind API platform: scale, trade-offs, results."}]))
        for idx, (company, title, location, platform, sponsorship) in enumerate(DECK):
            stack = rnd.sample(DECK_STACK, 4)
            description = (f"{title} at {company}. Summer 2027, 12 weeks. You'll ship production code with {', '.join(stack)} "
                           f"alongside a mentor and present your project at the end of the summer.")
            url = f"https://example.com/{company.lower().replace(' ', '')}/interns/{idx}"
            job = db.scalar(select(Job).where(Job.source_url == url))
            if job is None:
                job = Job(company_name=company, role_title=title, description=description, location=location,
                          is_remote="Remote" in location, source_platform=platform, job_type=JobType.INTERNSHIP,
                          source_url=url, application_url=url, dedupe_key=dedupe_key(company, title, location),
                          extracted_skills=extract_skills(description), posted_date=(now - timedelta(days=rnd.randint(0, 9))).date(),
                          description_embedding=embed_text(description),
                          raw_data={"listing_source": "simplify-internships", "sponsorship": sponsorship, "terms": ["Summer 2027"]})
                db.add(job)
                db.flush()
            ev = heuristic_evaluation(content, job, prefs, 65)
            db.add(Application(user_id=user.id, job_id=job.id, status=ApplicationStatus.MATCHED, match_score=ev["match_score"],
                               match_reasoning=ev["reasoning"], match_details={**ev, "heads_up": []}, ats_platform=platform))
        db.add(AgentRun(user_id=user.id, run_type="scan", trigger="schedule", status="completed", jobs_discovered=len(JOBS),
                        jobs_matched=7, started_at=now - timedelta(hours=2), completed_at=now - timedelta(hours=2) + timedelta(seconds=74),
                        duration_seconds=74, log=[{"ts": (now - timedelta(hours=2)).isoformat(), "level": "info",
                                                   "message": "Seeded demo scan"}]))
    print(f"Seeded demo account: {args.email} / {args.password}")


if __name__ == "__main__":
    main()
