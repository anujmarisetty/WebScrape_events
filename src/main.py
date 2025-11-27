import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import date, timedelta, datetime
from dateutil import parser as date_parser
from urllib.parse import urljoin
from pathlib import Path
import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

BASE_URL = "https://shotgun.live"
PARIS_URL = "https://shotgun.live/en/cities/paris"
# Output directory relative to project root (one level up from src/)
OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def fetch_page(url: str) -> str:
    """Fetch HTML content from the given URL with error handling."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        logger.info(f"Fetching page: {url}")
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        logger.info(f"Successfully fetched page ({len(resp.text)} characters)")
        return resp.text
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching {url}: {e}")
        raise


def looks_like_day_header(text: str) -> bool:
    """
    Accept things like 'Thu 27 Nov' or 'Fri 5 Dec'.
    """
    parts = text.split()
    if len(parts) < 3:
        return False
    # Very loose check: first part three letters, second is number
    return len(parts[0]) == 3 and parts[1].rstrip(".").isdigit()


def parse_shotgun_day_to_date(text: str) -> date | None:
    """
    Convert 'Thu 27 Nov' to a real date in the current year.
    If the date has already passed in the current year, assume next year.
    """
    parts = text.split()
    if len(parts) < 3:
        return None

    try:
        # Drop weekday, parse '27 Nov' with a guessed year
        day_month = " ".join(parts[1:3])
        today = date.today()
        year = today.year
        dt = date_parser.parse(f"{day_month} {year}", dayfirst=True).date()

        # If date is more than 6 months in the past, assume next year
        # (handles year rollover better)
        if dt < today - timedelta(days=180):
            dt = dt.replace(year=year + 1)
        # If date is in the past but within 6 months, skip it for "next 7 days"
        elif dt < today:
            return None

        return dt
    except Exception as e:
        logger.debug(f"Error parsing date '{text}': {e}")
        return None


def validate_event_row(row: dict) -> bool:
    """Validate that an event row has required fields."""
    required = ["Date", "Event name", "Event link"]
    return all(key in row and row[key] for key in required)


def parse_events_for_next_7_days(html: str):
    """
    Parse events from Shotgun Paris page for the next 7 days.
    Returns a list of event dictionaries.
    """
    soup = BeautifulSoup(html, "html.parser")
    today = date.today()
    end_date = today + timedelta(days=7)

    rows = []
    seen_links = set()  # Prevent duplicates

    # Shotgun Paris page groups events under headings like "Thu 27 Nov"
    # Use that to determine the date for all events that follow until the next heading.
    day_headers = soup.find_all(["h2", "h3", "h4"])

    logger.info(f"Found {len(day_headers)} potential day headers")

    for header in day_headers:
        day_text = header.get_text(strip=True)

        # Skip sections like "Featured", "Artists to see in Paris" etc
        if not looks_like_day_header(day_text):
            continue

        event_date = parse_shotgun_day_to_date(day_text)
        if event_date is None:
            continue

        if not (today <= event_date <= end_date):
            logger.debug(f"Skipping date {event_date} (outside 7-day window)")
            continue

        logger.info(f"Processing events for {event_date} (header: '{day_text}')")

        # Strategy 1: Check parent container for events
        parent = header.parent
        if parent:
            links = parent.find_all("a", href=lambda x: x and "/events/" in x)
            for a in links:
                href = a.get("href", "")
                event_link = urljoin(BASE_URL, href)
                if event_link in seen_links:
                    continue
                seen_links.add(event_link)

                text = a.get_text(" ", strip=True)
                if not text or len(text.strip()) < 3:
                    continue

                name = text.split("€")[0].strip()
                if not name:
                    continue

                row = {
                    "Date": event_date.isoformat(),
                    "Event name": name,
                    "Event link": event_link,
                }
                if validate_event_row(row):
                    rows.append(row)

        # Strategy 2: Check following siblings (original approach)
        for sibling in header.find_next_siblings():
            # Check if it's another day header
            if sibling.name in ["h2", "h3", "h4"]:
                if looks_like_day_header(sibling.get_text(strip=True)):
                    break

            # Recursively find all links in this sibling
            links = sibling.find_all("a", href=lambda x: x and "/events/" in x)
            for a in links:
                href = a.get("href", "")
                event_link = urljoin(BASE_URL, href)
                if event_link in seen_links:
                    continue
                seen_links.add(event_link)

                text = a.get_text(" ", strip=True)
                if not text or len(text.strip()) < 3:
                    continue

                name = text.split("€")[0].strip()
                if not name:
                    continue

                row = {
                    "Date": event_date.isoformat(),
                    "Event name": name,
                    "Event link": event_link,
                }
                if validate_event_row(row):
                    rows.append(row)

    logger.info(f"Found {len(rows)} total events")
    return rows


def save_to_excel(rows, output_path: str):
    """Save events to Excel file with proper error handling."""
    if not rows:
        logger.warning("No events found for next seven days")
        return

    try:
        df = pd.DataFrame(rows)
        
        # Sort by date for better organization
        df = df.sort_values("Date")
        df = df.reset_index(drop=True)
        
        # Add serial number
        df.insert(0, "S.no", range(1, len(df) + 1))
        
        # Save to Excel
        df.to_excel(output_path, index=False, engine='openpyxl')
        logger.info(f"Saved {len(df)} events to {output_path}")
        
    except PermissionError:
        logger.error(f"Cannot write to {output_path}. File may be open.")
        raise
    except Exception as e:
        logger.error(f"Error saving to Excel: {e}")
        raise


def get_output_filename() -> str:
    """Generate output filename with current date."""
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"shotgun_paris_events_{today}.xlsx"
    return str(OUTPUT_DIR / filename)


def main():
    """Main function to scrape events and save to Excel."""
    try:
        logger.info("Starting Shotgun Paris events scraper")
        
        # Fetch the page
        html = fetch_page(PARIS_URL)
        
        # Parse events
        rows = parse_events_for_next_7_days(html)
        
        # Save to Excel with date in filename
        output_path = get_output_filename()
        save_to_excel(rows, output_path)
        
        logger.info("Scraping completed successfully")
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

