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
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================================================
# CONFIG
# =========================================================

MAX_WORKERS = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# =========================================================
# STEP 1 — DOWNLOAD LATEST UK SPONSOR LIST
# =========================================================

GOV_PAGE = (
    "https://www.gov.uk/government/publications/"
    "register-of-licensed-sponsors-workers"
)

print("Finding latest sponsor list...")

response = requests.get(
    GOV_PAGE,
    headers=HEADERS,
    timeout=30
)

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

sponsors = pd.read_csv(csv_url)

print(f"\nTotal sponsors: {len(sponsors)}")

# =========================================================
# STEP 2 — NORMALIZATION
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

sponsor_set = set(
    sponsors["company_clean"]
    .dropna()
)

# =========================================================
# STEP 3 — UK LOCATION FILTER
# =========================================================

UK_PATTERNS = [

    # Countries
    "united kingdom",
    "great britain",
    "england",
    "scotland",
    "wales",
    "northern ireland",
    "uk",

    # Cities
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
    "cardiff",
    "swansea",
    "newport",

    # Remote
    "remote uk",
    "uk remote",
    "remote - united kingdom",
    "hybrid uk",
]

AUSTRALIA_BLOCKLIST = [

    "australia",
    "australian",
    "new south wales",
    "nsw",
    "sydney",
    "melbourne",
    "brisbane",
    "perth",
    "adelaide",
    "canberra",
    "south wales australia",
]

def is_uk_location(location):

    if not location:
        return False

    location = str(location).lower().strip()

    # Block Australia first
    if any(
        blocked in location
        for blocked in AUSTRALIA_BLOCKLIST
    ):
        return False

    # Then allow UK patterns
    return any(
        pattern in location
        for pattern in UK_PATTERNS
    )

# =========================================================
# STEP 4 — GREENHOUSE SCRAPER
# =========================================================

def get_greenhouse_jobs(company_slug):

    url = (
        f"https://boards-api.greenhouse.io/v1/"
        f"boards/{company_slug}/jobs"
    )

    try:

        r = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        if r.status_code != 200:
            return []

        data = r.json()

        jobs = []

        for job in data.get("jobs", []):

            jobs.append({
                "company_slug": company_slug,
                "job_title": job.get("title"),
                "location": (
                    job.get("location", {})
                    .get("name")
                ),
                "job_url": job.get("absolute_url"),
                "source": "Greenhouse"
            })

        return jobs

    except Exception as e:

        print(f"Greenhouse error with {company_slug}: {e}")
        return []

# =========================================================
# STEP 5 — LEVER SCRAPER
# =========================================================

def get_lever_jobs(company_slug):

    url = (
        f"https://api.lever.co/v0/postings/"
        f"{company_slug}?mode=json"
    )

    try:

        r = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        if r.status_code != 200:
            return []

        data = r.json()

        jobs = []

        for job in data:

            jobs.append({
                "company_slug": company_slug,
                "job_title": job.get("text"),
                "location": (
                    job.get("categories", {})
                    .get("location")
                ),
                "job_url": job.get("hostedUrl"),
                "source": "Lever"
            })

        return jobs

    except Exception as e:

        print(f"Lever error with {company_slug}: {e}")
        return []

# =========================================================
# STEP 6 — WORKDAY SCRAPER
# =========================================================

def get_workday_jobs(company, tenant):

    url = (
        f"https://{company}.wd1.myworkdayjobs.com"
        f"/wday/cxs/{company}/{tenant}/jobs"
    )

    payload = {
        "limit": 20,
        "offset": 0,
        "searchText": ""
    }

    jobs = []

    try:

        while True:

            r = requests.post(
                url,
                headers=HEADERS,
                json=payload,
                timeout=30
            )

            if r.status_code != 200:
                return jobs

            data = r.json()

            postings = data.get("jobPostings", [])

            if not postings:
                break

            for job in postings:

                jobs.append({
                    "company_slug": company,
                    "job_title": job.get("title"),
                    "location": job.get("locationsText"),
                    "job_url": (
                        f"https://{company}.wd1.myworkdayjobs.com"
                        f"/en-US/{tenant}"
                        f"{job.get('externalPath')}"
                    ),
                    "source": "Workday"
                })

            payload["offset"] += payload["limit"]

        return jobs

    except Exception as e:

        print(f"Workday error with {company}: {e}")
        return []

# =========================================================
# STEP 7 — SMARTRECRUITERS SCRAPER
# =========================================================

