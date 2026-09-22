# Main-Tauber-Kreis News Monitor

Checks the Main-Tauber-Kreis RSS feed every day at **20:00 Europe/Berlin** and sends each new item to Bark as a separate bilingual German/Chinese notification.

## RSS feed

`https://www.main-tauber-kreis.de/custom/xml.php?id=2894.156&max=25&kat=2177.1683&sort_by=Datum%20DESC`

## What it does

- Runs every day at 20:00 in the `Europe/Berlin` timezone.
- Selects RSS items published on the current Berlin calendar day.
- Deduplicates by item URL (with title/date fallback).
- Keeps the original German title.
- Translates the title to Simplified Chinese.
- Sends one Bark notification per new RSS item.
- Includes the original news URL in the message and as Bark's tap-to-open URL.
- Provides a manual test mode that sends exactly one latest RSS item even when nothing was published today.

## Required GitHub secret

Create a repository secret named:

`BARK_KEY`

Its value must be only the Bark device key, **not** the full `https://api.day.app/...` URL.

Path in GitHub:

**Settings → Secrets and variables → Actions → New repository secret**

## Manual test

Open **Actions → Main-Tauber RSS to Bark → Run workflow**.

Leave **test_mode** enabled. The workflow will send exactly one notification using the latest RSS item.

## Files

- `monitor.py` — RSS parsing, date filtering, translation, deduplication and Bark push.
- `state.json` — remembers already-sent items.
- `.github/workflows/monitor.yml` — scheduled/manual GitHub Actions workflow.
