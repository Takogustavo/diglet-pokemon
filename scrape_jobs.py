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

# Load GOV page
response = requests.get(GOV_PAGE)

soup = BeautifulSoup(response.text, "html.parser")

# Find latest CSV URL
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

# Rename company column
sponsors = sponsors.rename(columns={
    "Organisation Name": "company"
})

# Normalize company names
def normalize_company(name):

    return (
        str(name)
        .lower()
        .replace("limited", "")
        .replace("ltd", "")
        .replace("llp", "")
        .replace("&", "and")
        .replace(",", "")
        .replace(".", "")
        .replace("-", "")
        .strip()
    )

# Create clean sponsor names
sponsors["company_clean"] = (
    sponsors["company"]
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

        r = requests.get(url, timeout=10)

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

        r = requests.get(url, timeout=10)

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
# STEP 5 — COMPANY LISTS
# =========================================================

GREENHOUSE_COMPANIES = [

    # Fintech
    "monzo",
    "wise",
    "checkoutcom",
    "stripe",

    # UK Tech
    "octopusenergy",
    "deliveroo",

    # SaaS / Security
    "datadog",
    "mongodb",
    "snyk",
    "cloudflare",
    "gitlab",
    "figma",
    "webflow",
]

LEVER_COMPANIES = [

    "netflix",
    "shopify",
    "asana",
    "robinhood",
    "coinbase",
]

# Display names
COMPANY_DISPLAY_NAMES = {

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
    "netflix": "Netflix",
    "shopify": "Shopify",
    "asana": "Asana",
    "robinhood": "Robinhood",
    "coinbase": "Coinbase",
}

# =========================================================
# STEP 6 — UK LOCATION FILTER
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

    "remote uk",
    "hybrid uk",
]

def is_uk_location(location):

    if not location:
        return False

    location = str(location).lower()

    return any(pattern in location for pattern in UK_PATTERNS)

# =========================================================
# STEP 7 — SCRAPE JOBS
# =========================================================

all_jobs = []

# GREENHOUSE JOBS
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

# LEVER JOBS
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
# STEP 8 — CREATE DATAFRAME
# =========================================================

jobs_df = pd.DataFrame(all_jobs)

if len(jobs_df) == 0:
    raise Exception("No jobs found.")

# =========================================================
# STEP 9 — NORMALIZE JOB COMPANY NAMES
# =========================================================

jobs_df["company_clean"] = (
    jobs_df["company"]
    .apply(normalize_company)
)

# =========================================================
# STEP 10 — MATCH AGAINST SPONSOR LIST
# =========================================================

sponsor_names = (
    sponsors["company_clean"]
    .dropna()
    .tolist()
)

def is_sponsor(company_name, sponsor_list, threshold=80):

    company_name = normalize_company(company_name)

    for sponsor in sponsor_list:

        score = fuzz.ratio(company_name, sponsor)

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
# STEP 11 — ADD METADATA
# =========================================================

today = datetime.today().strftime("%Y-%m-%d")

jobs_df["visa_sponsorship_possible"] = True
jobs_df["scraped_date"] = today

# =========================================================
# STEP 12 — FINAL COLUMNS
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

# =========================================================
# STEP 13 — EXPORT CSV
# =========================================================

jobs_df.to_csv("jobs.csv", index=False)

print("\nSaved jobs.csv")

# =========================================================
# STEP 14 — PREVIEW RESULTS
# =========================================================

print("\nPreview:")

display(jobs_df.head(20))

print(f"\nFinal total sponsored UK jobs: {len(jobs_df)}")


# In[ ]:




