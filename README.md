# Shotgun Paris Events Scraper

A Python web scraper that extracts upcoming events from [Shotgun.live](https://shotgun.live/en/cities/paris) for Paris and saves them to an Excel file.

## Features

- 🎯 Scrapes events for the next 7 days
- 📅 Automatically generates Excel files with date in filename
- 🔄 Runs automatically every Monday via GitHub Actions
- 📊 Clean, organized Excel output with serial numbers
- 🛡️ Robust error handling and logging
- 🚫 Duplicate event detection

## Setup

### Prerequisites

- Python 3.8 or higher
- pip

### Installation

1. Clone the repository:
```bash
git clone https://github.com/anujmarisetty/WebScrape_events.git
cd WebScrape_events
```

2. Install dependencies:
```bash
cd src
pip install -r requirements.txt
```

## Usage

### Run locally

```bash
cd src
python main.py
```

The script will:
1. Fetch events from the Shotgun Paris page
2. Parse events for the next 7 days
3. Save them to `output/shotgun_paris_events_YYYY-MM-DD.xlsx`

### Output Format

The Excel file contains:
- **S.no**: Serial number
- **Date**: Event date (YYYY-MM-DD format)
- **Event name**: Name of the event
- **Event link**: Full URL to the event page

## GitHub Actions

The repository includes a GitHub Actions workflow that:
- Runs every Monday at 9:00 AM UTC
- Scrapes the latest events
- Commits the Excel file to the repository
- Creates a new file with the current date in the filename

The workflow file is located at `.github/workflows/scrape-events.yml`.

## Project Structure

```
WebScrape_events/
├── src/
│   ├── main.py                 # Main scraper script
│   └── requirements.txt        # Python dependencies
├── README.md                   # This file
├── .gitignore                 # Git ignore rules
├── .github/
│   └── workflows/
│       └── scrape-events.yml  # GitHub Actions workflow
└── output/                    # Output directory for Excel files
    └── .gitkeep
```

## How It Works

1. **Fetching**: Uses `requests` to fetch the HTML from the Shotgun Paris page
2. **Parsing**: Uses `BeautifulSoup` to parse HTML and find:
   - Day headers (e.g., "Thu 27 Nov")
   - Event links under each day
   - Event names and URLs
3. **Date Parsing**: Converts day headers to actual dates, handling year rollovers
4. **Deduplication**: Prevents duplicate events from being added
5. **Export**: Saves to Excel using `pandas` and `openpyxl`

## Error Handling

The script includes comprehensive error handling for:
- Network request failures
- HTML parsing errors
- Date parsing issues
- File permission errors
- Missing data validation

All errors are logged with appropriate messages.

## Dependencies

- `requests`: HTTP library for fetching web pages
- `beautifulsoup4`: HTML parsing library
- `pandas`: Data manipulation and Excel export
- `openpyxl`: Excel file writing engine
- `python-dateutil`: Date parsing utilities
- `lxml`: Fast XML/HTML parser (optional but recommended)

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is open source and available under the MIT License.

## Disclaimer

This scraper is for educational and personal use only. Please respect the website's terms of service and robots.txt file. Consider adding delays between requests if scraping frequently.

