"""
Job monitor: checks a list of companies' career pages for new design roles
and sends a push notification (via ntfy.sh) when one shows up.

Usage:
    python job_monitor.py

Config:
    - companies.json   list of companies + how to fetch their jobs
    - state.json       auto-created; remembers which jobs we've already seen
    - NTFY_TOPIC        set below or as an environment variable

First run just records everything as "seen" (no notifications), so you
don't get spammed with every existing job on the first run. From the
second run onward, only new postings trigger a notification.
"""

import json
import os
import hashlib
import re
from pathlib import Path

import requests

HERE = Path(__file__).parent
COMPANIES_FILE = HERE / "companies.json"
STATE_FILE = HERE / "state.json"

# --- Configure these two ---
PLACEHOLDER_NTFY_TOPIC = "changeme-to-a-private-topic-name"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
TARGET_TITLES = [
    "UX Designer",
    "Product Designer",
    "UI/UX Designer",
    "User Experience Designer",
    "Interaction Designer",
]
TARGET_TITLE_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(title) for title in TARGET_TITLES) + r")\b",
    re.IGNORECASE,
)
US_LOCATION_INDICATORS = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
    "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma",
    "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia",
    "Wisconsin", "Wyoming", "District of Columbia", "AL", "AK", "AZ", "AR", "CA",
    "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
    "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
    "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]
US_LOCATION_PATTERN = re.compile(
    r"(?<!\w)(?:United States|USA|U\.S\.|US|" + "|".join(
        re.escape(indicator) for indicator in US_LOCATION_INDICATORS
    ) + r")(?!\w)",
    re.IGNORECASE,
)


def validate_ntfy_topic(topic: str) -> str:
    if not topic or topic == PLACEHOLDER_NTFY_TOPIC:
        raise ValueError(
            "NTFY_TOPIC is not configured. Set the environment variable to a private ntfy topic "
            "before running job_monitor.py."
        )
    return topic


def matches_design_role(title: str) -> bool:
    return TARGET_TITLE_PATTERN.search(title) is not None


def matches_us_location(location: str) -> bool:
    return US_LOCATION_PATTERN.search(location) is not None


def fetch_greenhouse(company: dict) -> list[dict]:
    """Greenhouse public Job Board API. No auth needed."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{company['token']}/jobs"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    jobs = resp.json().get("jobs", [])
    return [
        {
            "id": str(job["id"]),
            "title": job["title"],
            "url": job.get("absolute_url", ""),
            "location": (job.get("location") or {}).get("name", ""),
        }
        for job in jobs
    ]


def fetch_lever(company: dict) -> list[dict]:
    """Lever public postings API. No auth needed."""
    url = f"https://api.lever.co/v0/postings/{company['token']}?mode=json"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    postings = resp.json()
    return [
        {
            "id": job["id"],
            "title": job["text"],
            "url": job.get("hostedUrl", ""),
            "location": (job.get("categories") or {}).get("location", ""),
        }
        for job in postings
    ]


def fetch_workday(company: dict) -> list[dict]:
    """
    Workday's internal search endpoint. It's a POST with a JSON body,
    and it paginates 20 at a time, so we loop until we've seen everything
    (capped at a reasonable limit so one company can't run forever).
    """
    tenant = company["tenant"]
    dc = company["dc"]
    site = company["site"]
    base = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"

    all_jobs = []
    offset = 0
    limit = 20
    max_pages = 50  # safety cap: 1000 jobs max per company per run

    for _ in range(max_pages):
        payload = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
        resp = requests.post(base, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("jobPostings", [])
        if not postings:
            break

        for job in postings:
            all_jobs.append(
                {
                    "id": job.get("bulletFields", [None])[0] or job.get("externalPath", ""),
                    "title": job.get("title", ""),
                    "url": f"https://{tenant}.{dc}.myworkdayjobs.com{job.get('externalPath', '')}",
                    "location": job.get("locationsText", ""),
                }
            )

        offset += limit
        if offset >= data.get("total", 0):
            break

    return all_jobs


def fetch_custom_diff(company: dict) -> list[dict]:
    """
    Fallback for pages without a known API: hash the page content and
    treat the whole page as a single 'job' whose id is the content hash.
    When the hash changes, we notify 'something changed, go check' rather
    than naming a specific new role.
    """
    resp = requests.get(company["url"], timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    content_hash = hashlib.sha256(resp.text.encode("utf-8")).hexdigest()
    return [
        {
            "id": content_hash,
            "title": f"[Page changed] {company['name']} careers page",
            "url": company["url"],
            "location": "",
        }
    ]


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "workday": fetch_workday,
    "custom_diff": fetch_custom_diff,
}


def load_state() -> dict:
    state_path = Path(STATE_FILE)
    if not state_path.exists():
        return {}

    raw = state_path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[warn] {state_path} is invalid JSON; resetting state")
        return {}

    if not isinstance(data, dict):
        print(f"[warn] {state_path} did not contain an object; resetting state")
        return {}

    return data


def save_state(state: dict) -> None:
    Path(STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")


def send_notification(title: str, message: str, url: str = "") -> None:
    headers = {"Title": title}
    if url:
        headers["Click"] = url
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=10,
        ).raise_for_status()
    except requests.RequestException as exc:
        print(f"[warn] notification failed: {exc}")


def main():
    validate_ntfy_topic(NTFY_TOPIC)
    companies_path = Path(COMPANIES_FILE)
    state_path = Path(STATE_FILE)
    companies = json.loads(companies_path.read_text(encoding="utf-8"))
    state = load_state()
    is_first_run = not state_path.exists()

    for company in companies:
        name = company["name"]
        fetcher = FETCHERS.get(company["type"])
        if not fetcher:
            print(f"[skip] {name}: unknown source type '{company['type']}'")
            continue

        try:
            jobs = fetcher(company)
        except Exception as e:
            print(f"[error] {name}: {e}")
            continue

        seen_ids = set(state.get(name, []))
        current_ids = set()

        for job in jobs:
            current_ids.add(job["id"])
            is_new = job["id"] not in seen_ids
            is_design = matches_design_role(job["title"])
            is_us_based = matches_us_location(job["location"])

            if is_new and is_design and is_us_based and not is_first_run:
                print(f"[new match] {name}: {job['title']}")
                send_notification(
                    title=f"New design role at {name}",
                    message=f"{job['title']} ({job['location']})",
                    url=job["url"],
                )

        state[name] = list(current_ids)
        print(f"[ok] {name}: {len(jobs)} jobs checked")

    save_state(state)
    if is_first_run:
        print("\nFirst run complete — baseline saved. Future runs will notify on new roles.")


if __name__ == "__main__":
    main()
