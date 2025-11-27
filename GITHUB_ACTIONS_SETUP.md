# GitHub Actions Setup Guide

## Is GitHub Actions Free?

**Yes! GitHub Actions is free for:**
- ✅ **Public repositories**: Unlimited free minutes
- ✅ **Private repositories**: 
  - **Free plan**: 2,000 minutes/month (free)
  - **Pro plan**: 3,000 minutes/month (free)
  - Additional minutes are billed at $0.008/minute

**For this project**: Since it runs once per week (~4-5 minutes per run), you'll use approximately **20-25 minutes per month**, which is well within the free tier even for private repos!

## Setup Instructions

### Step 1: Commit the Workflow File

The workflow file is already created at `.github/workflows/scrape-events.yml`. You just need to commit and push it:

```bash
git add .github/workflows/scrape-events.yml
git commit -m "Add GitHub Actions workflow for automated scraping"
git push
```

### Step 2: Enable Workflow Permissions

1. Go to your GitHub repository
2. Click **Settings** → **Actions** → **General**
3. Under **Workflow permissions**, select:
   - ✅ **Read and write permissions**
   - ✅ **Allow GitHub Actions to create and approve pull requests**
4. Click **Save**

### Step 3: Test the Workflow

You can test it immediately:

1. Go to your repository on GitHub
2. Click the **Actions** tab
3. You'll see "Scrape Shotgun Paris Events" workflow
4. Click on it, then click **Run workflow** → **Run workflow** (green button)
5. Watch it run in real-time!

### Step 4: Verify It Works

After the workflow completes:
1. Check the **Actions** tab to see if it succeeded (green checkmark)
2. Go to the **output/** folder in your repository
3. You should see the Excel file: `shotgun_paris_events_YYYY-MM-DD.xlsx`

## How It Works

The workflow is configured to:

1. **Run automatically**: Every Monday at 9:00 AM UTC (scheduled via cron)
2. **Run manually**: You can trigger it anytime via the Actions tab
3. **What it does**:
   - Sets up Python 3.11
   - Installs Chrome and ChromeDriver (for Selenium)
   - Installs Python dependencies
   - Runs the scraper (fetches events for next 7 days)
   - Commits the Excel file to the repository
   - Pushes the changes

## Workflow Schedule

The cron expression `'0 9 * * 1'` means:
- **Day**: Monday (1)
- **Time**: 9:00 AM UTC
- **Frequency**: Weekly

To change the schedule, edit `.github/workflows/scrape-events.yml` and modify the cron expression.

### Common Cron Examples:
- `'0 9 * * 1'` - Every Monday at 9 AM UTC
- `'0 9 * * *'` - Every day at 9 AM UTC
- `'0 */6 * * *'` - Every 6 hours
- `'0 0 * * 0'` - Every Sunday at midnight UTC

## Troubleshooting

### Workflow Fails to Commit

If you see permission errors:
1. Go to **Settings** → **Actions** → **General**
2. Ensure **Workflow permissions** is set to "Read and write"
3. Re-run the workflow

### Chrome Installation Issues

If Chrome installation fails, the workflow will fall back to regular requests (without Selenium), which still works but may miss some events.

### No Excel File Appears

Check the workflow logs:
1. Go to **Actions** tab
2. Click on the failed/successful run
3. Expand each step to see detailed logs
4. Look for error messages

## Monitoring Usage

To check your GitHub Actions usage:
1. Go to your GitHub profile
2. Click **Settings** → **Billing**
3. Scroll to **GitHub Actions** section
4. See your monthly usage and remaining free minutes

## Next Steps

Once set up:
- ✅ The scraper will run automatically every Monday
- ✅ Excel files will be committed to your repository
- ✅ You can download them anytime from the `output/` folder
- ✅ Each file contains events for the next 7 days in separate sheets

Enjoy automated event scraping! 🎉

