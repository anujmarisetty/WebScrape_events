import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import date, timedelta, datetime
from urllib.parse import urljoin
from pathlib import Path
import logging
import sys
import time
from openpyxl import load_workbook

# Configure logging first
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Try to import Selenium for handling "view more" button
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    logger.warning("Selenium not available. 'View more' button handling will be limited.")


BASE_URL = "https://shotgun.live"
PARIS_BASE_URL = "https://shotgun.live/en/cities/paris"
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


def fetch_page_with_selenium(url: str, max_clicks: int = 10) -> str:
    """
    Fetch HTML content using Selenium, clicking 'view more' buttons to load all events.
    Returns the final HTML after all clicks.
    """
    if not SELENIUM_AVAILABLE:
        logger.warning("Selenium not available, falling back to regular fetch")
        return fetch_page(url)
    
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')  # Run in background
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
    
    driver = None
    try:
        logger.info(f"Fetching page with Selenium: {url}")
        # Use webdriver-manager to automatically handle ChromeDriver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.get(url)
        
        # Wait for page to load and content to appear
        wait = WebDriverWait(driver, 10)
        try:
            # Wait for any content to load (look for body or main content)
            wait.until(lambda d: len(d.page_source) > 1000)
        except TimeoutException:
            logger.warning("Page took too long to load initial content")
        
        # Additional wait for dynamic content
        time.sleep(3)
        
        # Click "view more" buttons multiple times
        click_count = 0
        while click_count < max_clicks:
            try:
                # Look for "view more" button with various possible texts/selectors
                view_more_selectors = [
                    "//button[contains(text(), 'View more')]",
                    "//button[contains(text(), 'view more')]",
                    "//button[contains(text(), 'See more')]",
                    "//button[contains(text(), 'Load more')]",
                    "//a[contains(text(), 'View more')]",
                    "//a[contains(text(), 'view more')]",
                    "//*[contains(@class, 'view-more')]",
                    "//*[contains(@class, 'load-more')]",
                    "//*[contains(@class, 'see-more')]",
                ]
                
                button_found = False
                for selector in view_more_selectors:
                    try:
                        wait = WebDriverWait(driver, 2)
                        button = wait.until(EC.element_to_be_clickable((By.XPATH, selector)))
                        if button.is_displayed():
                            # Scroll to button
                            driver.execute_script("arguments[0].scrollIntoView(true);", button)
                            time.sleep(0.5)
                            # Click button
                            button.click()
                            click_count += 1
                            logger.info(f"Clicked 'view more' button (click {click_count}/{max_clicks})")
                            time.sleep(2)  # Wait for content to load
                            button_found = True
                            break
                    except (TimeoutException, NoSuchElementException):
                        continue
                
                if not button_found:
                    logger.info("No more 'view more' buttons found")
                    break
                    
            except Exception as e:
                logger.debug(f"Error clicking view more: {e}")
                break
        
        # Get final page source
        html = driver.page_source
        logger.info(f"Successfully fetched page with Selenium ({len(html)} characters, {click_count} clicks)")
        
        # Debug: Check if we got meaningful content
        if len(html) < 5000:
            logger.warning(f"Page content seems small ({len(html)} chars). Page might not have loaded correctly.")
            # Try to find if there's an error message or empty state
            if "no events" in html.lower() or "no results" in html.lower():
                logger.warning("Page indicates no events found")
        
        return html
        
    except Exception as e:
        logger.error(f"Error fetching with Selenium: {e}")
        # Fallback to regular fetch
        return fetch_page(url)
    finally:
        if driver:
            driver.quit()


def get_date_url(target_date: date) -> str:
    """Generate URL for a specific date."""
    date_str = target_date.strftime("%Y-%m-%d")
    return f"{PARIS_BASE_URL}/-/{date_str}"


