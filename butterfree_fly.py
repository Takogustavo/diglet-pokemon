#!/usr/bin/env python
# coding: utf-8

# =========================================================
# IMPORTS
# =========================================================

import time
import random
import pandas as pd
import requests

from bs4 import BeautifulSoup
from datetime import datetime

from rapidfuzz import fuzz
from rapidfuzz.process import extractOne

from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed
)

# =========================================================
# CONFIG
# =========================================================

MAX_WORKERS = 20

REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 1.2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# =========================================================
# UTILITIES
# =========================================================

def safe_request_delay():
    time.sleep(
        random.uniform(
            REQUEST_DELAY_MIN,
            REQUEST_DELAY_MAX
        )
    )

def safe_get(url, timeout=20):

    safe_request_delay()

    return requests.get(
        url,
        headers=HEADERS,
        timeout=timeout
    )

def safe_post(url, payload, timeout=30):

    safe_request_delay()

    return requests.post(
        url,
        headers=HEADERS,
        json=payload,
        timeout=timeout
    )

# =========================================================
# STEP 1 — DOWNLOAD LATEST UK SPONSOR LIST
# =========================================================

GOV_PAGE = (
    "https://www.gov.uk/government/publications/"
    "register-of-licensed-sponsors-workers"
)

print("Finding latest sponsor list...")

response = safe_get(GOV_PAGE, timeout=30)

soup = BeautifulSoup(
    response.text,
    "html.parser"
)

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
    raise Exception(
        "Could not find sponsor CSV URL"
    )

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

    # Europe / EMEA
    "emea",
    "europe",
    "remote",
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
]

def is_uk_location(location):

    if not location:
        return False

    location = str(location).lower().strip()

    # Block Australia
    if any(
        blocked in location
        for blocked in AUSTRALIA_BLOCKLIST
    ):
        return False

    # Allow UK patterns
    return any(
        pattern in location
        for pattern in UK_PATTERNS
    )

# =========================================================
# GREENHOUSE
# =========================================================

def get_greenhouse_jobs(company_slug):

    url = (
        f"https://boards-api.greenhouse.io/v1/"
        f"boards/{company_slug}/jobs"
    )

    try:

        r = safe_get(url)

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
                "source": "Greenhouse",
                "ats": "Greenhouse"
            })

        return jobs

    except Exception as e:

        print(
            f"Greenhouse error "
            f"with {company_slug}: {e}"
        )

        return []

# =========================================================
# LEVER
# =========================================================

def get_lever_jobs(company_slug):

    url = (
        f"https://api.lever.co/v0/postings/"
        f"{company_slug}?mode=json"
    )

    try:

        r = safe_get(url)

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
                "source": "Lever",
                "ats": "Lever"
            })

        return jobs

    except Exception as e:

        print(
            f"Lever error "
            f"with {company_slug}: {e}"
        )

        return []

# =========================================================
# WORKDAY
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

            r = safe_post(url, payload)

            if r.status_code != 200:
                return jobs

            data = r.json()

            postings = data.get(
                "jobPostings",
                []
            )

            if not postings:
                break

            for job in postings:

                jobs.append({
                    "company_slug": company,
                    "job_title": job.get("title"),
                    "location": (
                        job.get("locationsText")
                    ),
                    "job_url": (
                        f"https://{company}"
                        f".wd1.myworkdayjobs.com"
                        f"/en-US/{tenant}"
                        f"{job.get('externalPath')}"
                    ),
                    "source": "Workday",
                    "ats": "Workday"
                })

            payload["offset"] += payload["limit"]

        return jobs

    except Exception as e:

        print(
            f"Workday error "
            f"with {company}: {e}"
        )

        return []

# =========================================================
# SMARTRECRUITERS
# =========================================================

def get_smartrecruiters_jobs(company_slug):

    url = (
        f"https://api.smartrecruiters.com"
        f"/v1/companies/{company_slug}/postings"
    )

    try:

        r = safe_get(url)

        if r.status_code != 200:
            return []

        data = r.json()

        jobs = []

        for job in data.get("content", []):

            location_data = job.get(
                "location",
                {}
            )

            location = " ".join([
                str(
                    location_data.get(
                        "city",
                        ""
                    )
                ),
                str(
                    location_data.get(
                        "country",
                        ""
                    )
                ),
            ])

            jobs.append({
                "company_slug": company_slug,
                "job_title": job.get("name"),
                "location": location,
                "job_url": job.get("ref"),
                "source": "SmartRecruiters",
                "ats": "SmartRecruiters"
            })

        return jobs

    except Exception as e:

        print(
            f"SmartRecruiters error "
            f"with {company_slug}: {e}"
        )

        return []

# =========================================================
# ASHBY
# =========================================================

