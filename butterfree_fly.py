#!/usr/bin/env python
# coding: utf-8

# =========================================================
# IMPORTS
# =========================================================

import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from rapidfuzz import fuzz

# =========================================================
# STEP 1 — DOWNLOAD LATEST UK SPONSOR LIST
# =========================================================

GOV_PAGE = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"

print("Finding latest sponsor list...")

response = requests.get(GOV_PAGE)
soup = BeautifulSoup(response.text, "html.parser")

csv_url = None

for link in soup.find_all("a", href=True):

    href = link["href"]

    if "Worker_and_Temporary_Worker.csv" in href:

        if href.startswith("/"):
            csv_url = "https://www.gov.uk" + href
        else:
            csv_url = href

        break

if not csv_url:
    raise Exception("Could not find sponsor CSV URL")

print("\nLatest sponsor CSV:")
print(csv_url)

# Download sponsor CSV
sponsors = pd.read_csv(csv_url)

print(f"\nTotal sponsors: {len(sponsors)}")

# =========================================================
# STEP 2 — CLEAN SPONSOR DATA
# =========================================================

sponsors = sponsors.rename(columns={
    "Organisation Name": "company"
})

def normalize_company(name):

    return (
        str(name)
        .lower()
        .replace("limited", "")
        .replace("ltd", "")
        .replace("llp", "")
        .replace("inc", "")
        .replace("corp", "")
        .replace("corporation", "")
        .replace("holdings", "")
        .replace("group", "")
        .replace("&", "and")
        .replace(",", "")
        .replace(".", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
        .strip()
    )

sponsors["company_clean"] = (
    sponsors["company"]
    .astype(str)
    .apply(normalize_company)
)

print("\nSponsor columns:")
print(sponsors.columns)

# =========================================================
# STEP 3 — GREENHOUSE SCRAPER
# =========================================================

def get_greenhouse_jobs(company_slug):

    url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs"

    try:

        r = requests.get(url, timeout=15)

        if r.status_code != 200:
            print(f"Greenhouse failed: {company_slug}")
            return []

        data = r.json()

        jobs = []

        for job in data.get("jobs", []):

            jobs.append({
                "company_slug": company_slug,
                "job_title": job.get("title"),
                "location": job.get("location", {}).get("name"),
                "job_url": job.get("absolute_url"),
                "source": "Greenhouse"
            })

        return jobs

    except Exception as e:
        print(f"Greenhouse error with {company_slug}: {e}")
        return []

# =========================================================
# STEP 4 — LEVER SCRAPER
# =========================================================

def get_lever_jobs(company_slug):

    url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"

    try:

        r = requests.get(url, timeout=15)

        if r.status_code != 200:
            print(f"Lever failed: {company_slug}")
            return []

        data = r.json()

        jobs = []

        for job in data:

            jobs.append({
                "company_slug": company_slug,
                "job_title": job.get("text"),
                "location": job.get("categories", {}).get("location"),
                "job_url": job.get("hostedUrl"),
                "source": "Lever"
            })

        return jobs

    except Exception as e:
        print(f"Lever error with {company_slug}: {e}")
        return []

# =========================================================
# STEP 5 — MASSIVELY EXPANDED COMPANY LISTS
# =========================================================

GREENHOUSE_COMPANIES = [

    # =====================================================
    # FINTECH / PAYMENTS / BANKING
    # =====================================================

    "monzo",
    "wise",
    "checkoutcom",
    "stripe",
    "revolut",
    "klarna",
    "plaid",
    "affirm",
    "brex",
    "ramp",
    "airwallex",
    "sumup",
    "zopa",
    "starlingbank",

    # =====================================================
    # BIG TECH
    # =====================================================

    "datadog",
    "mongodb",
    "snyk",
    "cloudflare",
    "gitlab",
    "figma",
    "webflow",
    "elastic",
    "hashicorp",
    "snowflake",
    "openai",
    "notion",
    "miro",
    "canva",
    "dropbox",
    "discord",
    "reddit",
    "spotify",
    "palantir",
    "criteo",
    "contentful",

    # =====================================================
    # ENTERPRISE SOFTWARE / AI
    # =====================================================

    "salesforce",
    "hubspot",
    "servicenow",
    "databricks",
    "confluent",
    "nvidia",
    "deepmind",
    "anthropic",

    # =====================================================
    # CONSULTING / PROFESSIONAL SERVICES
    # =====================================================

    "mckinsey",
    "bcg",
    "bain",
    "pwc",
    "deloitte",
    "ey",
    "kpmg",
    "accenture",
    "capgemini",

    # =====================================================
    # HEALTH / BIOTECH / PHARMA
    # =====================================================

    "healx",
    "owkin",
    "color",
    "23andme",
    "flatironhealth",
    "tempus",
    "roche",
    "astrazeneca",
    "gsk",
    "pfizer",
    "novartis",
    "sanofi",

    # =====================================================
    # ENERGY / UTILITIES
    # =====================================================

    "octopusenergy",
    "shell",
    "bp",
    "nationalgrid",
    "siemensenergy",

    # =====================================================
    # CONSTRUCTION / ENGINEERING
    # =====================================================

    "procore",
    "autodesk",
    "planradar",
    "bentley",
    "buildots",
    "siemens",
    "jacobs",
    "aecom",

    # =====================================================
    # DEFENSE / AEROSPACE
    # =====================================================

    "bae",
    "boeing",
    "airbus",
    "northropgrumman",

    # =====================================================
    # LOGISTICS / TRANSPORT
    # =====================================================

    "uber",
    "bolt",
    "cargoone",
    "flexport",
    "deliveroo",

    # =====================================================
    # RETAIL / ECOMMERCE
    # =====================================================

    "amazon",
    "zalando",
    "wayfair",
    "etsy",

    # =====================================================
    # MEDIA / ENTERTAINMENT
    # =====================================================

    "netflix",
    "spotify",
    "bbc",
    "sky",

    # =====================================================
    # TELECOMS
    # =====================================================

    "vodafone",
    "bt",
    "verizon",

    # =====================================================
    # EDUCATION
    # =====================================================

    "duolingo",
    "udacity",
    "coursera",
    "masterclass",
]

LEVER_COMPANIES = [

    # =====================================================
    # TECH
    # =====================================================

    "netflix",
    "shopify",
    "asana",
    "robinhood",
    "coinbase",
    "atlassian",
    "digitalocean",
    "slack",
    "segment",
    "intercom",
    "zapier",

    # =====================================================
    # FINANCE
    # =====================================================

    "plaid",
    "brex",
    "ramp",
    "affirm",

    # =====================================================
    # BIOTECH
    # =====================================================

    "moderna",
    "invitae",
    "grail",

    # =====================================================
    # INDUSTRIAL / ENGINEERING
    # =====================================================

    "autodesk",
    "procore",
    "caterpillar",
    "siemens",

    # =====================================================
    # RETAIL / CONSUMER
    # =====================================================

    "nike",
    "adidas",
    "zappos",

    # =====================================================
    # AI / DATA
    # =====================================================

    "scaleai",
    "huggingface",
    "weightsandbiases",

    # =====================================================
    # WEB3
    # =====================================================

    "chainalysis",
    "consensys",
]

# =========================================================
# STEP 6 — DISPLAY NAMES
# =========================================================

COMPANY_DISPLAY_NAMES = {

    # EXISTING
    "monzo": "Monzo",
    "wise": "Wise",
    "checkoutcom": "Checkout.com",
    "stripe": "Stripe",
    "octopusenergy": "Octopus Energy",
    "deliveroo": "Deliveroo",
    "datadog": "Datadog",
    "mongodb": "MongoDB",
    "snyk": "Snyk",
    "cloudflare": "Cloudflare",
    "gitlab": "GitLab",
    "figma": "Figma",
    "webflow": "Webflow",

    # FINTECH
    "revolut": "Revolut",
    "klarna": "Klarna",
    "plaid": "Plaid",
    "affirm": "Affirm",
    "brex": "Brex",
    "ramp": "Ramp",
    "airwallex": "Airwallex",
    "sumup": "SumUp",
    "zopa": "Zopa",
    "starlingbank": "Starling Bank",

    # BIG TECH
    "elastic": "Elastic",
    "hashicorp": "HashiCorp",
    "snowflake": "Snowflake",
    "openai": "OpenAI",
    "notion": "Notion",
    "miro": "Miro",
    "canva": "Canva",
    "dropbox": "Dropbox",
    "discord": "Discord",
    "reddit": "Reddit",
    "spotify": "Spotify",
    "palantir": "Palantir",
    "contentful": "Contentful",

    # CONSULTING
    "mckinsey": "McKinsey",
    "bcg": "BCG",
    "bain": "Bain",
    "pwc": "PwC",
    "deloitte": "Deloitte",
    "ey": "EY",
    "kpmg": "KPMG",
    "accenture": "Accenture",
    "capgemini": "Capgemini",

    # PHARMA
    "pfizer": "Pfizer",
    "novartis": "Novartis",
    "sanofi": "Sanofi",

    # ENERGY
    "shell": "Shell",
    "bp": "BP",
    "nationalgrid": "National Grid",
    "siemensenergy": "Siemens Energy",

    # ENGINEERING
    "jacobs": "Jacobs",
    "aecom": "AECOM",

    # AEROSPACE
    "bae": "BAE Systems",
    "boeing": "Boeing",
    "airbus": "Airbus",
    "northropgrumman": "Northrop Grumman",

    # TELECOM
    "vodafone": "Vodafone",
    "bt": "BT",
    "verizon": "Verizon",

    # AI
    "scaleai": "Scale AI",
    "huggingface": "Hugging Face",
    "weightsandbiases": "Weights & Biases",

    # WEB3
    "chainalysis": "Chainalysis",
    "consensys": "Consensys",
}

# =========================================================
# STEP 7 — UK LOCATION FILTER
# =========================================================

UK_PATTERNS = [

    "uk",
    "united kingdom",
    "england",
    "scotland",
    "wales",

    "london",
    "manchester",
    "birmingham",
    "glasgow",
    "edinburgh",
    "bristol",
    "cambridge",
    "oxford",
    "leeds",
    "newcastle",
    "sheffield",
    "liverpool",
    "nottingham",
    "reading",
    "milton keynes",
    "southampton",
    "belfast",

    "remote uk",
    "hybrid uk",
    "remote - united kingdom",
    "united kingdom remote",
    "uk remote",
]

def is_uk_location(location):

    if not location:
        return False

    location = str(location).lower()

    return any(pattern in location for pattern in UK_PATTERNS)

# =========================================================
# STEP 8 — SCRAPE JOBS
# =========================================================

all_jobs = []

# ---------------- GREENHOUSE ----------------

for slug in GREENHOUSE_COMPANIES:

    jobs = get_greenhouse_jobs(slug)

    uk_jobs = [
        job for job in jobs
        if is_uk_location(job.get("location"))
    ]

    print(f"{slug} (Greenhouse): {len(uk_jobs)} UK jobs")

    for job in uk_jobs:
        job["company"] = COMPANY_DISPLAY_NAMES.get(slug, slug)

    all_jobs.extend(uk_jobs)

# ---------------- LEVER ----------------

for slug in LEVER_COMPANIES:

    jobs = get_lever_jobs(slug)

    uk_jobs = [
        job for job in jobs
        if is_uk_location(job.get("location"))
    ]

    print(f"{slug} (Lever): {len(uk_jobs)} UK jobs")

    for job in uk_jobs:
        job["company"] = COMPANY_DISPLAY_NAMES.get(slug, slug)

    all_jobs.extend(uk_jobs)

print(f"\nTotal UK jobs found: {len(all_jobs)}")

# =========================================================
# STEP 9 — CREATE DATAFRAME
# =========================================================

jobs_df = pd.DataFrame(all_jobs)

if len(jobs_df) == 0:
    raise Exception("No jobs found.")

# =========================================================
# STEP 10 — NORMALIZE JOB COMPANY NAMES
# =========================================================

jobs_df["company_clean"] = (
    jobs_df["company"]
    .astype(str)
    .apply(normalize_company)
)

# =========================================================
# STEP 11 — MATCH AGAINST SPONSOR LIST
# =========================================================

sponsor_names = (
    sponsors["company_clean"]
    .dropna()
    .tolist()
)

def is_sponsor(company_name, sponsor_list, threshold=80):

    company_name = normalize_company(company_name)

    for sponsor in sponsor_list:

        score = fuzz.token_set_ratio(company_name, sponsor)

        if score >= threshold:
            return True

    return False

jobs_df["is_licensed_sponsor"] = jobs_df["company"].apply(
    lambda x: is_sponsor(x, sponsor_names)
)

# Keep only sponsors
jobs_df = jobs_df[
    jobs_df["is_licensed_sponsor"] == True
]

print(f"\nLicensed sponsor jobs: {len(jobs_df)}")

# =========================================================
# STEP 12 — ADD METADATA
# =========================================================

today = datetime.today().strftime("%Y-%m-%d")

jobs_df["visa_sponsorship_possible"] = True
jobs_df["scraped_date"] = today

# =========================================================
# STEP 13 — FINAL CLEANUP
# =========================================================

jobs_df = jobs_df[[

    "company",
    "job_title",
    "location",
    "job_url",
    "source",
    "visa_sponsorship_possible",
    "scraped_date"
]]

# Remove duplicates
jobs_df = jobs_df.drop_duplicates()

# Sort nicely
jobs_df = jobs_df.sort_values(
    by=["company", "job_title"]
)

# =========================================================
# STEP 14 — EXPORT CSV
# =========================================================

jobs_df.to_csv("jobs.csv", index=False)

print("\nSaved jobs.csv")

print(f"\nFinal total sponsored UK jobs: {len(jobs_df)}")

print("\nDone.")
