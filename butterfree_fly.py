#!/usr/bin/env python
# coding: utf-8

# =========================================================
# UK SPONSORED JOB FINDER
#
# Output:
#     jobs.csv
#
# Includes:
#     - Existing Greenhouse companies
#     - Existing Lever companies
#     - Existing Workday companies
#     - Existing SmartRecruiters companies
#     - Existing Ashby companies
#     - Care / healthcare job filtering
#     - GOV.UK sponsor-list verification
#
# =========================================================

import re
import time
import requests
import pandas as pd

from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urljoin
from rapidfuzz import fuzz
from concurrent.futures import ThreadPoolExecutor, as_completed


# =========================================================
# CONFIG
# =========================================================

MAX_WORKERS = 20

GOV_PAGE = (
    "https://www.gov.uk/government/publications/"
    "register-of-licensed-sponsors-workers"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,text/csv,*/*",
}

SPONSOR_FUZZY_THRESHOLD = 88


# =========================================================
# STEP 1 — DOWNLOAD LATEST GOV.UK SPONSOR LIST
# =========================================================

print("=" * 70)
print("STEP 1 — DOWNLOADING LATEST UK SPONSOR LIST")
print("=" * 70)

print("\nFinding latest sponsor list...")

response = requests.get(
    GOV_PAGE,
    headers=HEADERS,
    timeout=30
)

response.raise_for_status()

soup = BeautifulSoup(
    response.text,
    "html.parser"
)

csv_url = None


# ---------------------------------------------------------
# Find CSV link
# ---------------------------------------------------------

for link in soup.find_all("a", href=True):

    href = link["href"]
    text = link.get_text(" ", strip=True).lower()

    if ".csv" in href.lower():

        if (
            "sponsor" in text
            or "worker" in text
            or "temporary worker" in text
            or "register" in text
        ):
            csv_url = urljoin(
                GOV_PAGE,
                href
            )
            break


# ---------------------------------------------------------
# Fallback: any CSV
# ---------------------------------------------------------

if not csv_url:

    for link in soup.find_all("a", href=True):

        href = link["href"]

        if ".csv" in href.lower():

            csv_url = urljoin(
                GOV_PAGE,
                href
            )
            break


# ---------------------------------------------------------
# Stop if not found
# ---------------------------------------------------------

if not csv_url:

    print("\nLinks found on GOV.UK page:")

    for link in soup.find_all("a", href=True):

        print(
            link.get_text(" ", strip=True),
            "=>",
            link["href"]
        )

    raise Exception(
        "Could not find sponsor CSV URL"
    )


print("\nLatest sponsor CSV:")
print(csv_url)


# ---------------------------------------------------------
# Download CSV
# ---------------------------------------------------------

csv_response = requests.get(
    csv_url,
    headers=HEADERS,
    timeout=60
)

csv_response.raise_for_status()


with open(
    "uk_sponsor_list.csv",
    "wb"
) as f:

    f.write(
        csv_response.content
    )


# ---------------------------------------------------------
# Read CSV
# ---------------------------------------------------------

sponsors = pd.read_csv(
    "uk_sponsor_list.csv"
)


print(
    f"\nTotal sponsor rows: "
    f"{len(sponsors):,}"
)


print("\nSponsor columns:")
print(
    sponsors.columns.tolist()
)


# =========================================================
# STEP 2 — NORMALIZATION
# =========================================================

print("\n" + "=" * 70)
print("STEP 2 — NORMALIZATION")
print("=" * 70)


# ---------------------------------------------------------
# Find organisation-name column
# ---------------------------------------------------------

organisation_column = None

possible_company_columns = [
    "Organisation Name",
    "Organisation name",
    "Organisation",
    "organisation_name",
    "Company Name",
    "Company",
]


for column in possible_company_columns:

    if column in sponsors.columns:

        organisation_column = column
        break


if organisation_column is None:

    raise Exception(
        "Could not find organisation/company column "
        f"in sponsor CSV.\nColumns: {sponsors.columns.tolist()}"
    )


sponsors = sponsors.rename(
    columns={
        organisation_column: "company"
    }
)


# ---------------------------------------------------------
# Normalization function
# ---------------------------------------------------------

def normalize_company(name):

    name = str(name).lower()

    replacements = [
        ("&", "and"),
        (".", ""),
        (",", ""),
        ("-", " "),
        ("(", " "),
        (")", " "),
        ("/", " "),
    ]

    for old, new in replacements:
        name = name.replace(
            old,
            new
        )

    # Remove common legal/company words
    name = re.sub(
        r"\b(limited|ltd|llp|inc|corp|corporation|plc)\b",
        " ",
        name
    )

    # Remove duplicate whitespace
    name = re.sub(
        r"\s+",
        " ",
        name
    )

    return name.strip()


sponsors["company_clean"] = (
    sponsors["company"]
    .astype(str)
    .apply(normalize_company)
)


sponsor_set = set(
    sponsors["company_clean"]
    .dropna()
)


print(
    f"\nUnique normalized sponsors: "
    f"{len(sponsor_set):,}"
)


# =========================================================
# STEP 3 — CARE / HEALTHCARE SPONSOR DISCOVERY
# =========================================================

print("\n" + "=" * 70)
print("STEP 3 — FINDING POTENTIAL CARE / HEALTHCARE SPONSORS")
print("=" * 70)


CARE_PROVIDER_KEYWORDS = [

    # Care homes
    "care home",
    "care homes",
    "nursing home",
    "nursing homes",
    "residential care",
    "residential home",
    "residential homes",
    "care centre",
    "care center",

    # Care providers
    "healthcare",
    "health care",
    "social care",
    "home care",
    "domiciliary care",
    "supported living",
    "adult social care",
    "adult care",
    "elderly care",

    # Medical
    "hospital",
    "hospice",
    "clinic",
    "medical",
    "nursing",

    # Specialist care
    "dementia",
    "mental health",
    "community health",
    "community care",
    "rehabilitation",
]


CARE_EXCLUDE_KEYWORDS = [

    "careers",
    "career",
    "car care",
    "pet care",
    "animal care",
    "skin care",
    "hair care",
    "childcare products",
    "care insurance",
    "care recruitment",
]


def looks_like_care_provider(company_name):

    name = str(
        company_name
    ).lower().strip()

    # Exclude obvious false positives
    for word in CARE_EXCLUDE_KEYWORDS:

        if word in name:

            return False

    # Match provider keywords
    for keyword in CARE_PROVIDER_KEYWORDS:

        if keyword in name:

            return True

    return False


care_sponsors = sponsors[
    sponsors["company"].apply(
        looks_like_care_provider
    )
].copy()


print(
    f"\nPotential care/health sponsors found: "
    f"{len(care_sponsors):,}"
)


print("\nSample care/health sponsors:")

print(
    care_sponsors[
        ["company"]
    ]
    .head(50)
    .to_string(index=False)
)


# =========================================================
# STEP 4 — UK LOCATION FILTER
# =========================================================

print("\n" + "=" * 70)
print("STEP 4 — LOCATION FILTER")
print("=" * 70)


UK_PATTERNS = [

    # Countries
    "united kingdom",
    "great britain",
    "england",
    "scotland",
    "wales",
    "northern ireland",
    "uk",

    # Major cities
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

    # Other common locations
    "aberdeen",
    "bath",
    "brighton",
    "coventry",
    "derby",
    "exeter",
    "hull",
    "leicester",
    "lincoln",
    "norwich",
    "plymouth",
    "portsmouth",
    "salford",
    "st albans",
    "stoke",
    "sunderland",
    "wolverhampton",
    "york",

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
]


def is_uk_location(location):

    if location is None:
        return False

    if pd.isna(location):
        return False

    location = str(
        location
    ).lower().strip()

    if not location:
        return False

    # Block Australia first
    for blocked in AUSTRALIA_BLOCKLIST:

        if blocked in location:

            return False

    # UK
    for pattern in UK_PATTERNS:

        if pattern in location:

            return True

    return False


# =========================================================
# STEP 5 — CARE JOB KEYWORDS
# =========================================================

CARE_JOB_KEYWORDS = [

    # -----------------------------------------------------
    # Care
    # -----------------------------------------------------

    "care assistant",
    "care worker",
    "care support worker",
    "support worker",
    "senior care assistant",
    "senior carer",
    "carer",
    "health care assistant",
    "healthcare assistant",
    "health care support worker",
    "healthcare support worker",

    # -----------------------------------------------------
    # Nursing
    # -----------------------------------------------------

    "registered nurse",
    "staff nurse",
    "nurse",
    "nursing",
    "nurse associate",
    "clinical nurse",

    # -----------------------------------------------------
    # Management
    # -----------------------------------------------------

    "care coordinator",
    "care manager",
    "registered manager",
    "deputy manager",
    "home manager",
    "unit manager",
    "clinical manager",

    # -----------------------------------------------------
    # Social care
    # -----------------------------------------------------

    "social worker",
    "social care",
    "adult social care",
    "community support",
    "support practitioner",

    # -----------------------------------------------------
    # Specialist
    # -----------------------------------------------------

    "dementia",
    "rehabilitation",
    "occupational therapist",
    "physiotherapist",
    "speech therapist",
    "activities coordinator",

    # -----------------------------------------------------
    # Provider terms
    # -----------------------------------------------------

    "care home",
    "nursing home",
    "residential care",
]


def is_care_job(job_title):

    if not job_title:
        return False

    title = str(
        job_title
    ).lower().strip()

    return any(
        keyword in title
        for keyword in CARE_JOB_KEYWORDS
    )


# =========================================================
# STEP 6 — GREENHOUSE
# =========================================================

def get_greenhouse_jobs(company_slug):

    url = (
        "https://boards-api.greenhouse.io/v1/"
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

        for job in data.get(
            "jobs",
            []
        ):

            location_data = job.get(
                "location",
                {}
            )

            jobs.append({

                "company_slug":
                    company_slug,

                "job_title":
                    job.get("title"),

                "location":
                    location_data.get("name"),

                "job_url":
                    job.get("absolute_url"),

                "source":
                    "Greenhouse",
            })

        return jobs

    except Exception as e:

        print(
            f"Greenhouse error "
            f"{company_slug}: {e}"
        )

        return []


# =========================================================
# STEP 7 — LEVER
# =========================================================

def get_lever_jobs(company_slug):

    url = (
        "https://api.lever.co/v0/postings/"
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

                "company_slug":
                    company_slug,

                "job_title":
                    job.get("text"),

                "location":
                    job.get(
                        "categories",
                        {}
                    ).get("location"),

                "job_url":
                    job.get("hostedUrl"),

                "source":
                    "Lever",
            })

        return jobs

    except Exception as e:

        print(
            f"Lever error "
            f"{company_slug}: {e}"
        )

        return []


# =========================================================
# STEP 8 — WORKDAY
# =========================================================

def get_workday_jobs(
    company,
    tenant
):

    url = (
        f"https://{company}.wd1.myworkdayjobs.com"
        f"/wday/cxs/{company}/{tenant}/jobs"
    )

    payload = {
        "limit": 100,
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

            postings = data.get(
                "jobPostings",
                []
            )

            if not postings:
                break

            for job in postings:

                external_path = (
                    job.get(
                        "externalPath"
                    )
                    or ""
                )

                jobs.append({

                    "company_slug":
                        company,

                    "job_title":
                        job.get("title"),

                    "location":
                        job.get(
                            "locationsText"
                        ),

                    "job_url":
                        (
                            f"https://{company}"
                            f".wd1.myworkdayjobs.com"
                            f"/en-US/{tenant}"
                            f"{external_path}"
                        ),

                    "source":
                        "Workday",
                })

            payload["offset"] += payload["limit"]

            # Safety limit
            if payload["offset"] > 5000:
                break

        return jobs

    except Exception as e:

        print(
            f"Workday error "
            f"{company}: {e}"
        )

        return []


# =========================================================
# STEP 9 — SMARTRECRUITERS
# =========================================================

def get_smartrecruiters_jobs(
    company_slug
):

    url = (
        "https://api.smartrecruiters.com"
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

        for job in data.get(
            "content",
            []
        ):

            location_data = job.get(
                "location",
                {}
            )

            location = " ".join(
                [
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
                ]
            ).strip()

            jobs.append({

                "company_slug":
                    company_slug,

                "job_title":
                    job.get("name"),

                "location":
                    location,

                "job_url":
                    job.get("ref"),

                "source":
                    "SmartRecruiters",
            })

        return jobs

    except Exception as e:

        print(
            f"SmartRecruiters error "
            f"{company_slug}: {e}"
        )

        return []


# =========================================================
# STEP 10 — ASHBY
# =========================================================

def get_ashby_jobs(
    company_slug
):

    url = (
        "https://jobs.ashbyhq.com/api/"
        "non-user-graphql?op=ApiJobBoardWithTeams"
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

        r = requests.post(
            url,
            headers=HEADERS,
            json=payload,
            timeout=20
        )

        if r.status_code != 200:
            return []

        data = r.json()

        job_board = (
            data
            .get("data", {})
            .get("jobBoard", {})
        )

        jobs = []

        for job in job_board.get(
            "jobs",
            []
        ):

            jobs.append({

                "company_slug":
                    company_slug,

                "job_title":
                    job.get("title"),

                "location":
                    job.get("locationName"),

                "job_url":
                    job.get("absoluteUrl"),

                "source":
                    "Ashby",
            })

        return jobs

    except Exception as e:

        print(
            f"Ashby error "
            f"{company_slug}: {e}"
        )

        return []


# =========================================================
# STEP 11 — EXISTING COMPANY LISTS
# =========================================================

GREENHOUSE_COMPANIES = [

    # -----------------------------------------------------
    # FINTECH
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # TECH
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # CONSULTING
    # -----------------------------------------------------

    "mckinsey",
    "bcg",
    "bain",

    # -----------------------------------------------------
    # ENERGY
    # -----------------------------------------------------

    "octopusenergy",

    # -----------------------------------------------------
    # LOGISTICS
    # -----------------------------------------------------

    "deliveroo",
    "uber",

    # -----------------------------------------------------
    # MEDIA / RESEARCH
    # -----------------------------------------------------

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

    # Media / Research
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

    # Media
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
# STEP 12 — DISPLAY NAMES
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

    # AI
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "scaleai": "Scale AI",
    "huggingface": "Hugging Face",
    "deepmind": "Google DeepMind",

    # Media
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
# STEP 13 — PROCESS COMPANY
# =========================================================

def process_company(
    source_name,
    slug,
    scraper
):

    try:

        jobs = scraper(
            slug
        )

        uk_jobs = [

            job

            for job in jobs

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
                .get(
                    slug,
                    slug
                )
            )

        return uk_jobs

    except Exception as e:

        print(
            f"Processing error "
            f"{slug}: {e}"
        )

        return []


# =========================================================
# STEP 14 — SCRAPE JOBS
# =========================================================

print("\n" + "=" * 70)
print("STEP 14 — SCRAPING JOBS")
print("=" * 70)


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
]


with ThreadPoolExecutor(
    max_workers=MAX_WORKERS
) as executor:

    futures = []


    # -----------------------------------------------------
    # Standard ATS systems
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # Workday
    # -----------------------------------------------------

    for (
        company,
        tenant
    ) in WORKDAY_COMPANIES:

        def workday_task(
            c=company,
            t=tenant
        ):

            result = []

            jobs = get_workday_jobs(
                c,
                t
            )

            for job in jobs:

                if is_uk_location(
                    job.get("location")
                ):

                    job["company"] = (
                        COMPANY_DISPLAY_NAMES
                        .get(
                            c,
                            c
                        )
                    )

                    result.append(
                        job
                    )

            print(
                f"{c} (Workday): "
                f"{len(result)} UK jobs"
            )

            return result


        futures.append(
            executor.submit(
                workday_task
            )
        )


    # -----------------------------------------------------
    # Collect
    # -----------------------------------------------------

    for future in as_completed(
        futures
    ):

        try:

            result = future.result()

            if result:

                all_jobs.extend(
                    result
                )

        except Exception as e:

            print(
                f"Future error: {e}"
            )


print(
    f"\nTotal UK jobs found: "
    f"{len(all_jobs):,}"
)


# =========================================================
# STEP 15 — CREATE DATAFRAME
# =========================================================

if not all_jobs:

    raise Exception(
        "No UK jobs found."
    )


jobs_df = pd.DataFrame(
    all_jobs
)


# =========================================================
# STEP 16 — NORMALIZE JOB COMPANIES
# =========================================================

jobs_df["company_clean"] = (
    jobs_df["company"]
    .astype(str)
    .apply(normalize_company)
)


# =========================================================
# STEP 17 — CARE JOB FLAG
# =========================================================

jobs_df["is_care_job"] = (
    jobs_df["job_title"]
    .apply(is_care_job)
)


# =========================================================
# STEP 18 — SPONSOR MATCHING
# =========================================================

print("\n" + "=" * 70)
print("STEP 18 — MATCHING JOBS AGAINST GOV.UK SPONSOR LIST")
print("=" * 70)


# ---------------------------------------------------------
# Exact match
# ---------------------------------------------------------

jobs_df["is_licensed_sponsor"] = (
    jobs_df["company_clean"]
    .isin(sponsor_set)
)


print(
    "\nExact sponsor matches:",
    jobs_df["is_licensed_sponsor"].sum()
)


# ---------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------

def fuzzy_sponsor_match(
    company_name,
    threshold=SPONSOR_FUZZY_THRESHOLD
):

    company_name = normalize_company(
        company_name
    )

    if not company_name:

        return False


    # Exact
    if company_name in sponsor_set:

        return True


    # Fuzzy
    for sponsor in sponsor_set:

        score = fuzz.token_set_ratio(
            company_name,
            sponsor
        )

        if score >= threshold:

            return True


    return False


missing_mask = (
    jobs_df["is_licensed_sponsor"]
    == False
)


missing_count = missing_mask.sum()


print(
    f"Jobs requiring fuzzy matching: "
    f"{missing_count:,}"
)


if missing_count > 0:

    jobs_df.loc[
        missing_mask,
        "is_licensed_sponsor"
    ] = (
        jobs_df.loc[
            missing_mask,
            "company"
        ]
        .apply(
            fuzzy_sponsor_match
        )
    )


# =========================================================
# STEP 19 — KEEP LICENSED SPONSORS ONLY
# =========================================================

jobs_df = jobs_df[
    jobs_df["is_licensed_sponsor"]
    == True
].copy()


print(
    f"\nLicensed-sponsor jobs: "
    f"{len(jobs_df):,}"
)


# =========================================================
# STEP 20 — ADD METADATA
# =========================================================

today = datetime.today().strftime(
    "%Y-%m-%d"
)

jobs_df["visa_sponsorship_possible"] = True
jobs_df["scraped_date"] = today


# =========================================================
# STEP 21 — CLEAN URLS
# =========================================================

jobs_df["job_url"] = (
    jobs_df["job_url"]
    .fillna("")
    .astype(str)
)


# =========================================================
# STEP 22 — REMOVE DUPLICATES
# =========================================================

jobs_df = jobs_df.drop_duplicates(
    subset=[
        "company",
        "job_title",
        "location",
        "job_url",
    ]
).copy()


# =========================================================
# STEP 23 — COUNT CARE-HOME / CARE JOBS
# =========================================================
#
# is_care_job is used internally only.
# It will NOT be included in the CSV.
#
# Because this happens after sponsor filtering and
# duplicate removal, this is the number of care jobs
# actually added to the final CSV.
# =========================================================

care_home_jobs_added = int(
    jobs_df["is_care_job"].sum()
)


# =========================================================
# STEP 24 — SORT
# =========================================================

jobs_df = jobs_df.sort_values(
    by=[
        "is_care_job",
        "company",
        "job_title",
    ],
    ascending=[
        False,
        True,
        True,
    ]
)


# =========================================================
# STEP 25 — FINAL CSV COLUMNS
# =========================================================
#
# IMPORTANT:
# The CSV will contain ONLY these columns.
#
# company
# job_title
# location
# job_url
# source
# visa_sponsorship_possible
# scraped_date
#
# Internal columns such as:
# - is_care_job
# - is_licensed_sponsor
# - company_clean
#
# are deliberately excluded.
# =========================================================

final_columns = [

    "company",
    "job_title",
    "location",
    "job_url",
    "source",
    "visa_sponsorship_possible",
    "scraped_date",
]


jobs_df = jobs_df[
    final_columns
].copy()


# =========================================================
# STEP 26 — EXPORT SINGLE CSV
# =========================================================

OUTPUT_FILE = "jobs.csv"


jobs_df.to_csv(
    OUTPUT_FILE,
    index=False,
    encoding="utf-8-sig"
)


# =========================================================
# STEP 27 — SUMMARY
# =========================================================

print("\n" + "=" * 70)
print("FINISHED")
print("=" * 70)

print(
    f"\nSaved: {OUTPUT_FILE}"
)

print(
    f"Total jobs added: "
    f"{len(jobs_df):,}"
)

print(
    f"Care-home / care-related jobs added: "
    f"{care_home_jobs_added:,}"
)

print(
    f"Other jobs added: "
    f"{len(jobs_df) - care_home_jobs_added:,}"
)

print(
    f"Visa sponsorship possible: "
    f"{jobs_df['visa_sponsorship_possible'].sum():,}"
)

print(
    "\nJobs by source:"
)

print(
    jobs_df[
        "source"
    ]
    .value_counts()
    .to_string()
)

print(
    "\nCSV columns:"
)

print(
    jobs_df.columns.tolist()
)

print(
    "\nDone."
)