def get_ashby_jobs(company_slug):

    url = (
        "https://jobs.ashbyhq.com/api/"
        "non-user-graphql"
        "?op=ApiJobBoardWithTeams"
    )

    payload = {
        "operationName":
        "ApiJobBoardWithTeams",

        "variables": {
            "organizationHostedJobsPageName":
            company_slug
        },

        "query": """
        query ApiJobBoardWithTeams(
          $organizationHostedJobsPageName: String!
        ) {
          jobBoard: jobBoardWithTeams(
            organizationHostedJobsPageName:
            $organizationHostedJobsPageName
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

        r = safe_post(url, payload)

        if r.status_code != 200:
            return []

        data = r.json()

        jobs = []

        for job in (
            data["data"]
            ["jobBoard"]
            ["jobs"]
        ):

            jobs.append({
                "company_slug": company_slug,
                "job_title": job.get("title"),
                "location": (
                    job.get("locationName")
                ),
                "job_url": (
                    job.get("absoluteUrl")
                ),
                "source": "Ashby",
                "ats": "Ashby"
            })

        return jobs

    except Exception as e:

        print(
            f"Ashby error "
            f"with {company_slug}: {e}"
        )

        return []

# =========================================================
# TEAMTAILOR
# =========================================================

def get_teamtailor_jobs(company_slug):

    url = (
        f"https://{company_slug}"
        f".teamtailor.com/api/jobs"
    )

    try:

        r = safe_get(url)

        if r.status_code != 200:
            return []

        data = r.json()

        jobs = []

        for job in data.get("data", []):

            attrs = job.get(
                "attributes",
                {}
            )

            jobs.append({
                "company_slug": company_slug,
                "job_title": (
                    attrs.get("title")
                ),
                "location": (
                    attrs.get("locations")
                ),
                "job_url": (
                    attrs.get("url")
                ),
                "source": "Teamtailor",
                "ats": "Teamtailor"
            })

        return jobs

    except Exception as e:

        print(
            f"Teamtailor error "
            f"with {company_slug}: {e}"
        )

        return []

# =========================================================
# ICIMS
# =========================================================

def get_icims_jobs(company_slug):

    url = (
        "https://jobs.icims.com/jobs/search"
        f"?ss=1&searchCompany={company_slug}"
    )

    try:

        r = safe_get(url)

        if r.status_code != 200:
            return []

        soup = BeautifulSoup(
            r.text,
            "html.parser"
        )

        jobs = []

        cards = soup.select(
            ".iCIMS_JobsTable .row"
        )

        for card in cards:

            title_el = card.select_one(
                ".title a"
            )

            loc_el = card.select_one(
                ".location"
            )

            if not title_el:
                continue

            jobs.append({
                "company_slug": company_slug,
                "job_title": (
                    title_el.text.strip()
                ),
                "location": (
                    loc_el.text.strip()
                    if loc_el else None
                ),
                "job_url": (
                    title_el["href"]
                ),
                "source": "iCIMS",
                "ats": "iCIMS"
            })

        return jobs

    except Exception as e:

        print(
            f"iCIMS error "
            f"with {company_slug}: {e}"
        )

        return []

# =========================================================
# COMPANY LISTS
# =========================================================

GREENHOUSE_COMPANIES = [

    # Fintech
    "monzo",
    "wise",
    "stripe",
    "checkoutcom",
    "revolut",
    "klarna",
    "plaid",
    "affirm",

    # AI
    "openai",
    "anthropic",
    "deepmind",
    "synthesia",
    "elevenlabs",

    # Tech
    "datadog",
    "mongodb",
    "cloudflare",
    "snyk",
    "figma",
    "gitlab",
    "elastic",
    "hashicorp",
    "notion",

    # Media
    "economist",
    "voxmedia",
    "buzzfeed",
    "businessinsider",
]

LEVER_COMPANIES = [

    "netflix",
    "shopify",
    "asana",
    "coinbase",
    "atlassian",
    "intercom",
    "zapier",
    "scaleai",
    "huggingface",
]

TEAMTAILOR_COMPANIES = [

    "octopus-energy",
    "ovoenergy",
    "synthesia",
    "elevenlabs",
    "graphcore",
    "gymshark",
]

SMARTRECRUITERS_COMPANIES = [

    "visa",
    "spotify",
    "klarna",
    "wolt",
    "bbc",
]

ASHBY_COMPANIES = [

    "openai",
    "anthropic",
    "cursor",
    "notion",
    "perplexity",
    "runway",
]

ICIMS_COMPANIES = [

    # Consulting
    "deloitte",
    "pwc",
    "ey",
    "kpmg",

    # Pharma
    "astrazeneca",
    "gsk",
    "pfizer",

    # Aerospace
    "airbus",
    "bae",
    "rollsroyce",

    # Retail
    "tesco",
    "ocado",
]

WORKDAY_COMPANIES = [

    # Banking
    ("barclays", "External_Career_Site"),
    ("hsbc", "HSBCCareers"),
    ("jpmorgan", "jpmc"),
    ("goldmansachs", "External"),
    ("morganstanley", "MorganStanleyCareers"),
    ("blackrock", "BlackRockCareers"),

    # Energy
    ("shell", "ShellCareers"),
    ("bp", "BP_Careers"),

    # Retail
    ("amazon", "AmazonJobs"),

    # Pharma
    ("astrazeneca", "ExternalCareerSite"),
]

# =========================================================
# DISPLAY NAMES
# =========================================================

COMPANY_DISPLAY_NAMES = {

    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "deepmind": "Google DeepMind",
    "scaleai": "Scale AI",
    "huggingface": "Hugging Face",

    "jpmorgan": "JPMorgan Chase",
    "goldmansachs": "Goldman Sachs",
    "morganstanley": "Morgan Stanley",

    "pwc": "PwC",
    "ey": "EY",
    "kpmg": "KPMG",

    "gsk": "GSK",
    "bae": "BAE Systems",
    "rollsroyce": "Rolls-Royce",
}

# =========================================================
# PROCESS COMPANY
# =========================================================

def process_company(
    source_name,
    slug,
    scraper
):

    try:

        jobs = scraper(slug)

        uk_jobs = [
            job for job in jobs
            if is_uk_location(
                job.get("location")
            )
        ]

        print(
            f"{slug} "
            f"({source_name}): "
            f"{len(uk_jobs)} UK jobs"
        )

        for job in uk_jobs:

            job["company"] = (
                COMPANY_DISPLAY_NAMES
                .get(slug, slug)
            )

        return uk_jobs

    except Exception as e:

        print(
            f"Processing error "
            f"for {slug}: {e}"
        )

        return []

# =========================================================
# SCRAPE JOBS
# =========================================================

all_jobs = []

JOB_SOURCES = [

    (
        "Greenhouse",
        GREENHOUSE_COMPANIES,
        get_greenhouse_jobs
    ),

    (
        "Lever",
        LEVER_COMPANIES,
        get_lever_jobs
    ),

    (
        "SmartRecruiters",
        SMARTRECRUITERS_COMPANIES,
        get_smartrecruiters_jobs
    ),

    (
        "Ashby",
        ASHBY_COMPANIES,
        get_ashby_jobs
    ),

    (
        "Teamtailor",
        TEAMTAILOR_COMPANIES,
        get_teamtailor_jobs
    ),

    (
        "iCIMS",
        ICIMS_COMPANIES,
        get_icims_jobs
    ),
]

print("\nScraping jobs...\n")

with ThreadPoolExecutor(
    max_workers=MAX_WORKERS
) as executor:

    futures = []

    # Standard ATS

    for (
        source_name,
        companies,
        scraper
    ) in JOB_SOURCES:

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

    for (
        company,
        tenant
    ) in WORKDAY_COMPANIES:

        futures.append(

            executor.submit(

                lambda c=company, t=tenant: [

                    {
                        **job,

                        "company":
                        COMPANY_DISPLAY_NAMES.get(
                            c,
                            c
                        )
                    }

                    for job in get_workday_jobs(c, t)

                    if is_uk_location(
                        job.get("location")
                    )
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

print(
    f"\nTotal UK jobs found: "
    f"{len(all_jobs)}"
)

# =========================================================
# CREATE DATAFRAME
# =========================================================

jobs_df = pd.DataFrame(all_jobs)

if len(jobs_df) == 0:
    raise Exception("No jobs found.")

# =========================================================
# NORMALIZE COMPANIES
# =========================================================

jobs_df["company_clean"] = (

    jobs_df["company"]
    .astype(str)
    .apply(normalize_company)
)

# =========================================================
# SPONSOR MATCHING
# =========================================================

jobs_df["is_licensed_sponsor"] = (

    jobs_df["company_clean"]
    .isin(sponsor_set)
)

def fuzzy_sponsor_match(
    company_name,
    threshold=90
):

    company_name = normalize_company(
        company_name
    )

    result = extractOne(

        company_name,
        sponsor_set,

        scorer=fuzz.token_sort_ratio
    )

    if not result:
        return False

    match, score, _ = result

    return score >= threshold

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

# =========================================================
# KEEP SPONSORS ONLY
# =========================================================

jobs_df = jobs_df[
    jobs_df["is_licensed_sponsor"] == True
]

print(
    f"\nLicensed sponsor jobs: "
    f"{len(jobs_df)}"
)

# =========================================================
# METADATA
# =========================================================

today = datetime.today().strftime(
    "%Y-%m-%d"
)

jobs_df[
    "visa_sponsorship_possible"
] = True

jobs_df["scraped_date"] = today

# =========================================================
# FINAL CLEANUP
# =========================================================

jobs_df = jobs_df[[

    "company",
    "job_title",
    "location",
    "job_url",
    "source",
    "ats",
    "visa_sponsorship_possible",
    "scraped_date"
]]

jobs_df = jobs_df.drop_duplicates()

jobs_df = jobs_df.sort_values(
    by=["company", "job_title"]
)

# =========================================================
# EXPORT
# =========================================================

jobs_df.to_csv(
    "jobs.csv",
    index=False
)

print("\nSaved jobs.csv")

print(
    f"\nFinal sponsored UK jobs: "
    f"{len(jobs_df)}"
)

print("\nDone.")