def get_smartrecruiters_jobs(company_slug):

    url = (
        f"https://api.smartrecruiters.com"
        f"/v1/companies/{company_slug}/postings"
    )

    try:

        r = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        if r.status_code != 200:
            return []

        data = r.json()

        jobs = []

        for job in data.get("content", []):

            location_data = job.get("location", {})

            location = " ".join([
                str(location_data.get("city", "")),
                str(location_data.get("country", "")),
            ])

            jobs.append({
                "company_slug": company_slug,
                "job_title": job.get("name"),
                "location": location,
                "job_url": job.get("ref"),
                "source": "SmartRecruiters"
            })

        return jobs

    except Exception as e:

        print(f"SmartRecruiters error: {e}")
        return []

# =========================================================
# STEP 8 — ASHBY SCRAPER
# =========================================================

def get_ashby_jobs(company_slug):

    url = (
        "https://jobs.ashbyhq.com/api/"
        "non-user-graphql?op=ApiJobBoardWithTeams"
    )

    payload = {
        "operationName": "ApiJobBoardWithTeams",
        "variables": {
            "organizationHostedJobsPageName": company_slug
        },
        "query": """
        query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
          jobBoard: jobBoardWithTeams(
            organizationHostedJobsPageName: $organizationHostedJobsPageName
          ) {
            jobs {
              title
              locationName
              absoluteUrl
            }
          }
        }
        """
    }

    try:

        r = requests.post(
            url,
            headers=HEADERS,
            json=payload,
            timeout=20
        )

        if r.status_code != 200:
            return []

        data = r.json()

        jobs = []

        for job in data["data"]["jobBoard"]["jobs"]:

            jobs.append({
                "company_slug": company_slug,
                "job_title": job.get("title"),
                "location": job.get("locationName"),
                "job_url": job.get("absoluteUrl"),
                "source": "Ashby"
            })

        return jobs

    except Exception as e:

        print(f"Ashby error with {company_slug}: {e}")
        return []

# =========================================================
# STEP 9 — COMPANY LISTS
# =========================================================

GREENHOUSE_COMPANIES = [

    # =====================================================
    # FINTECH
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
    # TECH
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
    "contentful",

    # =====================================================
    # CONSULTING
    # =====================================================

    "mckinsey",
    "bcg",
    "bain",

    # =====================================================
    # ENERGY
    # =====================================================

    "octopusenergy",

    # =====================================================
    # LOGISTICS
    # =====================================================

    "deliveroo",
    "uber",

    # =====================================================
    # JOURNALISM / RESEARCH / MEDIA
    # =====================================================

    "economist",
    "thomsonreuters",
    "bellingcat",
    "restofworld",
    "semafor",
    "theathletic",
    "voxmedia",
    "buzzfeed",
    "businessinsider",
    "washingtonpost",
    "forbes",
    "giphy",
    "axios",
    "morningbrew",
    "newscientist",
]

LEVER_COMPANIES = [

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
    "scaleai",
    "huggingface",

    # Journalism / Research
    "substack",
    "quora",
    "medium",
    "protocol",
    "theinformation",
    "deepl",
]

WORKDAY_COMPANIES = [

    ("barclays", "External_Career_Site"),
    ("hsbc", "HSBCCareers"),
    ("jpmorgan", "jpmc"),
    ("goldmansachs", "External"),
    ("morganstanley", "MorganStanleyCareers"),
    ("blackrock", "BlackRockCareers"),
    ("natwestgroup", "NatWest_Group_Careers"),

    # Research / Financial Data
    ("bloomberg", "careers"),
    ("factset", "FactSetCareers"),
]

SMARTRECRUITERS_COMPANIES = [

    "visa",
    "spotify",
    "klarna",
    "wolt",

    # Media / Journalism
    "bbc",
    "dw",
    "euronews",
]

ASHBY_COMPANIES = [

    "openai",
    "anthropic",
    "notion",
    "cursor",
    "scaleai",

    # AI / Research
    "perplexity",
    "character",
    "huggingface",
    "runway",
    "deepmind",
]

# =========================================================
# STEP 10 — DISPLAY NAMES
# =========================================================

COMPANY_DISPLAY_NAMES = {

    # Fintech
    "monzo": "Monzo",
    "wise": "Wise",
    "checkoutcom": "Checkout.com",
    "stripe": "Stripe",
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

    # Banks
    "barclays": "Barclays",
    "hsbc": "HSBC",
    "jpmorgan": "JPMorgan Chase",
    "goldmansachs": "Goldman Sachs",
    "morganstanley": "Morgan Stanley",
    "blackrock": "BlackRock",
    "natwestgroup": "NatWest Group",

    # AI / Tech
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "scaleai": "Scale AI",
    "huggingface": "Hugging Face",
    "deepmind": "Google DeepMind",

    # Media / Research
    "thomsonreuters": "Thomson Reuters",
    "voxmedia": "Vox Media",
    "businessinsider": "Business Insider",
    "washingtonpost": "The Washington Post",
    "newscientist": "New Scientist",
    "theinformation": "The Information",
    "deepl": "DeepL",
    "dw": "Deutsche Welle",
}

