import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import date, timedelta, datetime
from urllib.parse import urljoin
from pathlib import Path
import logging
import sys
import time
import tempfile
import os
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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


def fetch_page(url: str, max_retries: int = 3) -> str:
    """Fetch HTML content from the given URL with error handling and retry logic."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    
    # Create session with retry strategy
    # Note: We exclude 429 from status_forcelist to handle it manually with longer waits
    session = requests.Session()
    retry_strategy = Retry(
        total=1,  # Only retry once automatically (for non-429 errors)
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],  # Exclude 429 - handle manually
        allowed_methods=["GET"],
        respect_retry_after_header=True
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    for attempt in range(max_retries):
        try:
            logger.info(f"[HTTP] Fetching page: {url} (attempt {attempt + 1}/{max_retries})")
            logger.debug(f"[HTTP] Using headers: {headers}")
            
            resp = session.get(url, headers=headers, timeout=30)
            logger.info(f"[HTTP] Response status: {resp.status_code}")
            logger.info(f"[HTTP] Response headers: Content-Type={resp.headers.get('Content-Type', 'N/A')}, Content-Length={resp.headers.get('Content-Length', 'N/A')}")
            
            # Handle rate limiting
            if resp.status_code == 429:
                retry_after = resp.headers.get('Retry-After', None)
                if retry_after:
                    try:
                        wait_time = int(retry_after)
                    except ValueError:
                        wait_time = 120  # Default to 2 minutes if header is invalid
                else:
                    # Exponential backoff: 60s, 120s, 180s
                    wait_time = 60 + (attempt * 60)
                
                if attempt < max_retries - 1:
                    logger.warning(f"[HTTP] Rate limited (429). Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"[HTTP] Rate limited (429) after {max_retries} attempts. Giving up.")
                    raise requests.exceptions.HTTPError(f"429 Client Error: Too Many Requests after {max_retries} attempts")
            
            resp.raise_for_status()
            html = resp.text
            logger.info(f"[HTTP] Successfully fetched page ({len(html)} characters)")
            
            # Log some HTML content for debugging
            logger.debug(f"[HTTP] HTML preview (first 200 chars): {html[:200]}")
            logger.debug(f"[HTTP] HTML contains 'events': {'events' in html.lower()}")
            logger.debug(f"[HTTP] HTML contains 'view more': {'view more' in html.lower()}")
            logger.debug(f"[HTTP] Number of '<a' tags: {html.count('<a')}")
            logger.debug(f"[HTTP] Number of '/events/' in HTML: {html.count('/events/')}")
            
            return html
            
        except requests.exceptions.HTTPError as e:
            if e.response and e.response.status_code == 429:
                if attempt < max_retries - 1:
                    wait_time = 60 + (attempt * 60)  # 60s, 120s, 180s
                    logger.warning(f"[HTTP] Rate limited. Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
            logger.error(f"[HTTP] Error fetching {url}: {e}")
            logger.error(f"[HTTP] Exception type: {type(e).__name__}")
            if attempt == max_retries - 1:
                raise
            # Wait before retry for other HTTP errors
            wait_time = 10 * (attempt + 1)
            logger.warning(f"[HTTP] Waiting {wait_time} seconds before retry...")
            time.sleep(wait_time)
        except (requests.exceptions.RetryError, requests.exceptions.RequestException) as e:
            # Handle urllib3's RetryError which occurs when too many 429s happen
            error_str = str(e).lower()
            if '429' in error_str or 'too many' in error_str:
                if attempt < max_retries - 1:
                    wait_time = 120 + (attempt * 60)  # Longer wait for rate limiting: 120s, 180s, 240s
                    logger.warning(f"[HTTP] Rate limited (RetryError). Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"[HTTP] Rate limited after {max_retries} attempts. Consider using Selenium instead.")
                    raise requests.exceptions.HTTPError(f"429 Client Error: Too Many Requests after {max_retries} attempts")
            logger.error(f"[HTTP] Request error: {e}")
            logger.error(f"[HTTP] Exception type: {type(e).__name__}")
            if attempt == max_retries - 1:
                raise
            # Wait before retry
            wait_time = 10 * (attempt + 1)
            logger.warning(f"[HTTP] Waiting {wait_time} seconds before retry...")
            time.sleep(wait_time)
    
    raise Exception(f"Failed to fetch {url} after {max_retries} attempts")


def fetch_page_with_selenium(url: str, max_clicks: int = 10) -> str:
    """
    Fetch HTML content using Selenium, clicking 'view more' buttons to load all events.
    Returns the final HTML after all clicks.
    """
    if not SELENIUM_AVAILABLE:
        logger.warning("Selenium not available, falling back to regular fetch")
        return fetch_page(url)
    
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')  # Use new headless mode
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-blink-features=AutomationControlled')  # Avoid detection
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--start-maximized')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-infobars')
    options.add_argument('--disable-notifications')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    driver = None
    try:
        logger.info(f"[SELENIUM] Starting Selenium fetch for: {url}")
        logger.info(f"[SELENIUM] Chrome options: headless={options.arguments}")
        # Use webdriver-manager to automatically handle ChromeDriver
        logger.info("[SELENIUM] Installing/checking ChromeDriver...")
        service = Service(ChromeDriverManager().install())
        logger.info("[SELENIUM] ChromeDriver ready, initializing browser...")
        driver = webdriver.Chrome(service=service, options=options)
        logger.info("[SELENIUM] Browser initialized successfully")
        
        # Execute script to avoid detection
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            '''
        })
        
        logger.info(f"[SELENIUM] Loading URL: {url}")
        driver.get(url)
        logger.info(f"[SELENIUM] URL loaded, waiting for content...")
        
        # Wait for page to load and content to appear
        wait = WebDriverWait(driver, 30)
        try:
            logger.info("[SELENIUM] Waiting for page content to load (timeout: 30s)...")
            # Wait for page to have substantial content or specific elements
            wait.until(lambda d: len(d.page_source) > 50000 or 
                      len(d.find_elements(By.TAG_NAME, "a")) > 10 or
                      "events" in d.page_source.lower())
            page_size = len(driver.page_source)
            num_links = len(driver.find_elements(By.TAG_NAME, "a"))
            logger.info(f"[SELENIUM] Initial page loaded: {page_size} characters, {num_links} links")
            logger.debug(f"[SELENIUM] Page source preview (first 300 chars): {driver.page_source[:300]}")
        except TimeoutException:
            current_size = len(driver.page_source)
            num_links = len(driver.find_elements(By.TAG_NAME, "a"))
            logger.warning(f"[SELENIUM] Page took too long to load. Current size: {current_size} characters, Links: {num_links}")
            # Check if we're on the right page
            try:
                current_url = driver.current_url
                page_title = driver.title
                logger.warning(f"[SELENIUM] Current URL: {current_url}")
                logger.warning(f"[SELENIUM] Page title: {page_title}")
                logger.warning(f"[SELENIUM] Page source sample: {driver.page_source[:500]}")
            except Exception as e:
                logger.error(f"[SELENIUM] Error getting page info: {e}")
        
        # Wait for JavaScript to execute and content to render
        logger.info("[SELENIUM] Waiting for JavaScript to execute (5 seconds)...")
        time.sleep(5)  # Increased to ensure content loads properly
        
        # Check page state before looking for elements
        initial_size = len(driver.page_source)
        initial_links = len(driver.find_elements(By.TAG_NAME, "a"))
        logger.info(f"[SELENIUM] After JS wait - Page size: {initial_size} chars, Links: {initial_links}")
        
        # Try to wait for specific elements that should be on the page
        try:
            logger.info("[SELENIUM] Looking for event elements...")
            # Look for event links or containers
            wait.until(lambda d: len(d.find_elements(By.XPATH, "//a[contains(@href, '/events/')]")) > 0 or
                      len(d.find_elements(By.XPATH, "//*[contains(@class, 'event')]")) > 0)
            event_links_count = len(driver.find_elements(By.XPATH, "//a[contains(@href, '/events/')]"))
            event_elements_count = len(driver.find_elements(By.XPATH, "//*[contains(@class, 'event')]"))
            logger.info(f"[SELENIUM] Event elements detected: {event_links_count} event links, {event_elements_count} event containers")
        except TimeoutException:
            logger.warning("[SELENIUM] No event elements found after waiting. Page may not have loaded correctly.")
            logger.warning(f"[SELENIUM] Current page size: {len(driver.page_source)} chars")
        
        # Scroll to bottom to trigger lazy loading
        logger.info("[SELENIUM] Scrolling to bottom to trigger lazy loading...")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)  # Reduced wait time
        after_scroll_size = len(driver.page_source)
        logger.info(f"[SELENIUM] After scrolling down - Page size: {after_scroll_size} chars")
        
        # Scroll back to top
        logger.info("[SELENIUM] Scrolling back to top...")
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)  # Reduced wait time
        
        # Check final page state
        final_size = len(driver.page_source)
        final_links = len(driver.find_elements(By.TAG_NAME, "a"))
        event_links_final = len(driver.find_elements(By.XPATH, "//a[contains(@href, '/events/')]"))
        logger.info(f"[SELENIUM] Final state - Page size: {final_size} chars, Total links: {final_links}, Event links: {event_links_final}")
        
        # Click "view more" buttons multiple times
        click_count = 0
        logger.info(f"[SELENIUM] Starting to look for 'view more' buttons (max {max_clicks} clicks)...")
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
                
                logger.debug(f"[SELENIUM] Attempt {click_count + 1}: Looking for 'view more' buttons...")
                button_found = False
                for i, selector in enumerate(view_more_selectors):
                    try:
                        logger.debug(f"[SELENIUM] Trying selector {i+1}/{len(view_more_selectors)}: {selector[:50]}...")
                        wait = WebDriverWait(driver, 2)  # Reduced timeout from 5 to 2 seconds
                        button = wait.until(EC.presence_of_element_located((By.XPATH, selector)))
                        is_displayed = button.is_displayed()
                        is_enabled = button.is_enabled()
                        logger.debug(f"[SELENIUM] Button found - displayed: {is_displayed}, enabled: {is_enabled}")
                        if is_displayed and is_enabled:
                            # Scroll to button
                            logger.debug("[SELENIUM] Scrolling to button...")
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                            time.sleep(0.3)  # Reduced wait time
                            # Try JavaScript click first (more reliable in headless)
                            try:
                                logger.debug("[SELENIUM] Attempting JavaScript click...")
                                driver.execute_script("arguments[0].click();", button)
                            except Exception as e:
                                logger.debug(f"[SELENIUM] JavaScript click failed, trying regular click: {e}")
                                button.click()
                            page_size_before = len(driver.page_source)
                            click_count += 1
                            
                            # Wait for new content to load
                            time.sleep(2)  # Increased to ensure content loads
                            page_size_after = len(driver.page_source)
                            
                            # Check if content actually increased
                            size_increase = page_size_after - page_size_before
                            logger.info(f"[SELENIUM] Clicked 'view more' button (click {click_count}/{max_clicks}). Page size: {page_size_before} -> {page_size_after} chars (+{size_increase})")
                            
                            # Scroll to bottom to trigger more loading
                            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                            time.sleep(1)  # Increased wait time
                            
                            # If page size didn't increase much, the button might not have loaded new content
                            # But continue anyway as sometimes content loads asynchronously
                            button_found = True
                            break
                    except (TimeoutException, NoSuchElementException) as e:
                        logger.debug(f"[SELENIUM] Selector {i+1} failed: {type(e).__name__}")
                        continue
                
                if not button_found:
                    logger.info(f"[SELENIUM] No more 'view more' buttons found after {click_count} clicks. All events should be loaded.")
                    break
                    
            except Exception as e:
                logger.warning(f"[SELENIUM] Error clicking view more: {e}")
                logger.debug(f"[SELENIUM] Exception type: {type(e).__name__}, Details: {str(e)}")
                break
        
        # Get final page source
        html = driver.page_source
        logger.info(f"Successfully fetched page with Selenium ({len(html)} characters, {click_count} clicks)")
        
        # Debug: Check if we got meaningful content
        if len(html) < 50000:
            logger.warning(f"Page content seems small ({len(html)} chars). Page might not have loaded correctly.")
            # Log a snippet of the HTML to debug
            logger.warning(f"HTML snippet (first 1000 chars): {html[:1000]}")
            logger.warning(f"HTML snippet (last 500 chars): {html[-500:]}")
            
            # Check page title and URL
            try:
                page_title = driver.title
                current_url = driver.current_url
                logger.warning(f"Page title: {page_title}")
                logger.warning(f"Current URL: {current_url}")
            except:
                pass
            
            # Check for common error indicators
            html_lower = html.lower()
            if "no events" in html_lower or "no results" in html_lower:
                logger.warning("Page indicates no events found")
            if "blocked" in html_lower or "access denied" in html_lower or "forbidden" in html_lower:
                logger.error("Page appears to be blocking access!")
            if "cloudflare" in html_lower or "checking your browser" in html_lower:
                logger.error("Cloudflare protection detected!")
            if len(html) < 10000:
                logger.error("Page content is extremely small. Page may not have loaded at all.")
                # Try to get a screenshot for debugging (if possible)
                try:
                    screenshot_path = os.path.join(tempfile.gettempdir(), f"selenium_debug_{int(time.time())}.png")
                    driver.save_screenshot(screenshot_path)
                    logger.info(f"Screenshot saved to {screenshot_path}")
                except:
                    pass
        
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
    logger.info(f"[PARSE] Starting to parse events for {target_date}")
    logger.debug(f"[PARSE] HTML size: {len(html)} characters")
    
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen_links = set()  # Prevent duplicates for this day
    
    # Debug: Check page content
    page_text = soup.get_text()[:500] if soup else ""
    logger.debug(f"[PARSE] Page text preview (first 500 chars): {page_text}")
    
    # Find all event links on the page
    # Shotgun uses various structures, so we'll look for links containing "/events/"
    logger.debug("[PARSE] Searching for event links with '/events/' pattern...")
    event_links = soup.find_all("a", href=lambda x: x and "/events/" in str(x))
    
    logger.info(f"[PARSE] Found {len(event_links)} potential event links for {target_date}")
    
    if len(event_links) == 0:
        logger.warning(f"[PARSE] No event links found! Checking HTML structure...")
        all_links = soup.find_all("a", href=True)
        logger.warning(f"[PARSE] Total links on page: {len(all_links)}")
        if len(all_links) > 0:
            logger.warning(f"[PARSE] Sample links (first 5):")
            for i, link in enumerate(all_links[:5]):
                href = link.get("href", "")
                text = link.get_text(strip=True)[:50]
                logger.warning(f"[PARSE]   Link {i+1}: href='{href[:100]}', text='{text}'")
    
    # If no links found, try alternative selectors
    if len(event_links) == 0:
        logger.warning(f"No event links found with '/events/' pattern. Trying alternative methods...")
        # Try finding any links that might be events
        all_links = soup.find_all("a", href=True)
        logger.info(f"Total links on page: {len(all_links)}")
        # Look for links with event-like patterns
        for link in all_links[:50]:  # Check first 50 links
            href = link.get("href", "")
            if href and ("event" in href.lower() or "paris" in href.lower()):
                logger.debug(f"Found potential event link: {href[:100]}")
    
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
        logger.warning("No events_by_date dictionary provided, creating empty file")
        # Still create a file with a summary sheet
        events_by_date = {date.today(): []}
    
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
                    # Create empty DataFrame with correct columns and a message row
                    df = pd.DataFrame({
                        "S.no": [""],
                        "Date": [target_date.isoformat()],
                        "Event name": ["No events found for this date"],
                        "Event link": [""]
                    })
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
                today = date.today()
                df = pd.DataFrame({
                    "S.no": [""],
                    "Date": [today.isoformat()],
                    "Event name": ["No events found for any date"],
                    "Event link": [""]
                })
                df.to_excel(writer, sheet_name="Summary", index=False)
                sheets_created = 1
        
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
            
            # Add delay before first request to avoid immediate rate limiting
            if day_offset == 0:
                logger.info("[MAIN] Waiting 2 seconds before first request...")
                time.sleep(2)
            else:
                # Delay between requests to avoid rate limiting
                delay = 3  # 3 seconds between requests (reduced for speed)
                logger.info(f"[MAIN] Waiting {delay} seconds before next request...")
                time.sleep(delay)
            
            # Get date-specific URL
            date_url = get_date_url(target_date)
            
            # Strategy: In CI environments, skip HTTP fetch and go straight to Selenium to save time
            # In local environments, try HTTP first, then fall back to Selenium if needed
            is_ci = os.getenv('CI') or os.getenv('GITHUB_ACTIONS') or os.getenv('RUNNER_OS')
            html = None
            initial_size = 0
            use_selenium_directly = False
            
            if is_ci:
                # Skip HTTP fetch in CI to avoid rate limiting and save time
                logger.info(f"[MAIN] CI environment detected. Skipping HTTP fetch, using Selenium directly...")
                use_selenium_directly = True
            else:
                # Try regular fetch first, then use Selenium if needed or if rate limited
                logger.info(f"[MAIN] Fetching events for {target_date}...")
                try:
                    html = fetch_page(date_url)
                    initial_size = len(html)
                    logger.info(f"[MAIN] Initial HTTP fetch returned {initial_size} characters")
                except (requests.exceptions.HTTPError, requests.exceptions.RetryError) as e:
                    error_str = str(e).lower()
                    if '429' in error_str or 'too many' in error_str:
                        logger.warning(f"[MAIN] HTTP fetch failed due to rate limiting. Falling back to Selenium...")
                        use_selenium_directly = True
                    else:
                        logger.error(f"[MAIN] HTTP fetch failed: {e}")
                        raise
            
            # Check if page has "view more" buttons that need clicking
            if html:
                html_lower = html.lower()
                has_view_more = any(phrase in html_lower for phrase in [
                    "view more", "see more", "load more", "show more"
                ])
                logger.info(f"[MAIN] Page contains 'view more' buttons: {has_view_more}")
                logger.info(f"[MAIN] Checking if Selenium is needed (content < 50KB or has 'view more' buttons)...")
            else:
                has_view_more = True  # Assume we need Selenium if HTTP failed
            
            # Also check if we got sufficient content or if we should use Selenium directly
            if use_selenium_directly or initial_size < 50000 or has_view_more:
                if use_selenium_directly:
                    logger.info(f"[MAIN] Using Selenium directly due to rate limiting")
                else:
                    logger.info(f"[MAIN] Conditions met for Selenium: size={initial_size} (< 50000) or has_view_more={has_view_more}")
                logger.info("[MAIN] Attempting Selenium fetch...")
                try:
                    selenium_html = fetch_page_with_selenium(date_url)
                    selenium_size = len(selenium_html)
                    logger.info(f"[MAIN] Selenium fetch completed: {selenium_size} characters")
                    
                    if use_selenium_directly:
                        # If HTTP failed, use Selenium result directly
                        logger.info(f"[MAIN] ✓ Using Selenium result (HTTP fetch failed due to rate limiting)")
                        html = selenium_html
                    else:
                        # Only use Selenium result if it's significantly better
                        improvement_ratio = selenium_size / initial_size if initial_size > 0 else 0
                        logger.info(f"[MAIN] Content comparison: Selenium={selenium_size} chars, HTTP={initial_size} chars, Ratio={improvement_ratio:.2f}x")
                        
                        if selenium_size > initial_size * 1.5:  # At least 50% more content
                            logger.info(f"[MAIN] ✓ Using Selenium result (improvement: {improvement_ratio:.2f}x)")
                            html = selenium_html
                        else:
                            logger.warning(f"[MAIN] ✗ Selenium didn't improve content enough. Using HTTP result.")
                            logger.warning(f"[MAIN]   Selenium: {selenium_size} chars, HTTP: {initial_size} chars")
                except Exception as e:
                    logger.error(f"[MAIN] Selenium failed with exception: {type(e).__name__}: {e}")
                    if use_selenium_directly:
                        # If Selenium fails and HTTP already failed, we're stuck
                        logger.error("[MAIN] Both HTTP and Selenium failed. Cannot proceed.")
                        raise
                    else:
                        logger.warning("[MAIN] Continuing with HTTP fetch result")
            else:
                logger.info(f"[MAIN] Regular fetch provided sufficient content ({initial_size} chars), skipping Selenium")
            
            logger.info(f"[MAIN] Final HTML size for parsing: {len(html)} characters")
            
            # Parse events for this date
            events = parse_events_for_date(html, target_date)
            
            # Always add the date to events_by_date, even if empty
            events_by_date[target_date] = events if events else []
            
            if not events:
                logger.warning(f"No events found for {target_date}")
            else:
                logger.info(f"Found {len(events)} events for {target_date}")
            
            # Delay is now handled at the start of each loop iteration
        
        # Save to Excel with separate sheets for each day
        output_path = get_output_filename()
        save_to_excel(events_by_date, output_path)
        
        total_events = sum(len(events) for events in events_by_date.values())
        events_by_day = {date: len(events) for date, events in events_by_date.items()}
        
        logger.info(f"\n{'='*60}")
        logger.info(f"[MAIN] Scraping completed successfully!")
        logger.info(f"[MAIN] Total events found: {total_events}")
        logger.info(f"[MAIN] Events by day:")
        for day, count in sorted(events_by_day.items()):
            logger.info(f"[MAIN]   {day}: {count} events")
        logger.info(f"[MAIN] Output file: {output_path}")
        logger.info(f"{'='*60}")
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
