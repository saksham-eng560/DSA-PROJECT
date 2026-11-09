"""Renowned companies to hunt internships at: big tech, product-based companies (PBCs), well-known Indian
and global startups, and AI companies.

Each entry has the company's name (plus the names it posts under), its tier for the "Top companies" page,
its website domain (a posting that sends you there is genuinely theirs) and, where known, its own job
board: Greenhouse / Lever / Ashby tokens or a Workday site. Companies that hire through their own career
site are found with a LinkedIn search for their name instead. A board that has moved is skipped (and
logged), never fatal, so the list is safe to extend: add an entry, nothing else changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

TIERS: dict[str, str] = {
    "big_tech": "Big Tech",
    "product": "Product companies",
    "startup_india": "Indian startups",
    "startup_global": "Global startups",
    "ai": "AI companies",
}


@dataclass(frozen=True)
class Company:
    name: str
    tier: str
    domain: str
    aliases: tuple[str, ...] = ()
    greenhouse: str | None = None
    lever: str | None = None
    ashby: str | None = None
    workday: str | None = None
    linkedin: bool = False  # hires through its own career site: searched on LinkedIn by name

    @property
    def tier_label(self) -> str:
        return TIERS[self.tier]


def _c(name: str, tier: str, domain: str, *aliases: str, **boards: str | bool) -> Company:
    return Company(name, tier, domain, tuple(aliases), **boards)  # type: ignore[arg-type]


CATALOG: tuple[Company, ...] = (
    # ------------------------------------------------------------------ big tech
    _c("Google", "big_tech", "google.com", "Alphabet", "Google India", "Google Cloud", "YouTube", linkedin=True),
    _c("Microsoft", "big_tech", "microsoft.com", "Microsoft India", "Microsoft India Development Center", linkedin=True),
    _c("Amazon", "big_tech", "amazon.jobs", "Amazon Web Services", "AWS", "Amazon Development Centre",
       "Amazon Development Center", linkedin=True),
    _c("Apple", "big_tech", "apple.com", linkedin=True),
    _c("Meta", "big_tech", "metacareers.com", "Meta Platforms", "Facebook", "Instagram", "WhatsApp", linkedin=True),
    _c("Netflix", "big_tech", "netflix.com"),
    _c("NVIDIA", "big_tech", "nvidia.com", workday="https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"),
    _c("Adobe", "big_tech", "adobe.com", workday="https://adobe.wd5.myworkdayjobs.com/external_experienced",
       linkedin=True),
    _c("Salesforce", "big_tech", "salesforce.com",
       workday="https://salesforce.wd12.myworkdayjobs.com/External_Career_Site"),
    _c("Oracle", "big_tech", "oracle.com", "Oracle India", linkedin=True),
    _c("Intel", "big_tech", "intel.com", workday="https://intel.wd1.myworkdayjobs.com/External"),
    _c("Cisco", "big_tech", "cisco.com", "Cisco Systems", linkedin=True),
    _c("IBM", "big_tech", "ibm.com", "IBM India"),
    _c("Qualcomm", "big_tech", "qualcomm.com", "Qualcomm India"),
    _c("Samsung", "big_tech", "samsung.com", "Samsung Electronics", "Samsung R&D Institute India", "Samsung Research"),
    _c("Uber", "big_tech", "uber.com", linkedin=True),
    _c("Atlassian", "big_tech", "atlassian.com", linkedin=True),
    _c("SAP", "big_tech", "sap.com", "SAP Labs", "SAP Labs India"),
    _c("LinkedIn", "big_tech", "linkedin.com"),
    _c("Spotify", "big_tech", "spotify.com", lever="spotify"),
    # ------------------------------------------------------------------ product-based companies
    _c("Stripe", "product", "stripe.com", greenhouse="stripe"),
    _c("Airbnb", "product", "airbnb.com", greenhouse="airbnb"),
    _c("Databricks", "product", "databricks.com", greenhouse="databricks"),
    _c("Dropbox", "product", "dropbox.com", greenhouse="dropbox"),
    _c("Pinterest", "product", "pinterest.com", greenhouse="pinterest"),
    _c("Reddit", "product", "reddit.com", greenhouse="reddit"),
    _c("Lyft", "product", "lyft.com", greenhouse="lyft"),
    _c("GitLab", "product", "gitlab.com", greenhouse="gitlab"),
    _c("Cloudflare", "product", "cloudflare.com", greenhouse="cloudflare"),
    _c("Twilio", "product", "twilio.com", greenhouse="twilio"),
    _c("Datadog", "product", "datadoghq.com", greenhouse="datadog"),
    _c("Elastic", "product", "elastic.co", greenhouse="elastic"),
    _c("MongoDB", "product", "mongodb.com", greenhouse="mongodb"),
    _c("Okta", "product", "okta.com", greenhouse="okta"),
    _c("Rubrik", "product", "rubrik.com", greenhouse="rubrik"),
    _c("Coinbase", "product", "coinbase.com", greenhouse="coinbase"),
    _c("Robinhood", "product", "robinhood.com", greenhouse="robinhood"),
    _c("Instacart", "product", "instacart.com", greenhouse="instacart"),
    _c("Duolingo", "product", "duolingo.com", greenhouse="duolingo"),
    _c("Asana", "product", "asana.com", greenhouse="asana"),
    _c("Airtable", "product", "airtable.com", greenhouse="airtable"),
    _c("Discord", "product", "discord.com", greenhouse="discord"),
    _c("Figma", "product", "figma.com", greenhouse="figma"),
    _c("Intuit", "product", "intuit.com"),
    _c("PayPal", "product", "paypal.com"),
    _c("Visa", "product", "visa.com"),
    _c("Mastercard", "product", "mastercard.com", workday="https://mastercard.wd1.myworkdayjobs.com/CorporateCareers"),
    _c("Walmart Global Tech", "product", "walmart.com", "Walmart", "Walmart Labs",
       workday="https://walmart.wd5.myworkdayjobs.com/WalmartExternal"),
    _c("ServiceNow", "product", "servicenow.com"),
    _c("Snowflake", "product", "snowflake.com"),
    _c("Nutanix", "product", "nutanix.com"),
    _c("Freshworks", "product", "freshworks.com", linkedin=True),
    _c("Zoho", "product", "zoho.com", "Zoho Corporation", linkedin=True),
    _c("Palantir", "product", "palantir.com", "Palantir Technologies", lever="palantir"),
    _c("Shopify", "product", "shopify.com"),
    # Product companies with large Indian engineering centres that hire interns there, on their own public boards.
    _c("Zscaler", "product", "zscaler.com", greenhouse="zscaler"),  # Mohali, Bengaluru, Pune
    _c("ThoughtSpot", "product", "thoughtspot.com", linkedin=True),  # Bengaluru, Hyderabad; its Greenhouse board is gone
    _c("Tower Research Capital", "product", "tower-research.com", "Tower Research",
       greenhouse="towerresearchcapital"),  # Gurugram
    # ------------------------------------------------------------------ Indian startups
    _c("Flipkart", "startup_india", "flipkart.com", "Flipkart Internet", linkedin=True),
    _c("Swiggy", "startup_india", "swiggy.com", "Bundl Technologies", linkedin=True),
    _c("Zomato", "startup_india", "zomato.com", "Eternal", "Zomato Media", "Blinkit", linkedin=True),
    _c("Razorpay", "startup_india", "razorpay.com", "Razorpay Software", linkedin=True),  # its Lever board is gone (404)
    _c("CRED", "startup_india", "cred.club", "Dreamplug Technologies", lever="cred"),
    _c("Meesho", "startup_india", "meesho.com", "Fashnear Technologies", lever="meesho"),
    _c("PhonePe", "startup_india", "phonepe.com", greenhouse="phonepe"),
    _c("Paytm", "startup_india", "paytm.com", "One97 Communications", linkedin=True),
    _c("Zepto", "startup_india", "zeptonow.com", "Kiranakart Technologies"),
    _c("Groww", "startup_india", "groww.in", "Nextbillion Technology"),
    _c("Zerodha", "startup_india", "zerodha.com"),
    _c("Ola", "startup_india", "olacabs.com", "Ola Cabs", "ANI Technologies", "Ola Electric", "Krutrim"),
    _c("Myntra", "startup_india", "myntra.com", "Myntra Designs"),
    _c("Dream11", "startup_india", "dream11.com", "Dream Sports"),
    _c("Nykaa", "startup_india", "nykaa.com", "FSN E-Commerce Ventures"),
    _c("InMobi", "startup_india", "inmobi.com", "Glance"),
    _c("ShareChat", "startup_india", "sharechat.com", "Mohalla Tech", "Moj"),
    _c("Unacademy", "startup_india", "unacademy.com"),
    _c("upGrad", "startup_india", "upgrad.com"),
    _c("PhysicsWallah", "startup_india", "pw.live", "Physics Wallah"),
    _c("Urban Company", "startup_india", "urbancompany.com", "UrbanClap"),
    _c("Lenskart", "startup_india", "lenskart.com"),
    _c("Delhivery", "startup_india", "delhivery.com"),
    _c("Juspay", "startup_india", "juspay.in", "Juspay Technologies"),
    _c("Postman", "startup_india", "postman.com", greenhouse="postman"),
    _c("BrowserStack", "startup_india", "browserstack.com", greenhouse="browserstack"),
    _c("Chargebee", "startup_india", "chargebee.com"),
    _c("Hasura", "startup_india", "hasura.io"),
    _c("Atlan", "startup_india", "atlan.com"),
    _c("Rapido", "startup_india", "rapido.bike", "Roppen Transportation Services"),
    _c("Zeta", "startup_india", "zeta.tech", "Zeta Suite", lever="zeta"),
    _c("Innovaccer", "startup_india", "innovaccer.com"),
    _c("Darwinbox", "startup_india", "darwinbox.com"),
    _c("Sprinklr", "startup_india", "sprinklr.com"),
    _c("MakeMyTrip", "startup_india", "makemytrip.com"),
    _c("PolicyBazaar", "startup_india", "policybazaar.com", "PB Fintech"),
    _c("Cars24", "startup_india", "cars24.com"),
    _c("Udaan", "startup_india", "udaan.com"),
    # Delhi NCR product companies and startups (recognised by name: their postings come through Naukri, Unstop,
    # LinkedIn and Internshala, so no board of their own is searched).
    _c("Info Edge", "startup_india", "infoedge.in", "Naukri.com", "Info Edge India"),
    _c("Pine Labs", "startup_india", "pinelabs.com"),
    _c("MapmyIndia", "startup_india", "mapmyindia.com", "CE Info Systems", "MapMyIndia Mappls", "Mappls"),
    _c("Spinny", "startup_india", "spinny.com", "Valuedrive Technologies"),
    _c("OYO", "startup_india", "oyorooms.com", "OYO Rooms", "Oravel Stays"),
    _c("GreyOrange", "startup_india", "greyorange.com", "GreyOrange Robotics"),
    _c("Wingify", "startup_india", "wingify.com", "VWO"),
    _c("Classplus", "startup_india", "classplusapp.com"),
    _c("CoinDCX", "startup_india", "coindcx.com"),
    # ------------------------------------------------------------------ global startups
    _c("Notion", "startup_global", "notion.so", ashby="notion"),
    _c("Ramp", "startup_global", "ramp.com", ashby="ramp"),
    _c("Linear", "startup_global", "linear.app", ashby="linear"),
    _c("Supabase", "startup_global", "supabase.com", ashby="supabase"),
    _c("Vercel", "startup_global", "vercel.com", greenhouse="vercel"),
    _c("Brex", "startup_global", "brex.com", greenhouse="brex"),
    _c("Plaid", "startup_global", "plaid.com"),  # its Lever board is gone (404)
    _c("Deel", "startup_global", "deel.com", ashby="deel"),
    _c("Rippling", "startup_global", "rippling.com"),
    _c("Replit", "startup_global", "replit.com", ashby="replit"),
    _c("Canva", "startup_global", "canva.com"),
    _c("Revolut", "startup_global", "revolut.com"),
    _c("Wise", "startup_global", "wise.com"),
    _c("Grammarly", "startup_global", "grammarly.com"),
    _c("Miro", "startup_global", "miro.com"),
    _c("Retool", "startup_global", "retool.com"),
    # ------------------------------------------------------------------ AI companies
    _c("OpenAI", "ai", "openai.com", ashby="openai"),
    _c("Anthropic", "ai", "anthropic.com", greenhouse="anthropic"),
    _c("Google DeepMind", "ai", "deepmind.google", "DeepMind"),
    _c("Mistral AI", "ai", "mistral.ai", "Mistral", lever="mistral"),
    _c("Cohere", "ai", "cohere.com", ashby="cohere"),
    _c("Perplexity", "ai", "perplexity.ai", "Perplexity AI", ashby="perplexity"),
    _c("Scale AI", "ai", "scale.com", greenhouse="scaleai"),
    _c("Hugging Face", "ai", "huggingface.co", "HuggingFace"),
    _c("ElevenLabs", "ai", "elevenlabs.io", ashby="elevenlabs"),
    _c("Glean", "ai", "glean.com", greenhouse="gleanwork"),
    _c("Sarvam AI", "ai", "sarvam.ai", "Sarvam"),
    _c("Character.AI", "ai", "character.ai", "Character AI"),
    _c("Runway", "ai", "runwayml.com", "Runway AI"),
    _c("Stability AI", "ai", "stability.ai"),
    _c("xAI", "ai", "x.ai"),
)

# Words that don't change who the company is: "Google India Private Limited" is Google. Words like
# "Solutions" or "Services" are NOT stripped: "Google Solutions" is a classic fake-company name.
_LEGAL = {"inc", "incorporated", "llc", "llp", "ltd", "limited", "pvt", "private", "corp", "corporation", "co",
          "company", "plc", "gmbh", "india", "the", "technologies", "technology", "software", "labs"}


def normalize_company(name: str | None) -> str:
    words = re.sub(r"[^a-z0-9& ]+", " ", (name or "").lower().replace(".", "")).split()
    while words and words[-1] in _LEGAL:
        words.pop()
    while words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


@lru_cache(maxsize=1)
def _index() -> dict[str, Company]:
    index: dict[str, Company] = {}
    for company in CATALOG:
        for name in (company.name, *company.aliases):
            index.setdefault(normalize_company(name), company)
    return index


def match_company(name: str | None) -> Company | None:
    """The catalog company a posting's company name refers to (exact name or a known alias), or None."""
    key = normalize_company(name)
    return _index().get(key) if key else None


def by_board(platform: str, token: str | None) -> Company | None:
    """The catalog company that owns a Greenhouse / Lever / Ashby board or Workday site."""
    if not token:
        return None
    token = token.lower().rstrip("/")
    for company in CATALOG:
        own = getattr(company, platform, None)
        if isinstance(own, str) and own.lower().rstrip("/") == token:
            return company
    return None


def owns_url(company: Company, url: str | None) -> bool:
    """The URL is on the company's own website (or its own job board)."""
    from urllib.parse import urlparse

    host = (urlparse(url or "").hostname or "").lower()
    return bool(host) and (host == company.domain or host.endswith("." + company.domain))