# =========================================================
# STEP 11 — PROCESS COMPANY
# =========================================================

def process_company(source_name, slug, scraper):

    try:

        jobs = scraper(slug)

        uk_jobs = [
            job for job in jobs
            if is_uk_location(job.get("location"))
        ]

        print(
            f"{slug} ({source_name}): "
            f"{len(uk_jobs)} UK jobs"
        )

        for job in uk_jobs:

            job["company"] = (
                COMPANY_DISPLAY_NAMES
                .get(slug, slug)
            )

        return uk_jobs

    except Exception as e:

        print(f"Processing error for {slug}: {e}")
        return []

# =========================================================
# STEP 12 — SCRAPE JOBS
# =========================================================

all_jobs = []

JOB_SOURCES = [

    ("Greenhouse", GREENHOUSE_COMPANIES, get_greenhouse_jobs),
    ("Lever", LEVER_COMPANIES, get_lever_jobs),
    ("SmartRecruiters", SMARTRECRUITERS_COMPANIES, get_smartrecruiters_jobs),
    ("Ashby", ASHBY_COMPANIES, get_ashby_jobs),
]

print("\nScraping jobs...\n")

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

    futures = []

    # Standard ATS systems

    for source_name, companies, scraper in JOB_SOURCES:

        for slug in companies:

            futures.append(
                executor.submit(
                    process_company,
                    source_name,
                    slug,
                    scraper
                )
            )

    # Workday

    for company, tenant in WORKDAY_COMPANIES:

        futures.append(
            executor.submit(
                lambda c=company, t=tenant: [
                    {
                        **job,
                        "company": COMPANY_DISPLAY_NAMES.get(c, c)
                    }
                    for job in get_workday_jobs(c, t)
                    if is_uk_location(job.get("location"))
                ]
            )
        )

    for future in as_completed(futures):

        try:

            result = future.result()

            if result:
                all_jobs.extend(result)

        except Exception as e:

            print(f"Future error: {e}")

print(f"\nTotal UK jobs found: {len(all_jobs)}")

# =========================================================
# STEP 13 — CREATE DATAFRAME
# =========================================================

jobs_df = pd.DataFrame(all_jobs)

if len(jobs_df) == 0:
    raise Exception("No jobs found.")

# =========================================================
# STEP 14 — NORMALIZE JOB COMPANIES
# =========================================================

jobs_df["company_clean"] = (
    jobs_df["company"]
    .astype(str)
    .apply(normalize_company)
)

# =========================================================
# STEP 15 — MATCH AGAINST SPONSOR LIST
# =========================================================

jobs_df["is_licensed_sponsor"] = (
    jobs_df["company_clean"]
    .isin(sponsor_set)
)

def fuzzy_sponsor_match(company_name, threshold=90):

    company_name = normalize_company(company_name)

    for sponsor in sponsor_set:

        score = fuzz.token_set_ratio(
            company_name,
            sponsor
        )

        if score >= threshold:
            return True

    return False

missing_matches = jobs_df[
    jobs_df["is_licensed_sponsor"] == False
]

if len(missing_matches) > 0:

    jobs_df.loc[
        jobs_df["is_licensed_sponsor"] == False,
        "is_licensed_sponsor"
    ] = missing_matches["company"].apply(
        fuzzy_sponsor_match
    )

# Keep sponsors only

jobs_df = jobs_df[
    jobs_df["is_licensed_sponsor"] == True
]

print(f"\nLicensed sponsor jobs: {len(jobs_df)}")

# =========================================================
# STEP 16 — ADD METADATA
# =========================================================

today = datetime.today().strftime("%Y-%m-%d")

jobs_df["visa_sponsorship_possible"] = True
jobs_df["scraped_date"] = today

# =========================================================
# STEP 17 — FINAL CLEANUP
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

jobs_df = jobs_df.drop_duplicates()

jobs_df = jobs_df.sort_values(
    by=["company", "job_title"]
)

# =========================================================
# STEP 18 — EXPORT CSV
# =========================================================

jobs_df.to_csv("jobs.csv", index=False)

print("\nSaved jobs.csv")

print(f"\nFinal total sponsored UK jobs: {len(jobs_df)}")

print("\nDone.")
