#!/usr/bin/env python
# coding: utf-8

# =========================================================
# UK SPONSORED JOB FINDER
#
# Output:
#     jobs.csv
#
# CSV COLUMNS:
#     company
#     job_title
#     location
#     job_url
#     source
#     visa_sponsorship_possible
#     scraped_date
#
# SOURCES:
#     - Greenhouse
#     - Lever
#     - Workday
#     - SmartRecruiters
#     - Ashby
#     - Barchester Healthcare
#     - HC-One
#     - Care UK
#
# FILTERING:
#     - UK jobs only
#     - Licensed Worker sponsors only
#     - Care jobs tracked internally
#     - Care-home jobs counted separately
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

REQUEST_TIMEOUT = 30

GOV_PAGE = (
    "https://www.gov.uk/government/publications/"
    "register-of-licensed-sponsors-workers"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/json,text/csv,*/*"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

SPONSOR_FUZZY_THRESHOLD = 88

OUTPUT_FILE = "jobs.csv"

SPONSOR_CSV_FILE = "uk_sponsor_list.csv"


# =========================================================
# SESSION
# =========================================================

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# =========================================================
# STEP 1 — DOWNLOAD LATEST GOV.UK SPONSOR LIST
# =========================================================

print("=" * 70)
print("STEP 1 — DOWNLOADING LATEST UK SPONSOR LIST")
print("=" * 70)

print("\nFinding latest sponsor list...")

response = SESSION.get(
    GOV_PAGE,
    timeout=REQUEST_TIMEOUT
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
    text = link.get_text(
        " ",
        strip=True
    ).lower()

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

    for link in soup.find_all(
        "a",
        href=True
    ):

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

    for link in soup.find_all(
        "a",
        href=True
    ):

        print(
            link.get_text(
                " ",
                strip=True
            ),
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

csv_response = SESSION.get(
    csv_url,
    timeout=60
)

csv_response.raise_for_status()


with open(
    SPONSOR_CSV_FILE,
    "wb"
) as f:

    f.write(
        csv_response.content
    )


# ---------------------------------------------------------
# Read CSV
# ---------------------------------------------------------

sponsors = pd.read_csv(
    SPONSOR_CSV_FILE
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
# STEP 2 — SPONSOR NORMALIZATION
# =========================================================

print("\n" + "=" * 70)
print("STEP 2 — NORMALIZING SPONSOR LIST")
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
        "in sponsor CSV.\n"
        f"Columns: {sponsors.columns.tolist()}"
    )


sponsors = sponsors.rename(
    columns={
        organisation_column: "company"
    }
)


# ---------------------------------------------------------
# Keep Worker sponsors where possible
# ---------------------------------------------------------

if "Route" in sponsors.columns:

    sponsors = sponsors[
        sponsors["Route"]
        .astype(str)
        .str.contains(
            "Skilled Worker|Worker",
            case=False,
            na=False,
            regex=True
        )
    ].copy()


# ---------------------------------------------------------
# Normalization function
# ---------------------------------------------------------

def normalize_company(name):

    if name is None:
        return ""

    if pd.isna(name):
        return ""

    name = str(name).lower().strip()

    replacements = [

        ("&", "and"),
        (".", ""),
        (",", ""),
        ("-", " "),
        ("(", " "),
        (")", " "),
        ("/", " "),
        ("'", ""),
        ("’", ""),
    ]

    for old, new in replacements:

        name = name.replace(
            old,
            new
        )


    # -----------------------------------------------------
    # Remove common legal/company words
    # -----------------------------------------------------

    name = re.sub(
        r"\b("
        r"limited|ltd|llp|inc|incorporated|"
        r"corp|corporation|plc|company|co"
        r")\b",
        " ",
        name
    )


    # -----------------------------------------------------
    # Remove duplicate whitespace
    # -----------------------------------------------------

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
# STEP 3 — CARE PROVIDER DETECTION
# =========================================================

print("\n" + "=" * 70)
print("STEP 3 — IDENTIFYING CARE / HEALTHCARE SPONSORS")
print("=" * 70)


CARE_PROVIDER_KEYWORDS = [

    "care home",
    "care homes",
    "nursing home",
    "nursing homes",
    "residential care",
    "residential home",
    "residential homes",
    "care centre",
    "care center",

    "healthcare",
    "health care",
    "social care",
    "home care",
    "domiciliary care",
    "supported living",
    "adult social care",
    "adult care",
    "elderly care",

    "hospital",
    "hospice",
    "clinic",
    "medical",
    "nursing",

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


def looks_like_care_provider(
    company_name
):

    name = str(
        company_name
    ).lower().strip()


    for word in CARE_EXCLUDE_KEYWORDS:

        if word in name:

            return False


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


if len(care_sponsors) > 0:

    print(
        "\nSample care/health sponsors:"
    )

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
print("STEP 4 — UK LOCATION FILTER")
print("=" * 70)


# ---------------------------------------------------------
# UK countries
# ---------------------------------------------------------

UK_COUNTRY_PATTERNS = [

    "united kingdom",
    "great britain",
    "england",
    "scotland",
    "wales",
    "northern ireland",
    "uk",
    "gb",
    "gbr",
]


# ---------------------------------------------------------
# UK cities
# ---------------------------------------------------------

UK_CITY_PATTERNS = [

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
    "stoke-on-trent",
    "stoke on trent",
    "sunderland",
    "wolverhampton",
    "york",

    "bournemouth",
    "bradford",
    "blackpool",
    "bolton",
    "burnley",
    "chester",
    "croydon",
    "darlington",
    "doncaster",
    "dundee",
    "durham",
    "guildford",
    "harrogate",
    "hastings",
    "ipswich",
    "luton",
    "maidstone",
    "middlesbrough",
    "northampton",
    "peterborough",
    "poole",
    "preston",
    "romford",
    "shrewsbury",
    "slough",
    "stockport",
    "swindon",
    "telford",
    "warrington",
    "watford",
    "wigan",
    "worcester",
    "worthing",
]


# ---------------------------------------------------------
# Non-UK locations
# ---------------------------------------------------------

NON_UK_PATTERNS = [

    "united states",
    "united states of america",
    "u.s.a",
    "usa",
    "us",

    "canada",

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

    "new zealand",

    "ireland",
    "republic of ireland",
    "dublin",

    "france",
    "germany",
    "spain",
    "italy",
    "netherlands",
    "amsterdam",

    "india",

    "singapore",

    "hong kong",

    "japan",

    "china",

    "south africa",
]


# ---------------------------------------------------------
# UK postcode
# ---------------------------------------------------------

UK_POSTCODE_PATTERN = re.compile(
    r"\b("
    r"[A-Z]{1,2}\d[A-Z\d]?"
    r"\s*\d[A-Z]{2}"
    r")\b",
    re.IGNORECASE
)


def is_uk_location(
    location
):

    if location is None:
        return False

    if pd.isna(location):
        return False

    location = str(
        location
    ).strip().lower()


    if not location:
        return False


    # -----------------------------------------------------
    # Reject obvious non-UK locations first
    # -----------------------------------------------------

    for pattern in NON_UK_PATTERNS:

        if pattern in [
            "usa",
            "us",
            "u.s.a",
        ]:

            if re.search(
                rf"\b{re.escape(pattern)}\b",
                location
            ):

                return False

        else:

            if re.search(
                rf"\b{re.escape(pattern)}\b",
                location
            ):

                return False


    # -----------------------------------------------------
    # UK postcode
    # -----------------------------------------------------

    if UK_POSTCODE_PATTERN.search(
        location
    ):

        return True


    # -----------------------------------------------------
    # UK country
    # -----------------------------------------------------

    for pattern in UK_COUNTRY_PATTERNS:

        if re.search(
            rf"\b{re.escape(pattern)}\b",
            location
        ):

            return True


    # -----------------------------------------------------
    # UK cities
    # -----------------------------------------------------

    for pattern in UK_CITY_PATTERNS:

        if re.search(
            rf"\b{re.escape(pattern)}\b",
            location
        ):

            return True


    # -----------------------------------------------------
    # Remote UK
    # -----------------------------------------------------

    remote_patterns = [

        r"\bremote\s*,?\s*uk\b",
        r"\buk\s*,?\s*remote\b",
        r"\bremote\s*-\s*uk\b",
        r"\bremote\s*-\s*united kingdom\b",
        r"\bunited kingdom\s*-\s*remote\b",
        r"\buk\s*remote\b",
        r"\bremote\s+within\s+the\s+uk\b",
    ]


    for pattern in remote_patterns:

        if re.search(
            pattern,
            location
        ):

            return True


    return False


# =========================================================
# STEP 5 — CARE JOB KEYWORDS
# =========================================================

CARE_JOB_KEYWORDS = [

    # -----------------------------------------------------
    # Direct care
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
    "registered general nurse",
    "staff nurse",
    "nurse",
    "nursing",
    "nurse associate",
    "clinical nurse",
    "rgn",
    "rmn",

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
    "activities co-ordinator",

    # -----------------------------------------------------
    # Care-home-specific
    # -----------------------------------------------------

    "care home",
    "care homes",
    "nursing home",
    "nursing homes",
    "residential care",
    "residential home",

    # -----------------------------------------------------
    # Care-home support
    # -----------------------------------------------------

    "housekeeper",
    "housekeeping",
    "kitchen assistant",
    "catering assistant",
    "chef",
    "cook",
    "domestic",
    "maintenance operative",
    "home administrator",
    "administrator",
    "activities",
]


def is_care_job(
    job_title
):

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
# STEP 6 — CARE-HOME PROVIDER SPONSOR ALIASES
# =========================================================

CARE_SPONSOR_ALIASES = {

    "Barchester Healthcare": [

        "barchester healthcare",
        "barchester healthcare limited",
    ],

    "HC-One": [

        "hc one",
        "hc-one",
        "hc-one limited",
        "hc one limited",
        "hc one no1 limited",
        "hc one no2 limited",
        "hc one no3 limited",
        "hc one no4 limited",
        "hc one no5 limited",
        "hc one no6 limited",
        "hc one management limited",
    ],

    "Care UK": [

        "care uk",
        "care uk limited",
        "care uk healthcare",
        "care uk healthcare group",
    ],
}


def care_provider_is_sponsor(
    provider_name
):

    aliases = CARE_SPONSOR_ALIASES.get(
        provider_name,
        []
    )


    for alias in aliases:

        alias_clean = normalize_company(
            alias
        )

        if alias_clean in sponsor_set:

            return True


    # -----------------------------------------------------
    # Fuzzy fallback
    # -----------------------------------------------------

    provider_clean = normalize_company(
        provider_name
    )


    for sponsor in sponsor_set:

        score = fuzz.token_set_ratio(
            provider_clean,
            sponsor
        )

        if score >= 88:

            return True


    return False


# =========================================================
# STEP 7 — GREENHOUSE
# =========================================================

def get_greenhouse_jobs(
    company_slug
):

    url = (
        "https://boards-api.greenhouse.io/v1/"
        f"boards/{company_slug}/jobs"
    )


    try:

        r = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT
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
# STEP 8 — LEVER
# =========================================================

def get_lever_jobs(
    company_slug
):

    url = (
        "https://api.lever.co/v0/postings/"
        f"{company_slug}?mode=json"
    )


    try:

        r = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT
        )


        if r.status_code != 200:

            return []


        data = r.json()

        jobs = []


        for job in data:

            categories = job.get(
                "categories",
                {}
            )


            jobs.append({

                "company_slug":
                    company_slug,

                "job_title":
                    job.get("text"),

                "location":
                    categories.get(
                        "location"
                    ),

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
# STEP 9 — WORKDAY
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
        "searchText": "",
    }


    jobs = []


    try:

        while True:

            r = SESSION.post(
                url,
                headers=HEADERS,
                json=payload,
                timeout=REQUEST_TIMEOUT
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


            payload["offset"] += (
                payload["limit"]
            )


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
# STEP 10 — SMARTRECRUITERS
# =========================================================

def get_smartrecruiters_jobs(
    company_slug
):

    url = (
        "https://api.smartrecruiters.com"
        f"/v1/companies/{company_slug}/postings"
    )


    try:

        r = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT
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


            job_url = job.get(
                "ref"
            )


            # -------------------------------------------------
            # SmartRecruiters sometimes returns a reference
            # rather than a complete URL.
            # -------------------------------------------------

            if job_url and not str(
                job_url
            ).startswith("http"):

                job_url = (
                    f"https://jobs.smartrecruiters.com/"
                    f"{company_slug}/{job_url}"
                )


            jobs.append({

                "company_slug":
                    company_slug,

                "job_title":
                    job.get("name"),

                "location":
                    location,

                "job_url":
                    job_url,

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
# STEP 11 — ASHBY
# =========================================================

def get_ashby_jobs(
    company_slug
):

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

        r = SESSION.post(
            url,
            headers=HEADERS,
            json=payload,
            timeout=REQUEST_TIMEOUT
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
# STEP 12 — BARCHESTER HEALTHCARE
# =========================================================
#
# Barchester has its own careers website with searchable
# vacancies.
#
# Current career site:
# https://jobs.barchester.com/search
#
# =========================================================

BARCHester_BASE_URL = (
    "https://jobs.barchester.com"
)

BARCHester_SEARCH_URL = (
    "https://jobs.barchester.com/search"
)


def extract_postcode(
    text
):

    if not text:

        return None


    match = UK_POSTCODE_PATTERN.search(
        str(text)
    )


    if match:

        return match.group(
            1
        ).upper()


    return None


def clean_location_text(
    text
):

    if not text:

        return ""


    text = str(text)

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()


    postcode = extract_postcode(
        text
    )


    if postcode:

        # Try to keep text around the postcode
        parts = text.split(
            postcode,
            1
        )

        before = parts[0].strip()

        before = re.sub(
            r"^\([^)]*\)\s*",
            "",
            before
        )

        return (
            f"{before}, {postcode}"
            if before
            else postcode
        )


    return text


def get_barchester_jobs():

    jobs = []

    seen_urls = set()

    max_pages = 20


    for page in range(
        1,
        max_pages + 1
    ):

        params = {

            "orderBy": 1,
            "page": page,
        }


        try:

            r = SESSION.get(
                BARCHester_SEARCH_URL,
                params=params,
                timeout=REQUEST_TIMEOUT
            )


            if r.status_code != 200:

                print(
                    "Barchester HTTP status:",
                    r.status_code
                )

                break


            soup = BeautifulSoup(
                r.text,
                "html.parser"
            )


            # -------------------------------------------------
            # Barchester job links
            # -------------------------------------------------

            found_on_page = 0


            for heading in soup.find_all(
                ["h2", "h3", "h4"]
            ):

                anchor = heading.find(
                    "a",
                    href=True
                )


                if not anchor:

                    continue


                title = anchor.get_text(
                    " ",
                    strip=True
                )


                href = anchor.get(
                    "href"
                )


                if not title or not href:

                    continue


                job_url = urljoin(
                    BARCHester_BASE_URL,
                    href
                )


                if job_url in seen_urls:

                    continue


                # -------------------------------------------------
                # Ignore navigation links
                # -------------------------------------------------

                title_lower = title.lower()


                if title_lower in [
                    "careers",
                    "search",
                    "home",
                    "login",
                    "register",
                ]:

                    continue


                # -------------------------------------------------
                # Find surrounding job card
                # -------------------------------------------------

                card = heading


                for _ in range(5):

                    if card.parent:

                        card = card.parent


                    card_text = card.get_text(
                        " ",
                        strip=True
                    )


                    if (
                        extract_postcode(
                            card_text
                        )
                        or
                        "Posted:" in card_text
                        or
                        "Expires:" in card_text
                    ):

                        break


                card_text = card.get_text(
                    " ",
                    strip=True
                )


                # -------------------------------------------------
                # Barchester job locations usually appear before
                # pay/type/posted information.
                # -------------------------------------------------

                location = ""


                postcode = extract_postcode(
                    card_text
                )


                if postcode:

                    # Take the sentence/portion before the postcode
                    index = card_text.lower().find(
                        postcode.lower()
                    )

                    if index >= 0:

                        before = card_text[
                            :index + len(postcode)
                        ]

                        # Remove title from the beginning
                        before = before.replace(
                            title,
                            "",
                            1
                        ).strip()


                        location = clean_location_text(
                            before
                        )


                # -------------------------------------------------
                # Fallback
                # -------------------------------------------------

                if not location:

                    location = "UK"


                # -------------------------------------------------
                # Only care-related Barchester jobs
                # -------------------------------------------------

                if not is_care_job(
                    title
                ):

                    continue


                if not is_uk_location(
                    location
                ):

                    # Barchester is UK-focused, but still enforce
                    # the same location rule.
                    if location != "UK":

                        continue


                seen_urls.add(
                    job_url
                )


                jobs.append({

                    "company":
                        "Barchester Healthcare",

                    "job_title":
                        title,

                    "location":
                        location,

                    "job_url":
                        job_url,

                    "source":
                        "Barchester Healthcare",

                    "is_care_job":
                        True,

                    "is_care_home_job":
                        True,
                })


                found_on_page += 1


            print(
                f"Barchester page {page}: "
                f"{found_on_page} care jobs"
            )


            # -------------------------------------------------
            # Stop if page has no jobs
            # -------------------------------------------------

            if found_on_page == 0:

                # There may still be pages with no matching
                # care jobs, so inspect pagination before stopping.
                page_text = soup.get_text(
                    " ",
                    strip=True
                ).lower()


                if (
                    "next" not in page_text
                    and page > 1
                ):

                    break


        except Exception as e:

            print(
                f"Barchester error page "
                f"{page}: {e}"
            )

            break


        time.sleep(
            0.25
        )


    return jobs


# =========================================================
# STEP 13 — HC-ONE
# =========================================================
#
# HC-One uses Eploy.
#
# =========================================================

HC_ONE_BASE_URL = (
    "https://apply.hc-one.co.uk"
)

HC_ONE_SEARCH_URL = (
    "https://apply.hc-one.co.uk/"
    "vacancies/vacancy-search-results.aspx"
)


def get_hcone_jobs():

    jobs = []

    seen_urls = set()

    max_pages = 30


    for page in range(
        1,
        max_pages + 1
    ):

        params = {

            "view": "list",
            "page": page,
        }


        try:

            r = SESSION.get(
                HC_ONE_SEARCH_URL,
                params=params,
                timeout=REQUEST_TIMEOUT
            )


            if r.status_code != 200:

                print(
                    "HC-One HTTP status:",
                    r.status_code
                )

                break


            soup = BeautifulSoup(
                r.text,
                "html.parser"
            )


            found_on_page = 0


            # -------------------------------------------------
            # Job headings are generally h2 links.
            # -------------------------------------------------

            headings = soup.find_all(
                ["h2", "h3"]
            )


            for heading in headings:

                anchor = heading.find(
                    "a",
                    href=True
                )


                if not anchor:

                    continue


                title = anchor.get_text(
                    " ",
                    strip=True
                )


                href = anchor.get(
                    "href"
                )


                if not title or not href:

                    continue


                title_lower = title.lower()


                # Ignore navigation
                if title_lower in [
                    "register",
                    "login",
                    "search jobs",
                    "more info",
                    "apply",
                ]:

                    continue


                job_url = urljoin(
                    HC_ONE_BASE_URL,
                    href
                )


                if job_url in seen_urls:

                    continue


                # -------------------------------------------------
                # Find surrounding job card.
                # -------------------------------------------------

                card = heading


                for _ in range(6):

                    if card.parent:

                        card = card.parent


                    card_text = card.get_text(
                        " ",
                        strip=True
                    )


                    if (
                        "All Locations:" in card_text
                        or
                        "Home/Department:" in card_text
                        or
                        "Job Family:" in card_text
                    ):

                        break


                card_text = card.get_text(
                    " ",
                    strip=True
                )


                # -------------------------------------------------
                # Extract location.
                # -------------------------------------------------

                location = ""


                location_match = re.search(
                    r"All Locations:\s*(.*?)(?:"
                    r"All Departments:"
                    r"|Job Family:"
                    r"|Salary Details:"
                    r"|£"
                    r")",
                    card_text,
                    re.IGNORECASE
                )


                if location_match:

                    location = location_match.group(
                        1
                    ).strip()


                # -------------------------------------------------
                # Fallback: postcode
                # -------------------------------------------------

                if not location:

                    postcode = extract_postcode(
                        card_text
                    )


                    if postcode:

                        location = postcode


                if not location:

                    location = "UK"


                # -------------------------------------------------
                # Only care-home/care jobs
                # -------------------------------------------------

                if not is_care_job(
                    title
                ):

                    # Some HC-One jobs have generic titles,
                    # so inspect the card text too.
                    if not any(
                        keyword in card_text.lower()
                        for keyword in [
                            "care home",
                            "nursing home",
                            "residential",
                            "dementia",
                            "resident",
                            "carer",
                        ]
                    ):

                        continue


                if not is_uk_location(
                    location
                ):

                    continue


                seen_urls.add(
                    job_url
                )


                jobs.append({

                    "company":
                        "HC-One",

                    "job_title":
                        title,

                    "location":
                        location,

                    "job_url":
                        job_url,

                    "source":
                        "HC-One",

                    "is_care_job":
                        True,

                    "is_care_home_job":
                        True,
                })


                found_on_page += 1


            print(
                f"HC-One page {page}: "
                f"{found_on_page} care jobs"
            )


            # -------------------------------------------------
            # Detect pagination
            # -------------------------------------------------

            next_exists = False


            for anchor in soup.find_all(
                "a",
                href=True
            ):

                anchor_text = anchor.get_text(
                    " ",
                    strip=True
                ).lower()


                if anchor_text in [
                    "next",
                    "next >",
                    ">",
                ]:

                    next_exists = True

                    break


            if (
                not next_exists
                and page > 1
            ):

                break


            # Avoid endless looping if page parameter
            # is ignored by the site.
            if found_on_page == 0:

                break


        except Exception as e:

            print(
                f"HC-One error page "
                f"{page}: {e}"
            )

            break


        time.sleep(
            0.25
        )


    return jobs


# =========================================================
# STEP 14 — CARE UK
# =========================================================
#
# Care UK has a dedicated careers/vacancies page.
#
# =========================================================

CARE_UK_BASE_URL = (
    "https://www.careuk.com"
)

CARE_UK_SEARCH_URL = (
    "https://www.careuk.com/careers/vacancies"
)


def get_careuk_jobs():

    jobs = []

    seen_urls = set()


    try:

        r = SESSION.get(
            CARE_UK_SEARCH_URL,
            timeout=REQUEST_TIMEOUT
        )


        if r.status_code != 200:

            print(
                "Care UK HTTP status:",
                r.status_code
            )

            return []


        soup = BeautifulSoup(
            r.text,
            "html.parser"
        )


        # -----------------------------------------------------
        # Care UK job application links
        # -----------------------------------------------------

        for anchor in soup.find_all(
            "a",
            href=True
        ):

            href = anchor.get(
                "href"
            )


            title = anchor.get_text(
                " ",
                strip=True
            )


            if not href or not title:

                continue


            href_lower = href.lower()


            # Care UK applications are normally hosted on
            # apply.careuk.com.
            if (
                "apply.careuk.com"
                not in href_lower
            ):

                continue


            title_lower = title.lower()


            if title_lower in [
                "apply now",
                "find out more",
                "read more",
                "login",
            ]:

                continue


            job_url = urljoin(
                CARE_UK_BASE_URL,
                href
            )


            if job_url in seen_urls:

                continue


            # -------------------------------------------------
            # Find surrounding job card.
            # -------------------------------------------------

            card = anchor


            for _ in range(7):

                if card.parent:

                    card = card.parent


                card_text = card.get_text(
                    " ",
                    strip=True
                )


                if (
                    "Care Home Based" in card_text
                    or
                    "£" in card_text
                    or
                    "Days" in card_text
                    or
                    "Nights" in card_text
                ):

                    break


            card_text = card.get_text(
                " ",
                strip=True
            )


            # -------------------------------------------------
            # Location
            # -------------------------------------------------

            postcode = extract_postcode(
                card_text
            )


            location = ""


            if postcode:

                location = postcode


            # -------------------------------------------------
            # Look for known UK city names
            # -------------------------------------------------

            if not location:

                lower_card = card_text.lower()


                for city in UK_CITY_PATTERNS:

                    if re.search(
                        rf"\b{re.escape(city)}\b",
                        lower_card
                    ):

                        location = city.title()

                        break


            if not location:

                location = "UK"


            # -------------------------------------------------
            # Care UK is a dedicated care-home source.
            # Include care-home support jobs as well.
            # -------------------------------------------------

            if not is_care_job(
                title
            ):

                if not any(
                    keyword in card_text.lower()
                    for keyword in [
                        "care home",
                        "resident",
                        "care assistant",
                        "care team",
                        "nursing",
                        "home manager",
                        "clinical lead",
                        "housekeeper",
                        "catering",
                    ]
                ):

                    continue


            if location != "UK":

                if not is_uk_location(
                    location
                ):

                    continue


            seen_urls.add(
                job_url
            )


            jobs.append({

                "company":
                    "Care UK",

                "job_title":
                    title,

                "location":
                    location,

                "job_url":
                    job_url,

                "source":
                    "Care UK",

                "is_care_job":
                    True,

                "is_care_home_job":
                    True,
            })


        print(
            f"Care UK: "
            f"{len(jobs)} care jobs found"
        )


        return jobs


    except Exception as e:

        print(
            f"Care UK error: {e}"
        )

        return []


# =========================================================
# STEP 15 — EXISTING COMPANY LISTS
# =========================================================

GREENHOUSE_COMPANIES = [

    # Fintech
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

    # Tech
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

    # Consulting
    "mckinsey",
    "bcg",
    "bain",

    # Energy
    "octopusenergy",

    # Logistics
    "deliveroo",
    "uber",

    # Media / Research
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

    (
        "barclays",
        "External_Career_Site"
    ),

    (
        "hsbc",
        "HSBCCareers"
    ),

    (
        "jpmorgan",
        "jpmc"
    ),

    (
        "goldmansachs",
        "External"
    ),

    (
        "morganstanley",
        "MorganStanleyCareers"
    ),

    (
        "blackrock",
        "BlackRockCareers"
    ),

    (
        "natwestgroup",
        "NatWest_Group_Careers"
    ),

    # Research / Financial Data
    (
        "bloomberg",
        "careers"
    ),

    (
        "factset",
        "FactSetCareers"
    ),
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
# STEP 16 — DISPLAY NAMES
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

    # Care
    "barchester": "Barchester Healthcare",
    "barchesterhealthcare":
        "Barchester Healthcare",
    "hc-one": "HC-One",
    "hcone": "HC-One",
    "careuk": "Care UK",
}


# =========================================================
# STEP 17 — PROCESS STANDARD ATS COMPANY
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


            job["is_care_job"] = (
                is_care_job(
                    job.get(
                        "job_title"
                    )
                )
            )


            job["is_care_home_job"] = False


        return uk_jobs


    except Exception as e:

        print(
            f"Processing error "
            f"{slug}: {e}"
        )

        return []


# =========================================================
# STEP 18 — SPONSOR MATCHING
# =========================================================

print("\n" + "=" * 70)
print("STEP 18 — PREPARING SPONSOR MATCHING")
print("=" * 70)


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


# =========================================================
# STEP 19 — SCRAPE STANDARD JOBS
# =========================================================

print("\n" + "=" * 70)
print("STEP 19 — SCRAPING STANDARD JOB SOURCES")
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


                    job["is_care_job"] = (
                        is_care_job(
                            job.get(
                                "job_title"
                            )
                        )
                    )


                    job["is_care_home_job"] = False


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
    f"\nTotal standard UK jobs found: "
    f"{len(all_jobs):,}"
)


# =========================================================
# STEP 20 — SCRAPE DEDICATED CARE-HOME SOURCES
# =========================================================

print("\n" + "=" * 70)
print("STEP 20 — SCRAPING DEDICATED CARE-HOME SOURCES")
print("=" * 70)


care_jobs = []


# ---------------------------------------------------------
# Barchester
# ---------------------------------------------------------

if care_provider_is_sponsor(
    "Barchester Healthcare"
):

    print(
        "\nBarchester Healthcare "
        "is present on sponsor list."
    )


    barchester_jobs = (
        get_barchester_jobs()
    )


    care_jobs.extend(
        barchester_jobs
    )


else:

    print(
        "\nWARNING: Barchester Healthcare "
        "was not matched to sponsor list."
    )


# ---------------------------------------------------------
# HC-One
# ---------------------------------------------------------

if care_provider_is_sponsor(
    "HC-One"
):

    print(
        "\nHC-One is present on sponsor list."
    )


    hcone_jobs = (
        get_hcone_jobs()
    )


    care_jobs.extend(
        hcone_jobs
    )


else:

    print(
        "\nWARNING: HC-One "
        "was not matched to sponsor list."
    )


# ---------------------------------------------------------
# Care UK
# ---------------------------------------------------------

if care_provider_is_sponsor(
    "Care UK"
):

    print(
        "\nCare UK is present on sponsor list."
    )


    careuk_jobs = (
        get_careuk_jobs()
    )


    care_jobs.extend(
        careuk_jobs
    )


else:

    print(
        "\nWARNING: Care UK "
        "was not matched to sponsor list."
    )


print(
    f"\nDedicated care jobs scraped: "
    f"{len(care_jobs):,}"
)


# ---------------------------------------------------------
# Add care jobs
# ---------------------------------------------------------

all_jobs.extend(
    care_jobs
)


print(
    f"Total jobs before sponsor filtering: "
    f"{len(all_jobs):,}"
)


# =========================================================
# STEP 21 — CHECK JOB DATA
# =========================================================

if not all_jobs:

    raise Exception(
        "No jobs found."
    )


jobs_df = pd.DataFrame(
    all_jobs
)


# =========================================================
# STEP 22 — NORMALIZE JOB COMPANIES
# =========================================================

jobs_df["company_clean"] = (
    jobs_df["company"]
    .astype(str)
    .apply(normalize_company)
)


# =========================================================
# STEP 23 — INTERNAL CARE FLAGS
# =========================================================

if "is_care_job" not in jobs_df.columns:

    jobs_df["is_care_job"] = (
        jobs_df["job_title"]
        .apply(is_care_job)
    )


if "is_care_home_job" not in jobs_df.columns:

    jobs_df["is_care_home_job"] = False


# =========================================================
# STEP 24 — SPONSOR MATCHING
# =========================================================

print("\n" + "=" * 70)
print("STEP 24 — MATCHING JOBS AGAINST GOV.UK SPONSOR LIST")
print("=" * 70)


# ---------------------------------------------------------
# Exact sponsor matches
# ---------------------------------------------------------

jobs_df["is_licensed_sponsor"] = (
    jobs_df["company_clean"]
    .isin(sponsor_set)
)


print(
    "\nExact sponsor matches:",
    int(
        jobs_df[
            "is_licensed_sponsor"
        ].sum()
    )
)


# ---------------------------------------------------------
# Care provider aliases
# ---------------------------------------------------------

for provider_name in [
    "Barchester Healthcare",
    "HC-One",
    "Care UK",
]:

    mask = (
        jobs_df["company"]
        == provider_name
    )


    if mask.any():

        provider_match = (
            care_provider_is_sponsor(
                provider_name
            )
        )


        if provider_match:

            jobs_df.loc[
                mask,
                "is_licensed_sponsor"
            ] = True


# ---------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------

missing_mask = (
    jobs_df["is_licensed_sponsor"]
    == False
)


missing_count = int(
    missing_mask.sum()
)


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
# STEP 25 — SPONSOR FILTER
# =========================================================

before_sponsor_filter = len(
    jobs_df
)


jobs_df = jobs_df[
    jobs_df[
        "is_licensed_sponsor"
    ]
    == True
].copy()


after_sponsor_filter = len(
    jobs_df
)


print(
    f"\nJobs before sponsor filter: "
    f"{before_sponsor_filter:,}"
)


print(
    f"Licensed-sponsor jobs: "
    f"{after_sponsor_filter:,}"
)


# =========================================================
# STEP 26 — ADD METADATA
# =========================================================

today = datetime.today().strftime(
    "%Y-%m-%d"
)


jobs_df[
    "visa_sponsorship_possible"
] = True


jobs_df[
    "scraped_date"
] = today


# =========================================================
# STEP 27 — CLEAN JOB DATA
# =========================================================

for column in [
    "company",
    "job_title",
    "location",
    "job_url",
    "source",
]:

    if column not in jobs_df.columns:

        jobs_df[column] = ""


    jobs_df[column] = (
        jobs_df[column]
        .fillna("")
        .astype(str)
        .str.strip()
    )


# ---------------------------------------------------------
# Remove invalid jobs
# ---------------------------------------------------------

jobs_df = jobs_df[
    jobs_df["job_title"] != ""
].copy()


jobs_df = jobs_df[
    jobs_df["job_url"] != ""
].copy()


# =========================================================
# STEP 28 — REMOVE DUPLICATES
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
# STEP 29 — COUNT CARE-HOME JOBS ACTUALLY ADDED
# =========================================================
#
# This happens AFTER:
#
#     - UK filtering
#     - sponsor filtering
#     - invalid-job removal
#     - duplicate removal
#
# Therefore this number represents care-home jobs that
# actually make it into jobs.csv.
#
# =========================================================

care_home_jobs_added = int(
    jobs_df[
        "is_care_home_job"
    ].sum()
)


care_related_jobs_added = int(
    jobs_df[
        "is_care_job"
    ].sum()
)


# =========================================================
# STEP 30 — SORT
# =========================================================

jobs_df = jobs_df.sort_values(
    by=[
        "is_care_home_job",
        "is_care_job",
        "company",
        "job_title",
    ],
    ascending=[
        False,
        False,
        True,
        True,
    ]
)


# =========================================================
# STEP 31 — FINAL CSV COLUMNS
# =========================================================
#
# IMPORTANT:
#
# These are the ONLY columns that will be written to CSV.
#
# Internal columns such as:
#
#     company_clean
#     is_care_job
#     is_care_home_job
#     is_licensed_sponsor
#
# are deliberately removed from the exported CSV.
#
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
# STEP 32 — EXPORT CSV
# =========================================================

jobs_df.to_csv(
    OUTPUT_FILE,
    index=False,
    encoding="utf-8-sig"
)


# =========================================================
# STEP 33 — VERIFY CSV
# =========================================================

verification_df = pd.read_csv(
    OUTPUT_FILE
)


expected_columns = [

    "company",
    "job_title",
    "location",
    "job_url",
    "source",
    "visa_sponsorship_possible",
    "scraped_date",
]


if verification_df.columns.tolist() != (
    expected_columns
):

    raise Exception(
        "CSV columns are incorrect.\n"
        f"Expected: {expected_columns}\n"
        f"Actual: "
        f"{verification_df.columns.tolist()}"
    )


# =========================================================
# STEP 34 — FINAL SUMMARY
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
    f"CARE-HOME JOBS ADDED: "
    f"{care_home_jobs_added:,}"
)


print(
    f"Care-related jobs added: "
    f"{care_related_jobs_added:,}"
)


print(
    f"Other jobs added: "
    f"{len(jobs_df) - care_related_jobs_added:,}"
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
    "\nFirst 10 care-home jobs:"
)


# ---------------------------------------------------------
# Reconstruct care-home count from source because the
# internal flag is no longer in jobs_df.
#
# We identify the dedicated care-home sources here.
# ---------------------------------------------------------

care_source_mask = jobs_df[
    "source"
].isin(
    [
        "Barchester Healthcare",
        "HC-One",
        "Care UK",
    ]
)


care_preview = jobs_df[
    care_source_mask
][
    [
        "company",
        "job_title",
        "location",
        "job_url",
        "source",
    ]
].head(10)


if len(care_preview) > 0:

    print(
        care_preview.to_string(
            index=False
        )
    )

else:

    print(
        "WARNING: No dedicated care-home "
        "jobs were written to the CSV."
    )


print(
    "\nDone."
)