def parse_events_for_date(html: str, target_date: date) -> list:
    """
    Parse events from HTML for a specific date.
    Returns a list of event dictionaries with deduplication.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen_links = set()  # Prevent duplicates for this day
    
    # Find all event links on the page
    # Shotgun uses various structures, so we'll look for links containing "/events/"
    event_links = soup.find_all("a", href=lambda x: x and "/events/" in str(x))
    
    logger.info(f"Found {len(event_links)} potential event links for {target_date}")
    
    for link in event_links:
        href = link.get("href", "")
        if not href:
            continue
            
        event_link = urljoin(BASE_URL, href)
        
        # Skip duplicates
        if event_link in seen_links:
            continue
        seen_links.add(event_link)
        
        # Extract event name
        text = link.get_text(" ", strip=True)
        if not text or len(text.strip()) < 3:
            # Try to find text in parent or nearby elements
            parent = link.parent
            if parent:
                text = parent.get_text(" ", strip=True)
        
        if not text or len(text.strip()) < 3:
            continue
        
        # Clean up event name (remove price if present)
        name = text.split("€")[0].strip()
        if not name:
            continue
        
        # Skip if it looks like navigation or non-event content
        if any(skip in name.lower() for skip in ["view more", "see all", "more events", "load more"]):
            continue
        
        row = {
            "Date": target_date.isoformat(),
            "Event name": name,
            "Event link": event_link,
        }
        
        if validate_event_row(row):
            rows.append(row)
    
    # Also try to find events in common structures
    # Look for event cards or containers
    event_containers = soup.find_all(["div", "article", "li"], class_=lambda x: x and (
        "event" in str(x).lower() or 
        "card" in str(x).lower() or
        "item" in str(x).lower()
    ))
    
    for container in event_containers:
        link = container.find("a", href=lambda x: x and "/events/" in str(x))
        if not link:
            continue
            
        href = link.get("href", "")
        event_link = urljoin(BASE_URL, href)
        
        if event_link in seen_links:
            continue
        seen_links.add(event_link)
        
        # Try multiple ways to get event name
        name = None
        # Try link text
        name = link.get_text(" ", strip=True)
        if not name or len(name) < 3:
            # Try title attribute
            name = link.get("title", "").strip()
        if not name or len(name) < 3:
            # Try container text
            name = container.get_text(" ", strip=True).strip()
        
        if not name or len(name) < 3:
            continue
        
        # Clean up event name
        name = name.split("€")[0].strip()
        if not name:
            continue
        
        # Skip navigation elements
        if any(skip in name.lower() for skip in ["view more", "see all", "more events", "load more"]):
            continue
        
        row = {
            "Date": target_date.isoformat(),
            "Event name": name,
            "Event link": event_link,
        }
        
        if validate_event_row(row):
            rows.append(row)
    
    logger.info(f"Found {len(rows)} unique events for {target_date}")
    return rows


def validate_event_row(row: dict) -> bool:
    """Validate that an event row has required fields."""
    required = ["Date", "Event name", "Event link"]
    return all(key in row and row[key] for key in required)


def save_to_excel(events_by_date: dict, output_path: str):
    """
    Save events to Excel file with separate sheets for each day.
    events_by_date: dict mapping date (date object) to list of event dicts
    """
    if not events_by_date:
        logger.warning("No events found for next seven days")
        return
    
    # Check if file exists and is locked
    output_path_obj = Path(output_path)
    if output_path_obj.exists():
        try:
            # Try to open in append mode to check if file is locked
            with open(output_path, 'r+b'):
                pass
        except PermissionError:
            logger.warning(f"File {output_path} is locked (may be open in Excel). Attempting to create new file with timestamp...")
            # Add timestamp to filename
            timestamp = datetime.now().strftime("%H%M%S")
            base_name = output_path_obj.stem
            output_path = str(output_path_obj.parent / f"{base_name}_{timestamp}.xlsx")
            logger.info(f"Using new filename: {output_path}")
    
    try:
        # Create Excel writer
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # Sort dates
            sorted_dates = sorted(events_by_date.keys())
            
            sheets_created = 0
            for target_date in sorted_dates:
                events = events_by_date[target_date]
                
                # Create sheet name (Excel sheet names have limitations)
                sheet_name = target_date.strftime("%Y-%m-%d")
                # Excel sheet names can't be longer than 31 characters
                if len(sheet_name) > 31:
                    sheet_name = sheet_name[:31]
                
                if not events:
                    logger.info(f"No events for {target_date}, creating empty sheet")
                    # Create empty DataFrame with correct columns
                    df = pd.DataFrame(columns=["S.no", "Date", "Event name", "Event link"])
                else:
                    # Create DataFrame for this date
                    df = pd.DataFrame(events)
                    
                    # Remove duplicates within this day (based on event link)
                    df = df.drop_duplicates(subset=['Event link'], keep='first')
                    
                    # Sort by event name for better organization
                    df = df.sort_values("Event name")
                    df = df.reset_index(drop=True)
                    
                    # Add serial number
                    df.insert(0, "S.no", range(1, len(df) + 1))
                
                # Write to sheet (even if empty)
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                sheets_created += 1
                if events:
                    logger.info(f"Saved {len(df)} events to sheet '{sheet_name}'")
                else:
                    logger.info(f"Created empty sheet '{sheet_name}' (no events found)")
            
            # Ensure at least one sheet exists (Excel requirement)
            if sheets_created == 0:
                logger.warning("No sheets created, creating a summary sheet")
                df = pd.DataFrame(columns=["S.no", "Date", "Event name", "Event link"])
                df.to_excel(writer, sheet_name="Summary", index=False)
        
        logger.info(f"Saved all events to {output_path}")
        
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
    """Main function to scrape events for next 7 days and save to Excel."""
    try:
        logger.info("Starting Shotgun Paris events scraper for next 7 days")
        
        today = date.today()
        events_by_date = {}
        
        # Fetch events for each of the next 7 days
        for day_offset in range(7):
            target_date = today + timedelta(days=day_offset)
            logger.info(f"\n{'='*60}")
            logger.info(f"Processing date: {target_date} (day {day_offset + 1}/7)")
            logger.info(f"{'='*60}")
            
            # Get date-specific URL
            date_url = get_date_url(target_date)
            
            # Fetch the page (try with Selenium to handle "view more" button)
            try:
                html = fetch_page_with_selenium(date_url)
            except Exception as e:
                logger.warning(f"Selenium fetch failed, using regular fetch: {e}")
                html = fetch_page(date_url)
            
            # Parse events for this date
            events = parse_events_for_date(html, target_date)
            
            if events:
                events_by_date[target_date] = events
            else:
                logger.warning(f"No events found for {target_date}")
                events_by_date[target_date] = []  # Keep empty list to create empty sheet
            
            # Small delay to be respectful to the server
            if day_offset < 6:  # Don't delay after last day
                time.sleep(1)
        
        # Save to Excel with separate sheets for each day
        output_path = get_output_filename()
        save_to_excel(events_by_date, output_path)
        
        total_events = sum(len(events) for events in events_by_date.values())
        logger.info(f"\n{'='*60}")
        logger.info(f"Scraping completed successfully!")
        logger.info(f"Total events found: {total_events}")
        logger.info(f"Output file: {output_path}")
        logger.info(f"{'='*60}")
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
