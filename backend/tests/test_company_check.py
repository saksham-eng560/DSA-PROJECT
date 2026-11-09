"""The company-check agent, the Internshala cap and the top-companies source (reported: "Internshala and fraud
companies fill my whole application list; verify companies and only apply to legit ones; find big tech and
renowned startups and give them their own section")."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.application import Application
from app.models.enums import ApplicationStatus, ATSPlatform, JobType
from app.models.job import Job
from app.models.user import User
from app.scrapers import SCRAPERS, SearchQuery
from app.scrapers import top_companies as tc
from app.scrapers.base import ScrapedJob
from app.services import agent_orchestrator as orch
from app.services.company_catalog import match_company, normalize_company
from app.services.company_verifier import SUSPICIOUS, UNVERIFIED, VERIFIED, check_company, verify_with_llm
from app.services.source_mix import cap_internshala

LONG = "Build Python services with our platform team. You'll ship features, write tests and learn from mentors. " * 3


def _job(company: str, title: str = "Software Engineer Intern", *, internshala: bool = False, desc: str = LONG,
         platform: ATSPlatform = ATSPlatform.CUSTOM, url: str | None = None, location: str = "Bengaluru, India",
         raw: dict | None = None) -> ScrapedJob:
    slug = normalize_company(company).replace(" ", "-") + "-" + title.lower().replace(" ", "-")
    url = url or (f"https://internshala.com/internship/detail/{slug}" if internshala else f"https://careers.example/{slug}")
    return ScrapedJob(company_name=company, role_title=title, description=desc, source_url=url, application_url=url,
                      source_platform=platform, location=location, job_type=JobType.INTERNSHIP,
                      raw={**({"listing_source": "internshala"} if internshala else {}), **(raw or {})}).finalize()


# ------------------------------------------------------------------ the catalog
def test_catalog_matches_legal_names_but_not_lookalikes() -> None:
    assert match_company("Google India Private Limited").name == "Google"
    assert match_company("Amazon Development Centre (India) Pvt. Ltd.") is None or match_company("Amazon").tier == "big_tech"
    assert match_company("Microsoft Corporation").name == "Microsoft" and match_company("Facebook").name == "Meta"
    assert match_company("Swiggy").tier == "startup_india" and match_company("OpenAI").tier == "ai"
    assert match_company("Google Solutions") is None  # the classic fake-company name
    assert match_company("Metaverse Labs") is None and match_company("") is None


# ------------------------------------------------------------------ the rules
def test_verdicts() -> None:
    def verdict(company: str, desc: str = LONG, **kw: Any) -> str:
        return check_company(company, desc, **kw).verdict

    assert verdict("Google India Private Limited", urls=["https://careers.google.com/jobs/1"]) == VERIFIED
    big = check_company("Google", LONG)
    assert big.tier == "big_tech" and "renowned" in big.reasons[0]
    assert verdict("Acme Robotics", platform=ATSPlatform.GREENHOUSE) == VERIFIED  # the company's own board
    assert verdict("Acme Robotics", urls=["https://jobs.lever.co/acme/1"]) == VERIFIED
    assert verdict("Acme Robotics", raw={"listing_source": "internshala"}) == UNVERIFIED
    # Scam signs
    fee = "Selected candidates must pay a registration fee of Rs 1999 for the training kit. " + LONG
    assert verdict("Bright Future Edutech", fee) == SUSPICIOUS
    assert verdict("Google", fee) == SUSPICIOUS  # a famous name doesn't excuse a fee
    assert verdict("Acme", "Send your CV on WhatsApp to apply. " + LONG) == SUSPICIOUS
    assert verdict("Acme", "Earn up to ₹2000 per day from home! " + LONG) == SUSPICIOUS
    assert verdict("Acme", "Join our network marketing team. " + LONG) == SUSPICIOUS
    # ... but not when the posting says there is no fee
    assert verdict("Acme", "There is no registration fee for this internship. " + LONG) == UNVERIFIED
    # Three warning signs together
    shady = "Urgent hiring! Data entry work, commission only. Mail hr.acme@gmail.com"
    assert verdict("Star Enterprises", shady) == SUSPICIOUS
    # You have the last word
    assert verdict("Bright Future Edutech", fee, trusted=["Bright Future Edutech Pvt Ltd"]) == VERIFIED


def test_the_ai_only_verifies_what_the_posting_backs_up(fake_llm: Any) -> None:
    job = SimpleNamespace(company_name="Tiny Labs", role_title="ML Intern", description="Apply at https://tinylabs.ai/careers",
                          application_url="https://tinylabs.ai/careers/42", source_url="https://tinylabs.ai/careers/42",
                          company_domain=None)
    check = check_company(job.company_name, job.description)
    fake_llm({"COMPANY LEGITIMACY CHECK": {"recognized": True, "official_domain": "tinylabs.ai", "legit": "yes", "concerns": []}})
    assert verify_with_llm(job, check).verdict == VERIFIED
    # The AI vouches, but the posting doesn't point to the domain it names: not enough
    fake_llm({"COMPANY LEGITIMACY CHECK": {"recognized": True, "official_domain": "tinylabs.com", "legit": "yes", "concerns": []}})
    assert verify_with_llm(job, check).verdict == UNVERIFIED
    fake_llm({"COMPANY LEGITIMACY CHECK": {"recognized": False, "official_domain": "", "legit": "no",
                                           "concerns": ["asks for a deposit"]}})
    flagged = verify_with_llm(job, check)
    assert flagged.verdict == SUSPICIOUS and "AI: asks for a deposit" in flagged.reasons[-1]


# ------------------------------------------------------------------ the Internshala cap
def test_internshala_is_capped_at_a_quarter_keeping_the_best() -> None:
    others = [_job(f"Company {i}", f"Intern {i}") for i in range(12)]
    fee = "Pay a security deposit of Rs 500 to join. " + LONG
    ours = ([_job("Swiggy", f"Intern {i}", internshala=True) for i in range(2)]
            + [_job(f"Small Co {i}", "Intern", internshala=True) for i in range(20)]
            + [_job("Scam Co", f"Intern {i}", internshala=True, desc=fee) for i in range(3)])
    plenty = [_job(f"Company {i}", f"Intern {i}") for i in range(60)]
    kept, dropped = cap_internshala(plenty + ours, 25, per_scan=50)
    kept_ours = [j for j in kept if "internshala" in j.source_url]
    assert len(kept_ours) == 20 and dropped == 5  # 60 from elsewhere -> at most 20 from Internshala (25 %)
    assert {j.company_name for j in kept_ours[:2]} == {"Swiggy"}  # renowned first, original order kept
    assert all(j.company_name != "Scam Co" for j in kept_ours)
    assert all(j in kept for j in plenty)
    # A thin scan (12 from elsewhere): the share would keep 4; Internshala fills up to its per-scan limit instead
    kept, dropped = cap_internshala(others + ours, 25)
    kept_ours = [j for j in kept if "internshala" in j.source_url]
    assert len(kept_ours) == 10 and dropped == 15 and all(j.company_name != "Scam Co" for j in kept_ours)
    assert cap_internshala(ours, 25)[0].__len__() == 10  # only Internshala answered: still its best 10
    kept, dropped = cap_internshala(others * 10 + ours, 100)  # no share limit: still at most 10 a scan
    assert sum(1 for j in kept if "internshala" in j.source_url) == 10 and dropped == 15
    assert cap_internshala(others * 10 + ours, 100, per_scan=50) == (others * 10 + ours, 0)


# ------------------------------------------------------------------ the top-companies source
def test_top_companies_keeps_internships_at_that_company(monkeypatch: Any) -> None:
    from app.scrapers.greenhouse import GreenhouseScraper
    from app.scrapers.linkedin import LinkedInScraper

    monkeypatch.setattr(tc, "tasks", lambda: ["greenhouse|stripe|Stripe", "linkedin||Google"])
    monkeypatch.setattr(GreenhouseScraper, "list_board", lambda self, token: [
        _job("Stripe", "Software Engineering Intern", platform=ATSPlatform.GREENHOUSE),
        _job("Stripe", "Staff Engineer", platform=ATSPlatform.GREENHOUSE)])
    searched: list[Any] = []

    def linkedin(self: Any, query: SearchQuery) -> list[ScrapedJob]:
        searched.append(query)
        return [_job("Google", "Software Engineering Intern, 2027", platform=ATSPlatform.LINKEDIN),
                _job("Googly Solutions", "Software Intern", platform=ATSPlatform.LINKEDIN)]

    monkeypatch.setattr(LinkedInScraper, "search", linkedin)
    jobs = tc.TopCompaniesScraper().search(SearchQuery(limit=50))
    assert sorted((j.company_name, j.role_title) for j in jobs) == [
        ("Google", "Software Engineering Intern, 2027"), ("Stripe", "Software Engineering Intern")]
    assert searched[0].search_terms == ["Google intern"] and searched[0].job_types == ["internship"]
    assert {j.raw["company_tier"] for j in jobs} == {"big_tech", "product"}


# ------------------------------------------------------------------ a whole scan
class _Board:
    name = "board"
    jobs: list[ScrapedJob] = []

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        return list(self.jobs)


def test_scan_caps_internshala_flags_fraud_and_files_top_companies(auth_client: TestClient, master_resume: dict,
                                                                   monkeypatch: Any) -> None:
    fee = "Candidates must pay a training fee of Rs 2500 before joining. " + LONG
    internshala = ([_job(f"Small Co {i}", f"Intern {i}", internshala=True) for i in range(15)]
                   + [_job("Scam Co", "Marketing Intern", internshala=True, desc=fee)])
    others = [_job("Google", "Software Engineering Intern", platform=ATSPlatform.LINKEDIN,
                   url="https://www.linkedin.com/jobs/view/1/")] + [_job(f"Startup {i}", f"Backend Intern {i}") for i in range(8)]
    monkeypatch.setitem(SCRAPERS, "board_i", type("BoardI", (_Board,), {"name": "board_i", "jobs": internshala}))
    monkeypatch.setitem(SCRAPERS, "board_o", type("BoardO", (_Board,), {"name": "board_o", "jobs": others}))
    c = auth_client
    c.put("/api/v1/users/me/preferences", json={"preferences": {"target_roles": [], "location_focus": {"enabled": False}}})
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == c.get("/api/v1/auth/me").json()["email"]).one()
        assert orch.scan_platforms(user.prefs, None)[0] == "top_companies"  # added to your saved sources
        assert orch.scan_platforms(user.prefs, ["board_i"]) == ["board_i"]  # not when you pick the sources
        run = orch.run_scan(db, user, platforms=["board_i", "board_o"])
        db.commit()
        assert run.status == "completed", run.log
        jobs = db.query(Job).all()
        from_internshala = [j for j in jobs if "internshala.com" in j.source_url]
        # 9 from elsewhere is a thin scan: Internshala isn't held to 25 % (3) but to its 10 a scan; never the fraud
        assert len(from_internshala) == 10
        assert all(j.company_name != "Scam Co" for j in from_internshala)
        google = next(j for j in jobs if j.company_name == "Google")
        assert google.company_tier == "big_tech" and google.company_verdict == VERIFIED
        assert {j.company_verdict for j in from_internshala} == {UNVERIFIED}

    # The deck: renowned companies first
    deck = c.get("/api/v1/review/queue").json()["items"]
    assert deck[0]["job"]["company_name"] == "Google" and deck[0]["job"]["company"]["tier"] == "big_tech"
    # The Top companies section
    top = c.get("/api/v1/jobs/top-companies").json()
    assert top["total"] == 1 and top["items"][0]["company_name"] == "Google"
    assert {t["key"]: t["count"] for t in top["tiers"]}["big_tech"] == 1 and "Google" in top["catalog"]["big_tech"]
    assert c.get("/api/v1/jobs/top-companies?tier=ai").json()["items"] == []
    assert c.get("/api/v1/jobs/top-companies?tier=nope").status_code == 422


def test_fraud_already_in_your_deck_is_skipped(auth_client: TestClient) -> None:
    email = auth_client.get("/api/v1/auth/me").json()["email"]
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        job = Job(company_name="Quick Cash Pvt Ltd", role_title="Work From Home Intern", source_url="https://internshala.com/x/1",
                  source_platform=ATSPlatform.CUSTOM, description="Earn up to Rs 3000 per day. Contact us on WhatsApp to apply.",
                  job_type=JobType.INTERNSHIP, raw_data={"listing_source": "internshala"})
        db.add(job)
        db.flush()
        db.add(Application(user_id=user.id, job_id=job.id, status=ApplicationStatus.MATCHED, match_score=70))
        db.commit()
    deck = auth_client.get("/api/v1/review/queue").json()
    assert deck["items"] == []  # checked (it predates the company check) and skipped
    with SessionLocal() as db:
        app = db.query(Application).one()
        assert app.status == ApplicationStatus.SKIPPED and app.match_reasoning.startswith("Possible fraud")
        assert app.job.company_verdict == SUSPICIOUS


def test_new_preferences_are_validated(auth_client: TestClient) -> None:
    url = "/api/v1/users/me/preferences"
    assert auth_client.put(url, json={"preferences": {"internshala_share": 140}}).status_code == 422
    assert auth_client.put(url, json={"preferences": {"skip_suspicious_companies": "yes"}}).status_code == 422
    assert auth_client.put(url, json={"preferences": {"trusted_companies": "Acme"}}).status_code == 422
    ok = auth_client.put(url, json={"preferences": {"internshala_share": 10, "scan_top_companies": False}}).json()
    assert ok["internshala_share"] == 10 and ok["scan_top_companies"] is False and ok["skip_suspicious_companies"] is True
