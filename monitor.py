import html
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import feedparser
import requests
from dateutil import parser as dateparser
from zoneinfo import ZoneInfo

RSS_URL = "https://www.main-tauber-kreis.de/custom/xml.php?id=2894.156&max=25&kat=2177.1683&sort_by=Datum%20DESC"
BARK_API = "https://api.day.app"
STATE_FILE = Path("state.json")
BERLIN = ZoneInfo("Europe/Berlin")
USER_AGENT = "Mozilla/5.0 (compatible; main-tauber-news-monitor/1.1)"


def load_state():
    if not STATE_FILE.exists():
        return {"sent": [], "last_checked": None}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state.json must contain an object")
        data.setdefault("sent", [])
        data.setdefault("last_checked", None)
        return data
    except Exception as exc:
        print(f"Warning: could not read state.json: {exc}")
        return {"sent": [], "last_checked": None}


def save_state(state):
    state["sent"] = state.get("sent", [])[-500:]
    state["last_checked"] = datetime.now(BERLIN).isoformat()
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_entries():
    response = requests.get(
        RSS_URL,
        timeout=30,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()

    feed = feedparser.parse(response.content)
    if feed.bozo and not feed.entries:
        raise RuntimeError(f"RSS parse failed: {feed.bozo_exception}")
    if not feed.entries:
        raise RuntimeError("RSS feed contains no entries")
    return feed.entries


def entry_url(entry):
    return (entry.get("link") or entry.get("id") or "").strip()


def entry_title(entry):
    return (entry.get("title") or "Ohne Titel").strip()


def entry_datetime(entry):
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if raw:
            try:
                dt = dateparser.parse(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=BERLIN)
                return dt.astimezone(BERLIN)
            except Exception:
                pass

    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                dt = datetime(*parsed[:6], tzinfo=ZoneInfo("UTC"))
                return dt.astimezone(BERLIN)
            except Exception:
                pass

    return None


def entry_key(entry):
    url = entry_url(entry)
    if url:
        return url
    dt = entry_datetime(entry)
    dt_text = dt.isoformat() if dt else "unknown-date"
    return f"{entry_title(entry)}|{dt_text}"


def _request_with_retry(url, *, params, attempts=3):
    last_error = None

    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=20,
                headers={"User-Agent": USER_AGENT},
            )

            if response.status_code == 429 or 500 <= response.status_code < 600:
                raise RuntimeError(
                    f"HTTP {response.status_code}: {response.text[:200]}"
                )

            response.raise_for_status()
            return response

        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)

    raise RuntimeError(str(last_error))


def translate_google(title):
    response = _request_with_retry(
        "https://translate.googleapis.com/translate_a/single",
        params={
            "client": "gtx",
            "sl": "de",
            "tl": "zh-CN",
            "dt": "t",
            "q": title,
        },
    )

    data = response.json()
    segments = data[0] if isinstance(data, list) and data else []
    translated = "".join(
        segment[0]
        for segment in segments
        if isinstance(segment, list) and segment and segment[0]
    ).strip()

    if not translated:
        raise RuntimeError("Google translation response was empty")

    return translated


def translate_mymemory(title):
    errors = []

    # MyMemory may accept either zh-CN or the generic zh code depending
    # on the backend language model, so try both.
    for target in ("zh-CN", "zh"):
        try:
            response = _request_with_retry(
                "https://api.mymemory.translated.net/get",
                params={
                    "q": title,
                    "langpair": f"de|{target}",
                },
            )

            data = response.json()
            status = data.get("responseStatus")
            if status not in (None, 200, "200"):
                raise RuntimeError(
                    f"MyMemory returned status {status}: "
                    f"{data.get('responseDetails', '')}"
                )

            translated = (
                data.get("responseData", {}).get("translatedText", "")
                if isinstance(data, dict)
                else ""
            )
            translated = html.unescape(str(translated)).strip()

            if not translated:
                raise RuntimeError("MyMemory translation response was empty")

            if translated.casefold() == title.casefold():
                raise RuntimeError("MyMemory returned the original German text")

            return translated

        except Exception as exc:
            errors.append(f"{target}: {exc}")

    raise RuntimeError(" | ".join(errors))


def translate_title(title):
    errors = []

    for name, translator in (
        ("MyMemory", translate_mymemory),
        ("Google", translate_google),
    ):
        try:
            translated = translator(title)
            print(f"Translation succeeded via {name}: {translated}")
            return translated
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            print(f"Translation provider {name} failed: {exc}", file=sys.stderr)

    print(
        "All translation providers failed: " + " | ".join(errors),
        file=sys.stderr,
    )
    return "（中文翻译暂时失败，请点击原文查看）"


def send_bark(german_title, chinese_title, news_url):
    bark_key = os.environ.get("BARK_KEY", "").strip()
    if not bark_key:
        raise RuntimeError("Missing GitHub Actions secret: BARK_KEY")

    # Use Bark's GET URL format, matching the format verified manually
    # on the user's iPhone:
    #   /:key/:title/:body
    title = "Main-Tauber-Kreis"
    body = f"{german_title}\n{chinese_title}\n{news_url}"

    bark_url = (
        f"{BARK_API}/{quote(bark_key, safe='')}/"
        f"{quote(title, safe='')}/{quote(body, safe='')}"
    )

    response = requests.get(
        bark_url,
        params={
            "url": news_url,
            "group": "Main-Tauber-Kreis",
        },
        timeout=20,
        headers={"User-Agent": USER_AGENT},
    )

    print(f"Bark HTTP status: {response.status_code}")
    print(f"Bark response: {response.text[:1000]}")
    response.raise_for_status()

    try:
        data = response.json()
        if isinstance(data, dict) and data.get("code") not in (None, 200):
            raise RuntimeError(f"Bark returned an error: {data}")
    except ValueError:
        pass

    print(f"Bark sent: {german_title}")


def main():
    test_mode = os.environ.get("TEST_MODE", "false").lower() == "true"
    entries = fetch_entries()
    state = load_state()
    sent = set(state.get("sent", []))
    now = datetime.now(BERLIN)

    dated_entries = []
    for entry in entries:
        dt = entry_datetime(entry)
        dated_entries.append((dt or datetime.min.replace(tzinfo=BERLIN), entry))
    dated_entries.sort(key=lambda item: item[0])

    if test_mode:
        latest = max(dated_entries, key=lambda item: item[0])[1]
        title_de = entry_title(latest)
        title_zh = translate_title(title_de)
        url = entry_url(latest)

        if not url:
            raise RuntimeError("Latest RSS item has no usable URL")

        send_bark(title_de, title_zh, url)
        save_state(state)
        print("Manual test completed: exactly one notification sent.")
        return

    candidates = []
    for dt, entry in dated_entries:
        key = entry_key(entry)
        if not dt or dt.date() != now.date():
            continue
        if key in sent:
            continue
        candidates.append(entry)

    if not candidates:
        print(f"No new RSS items for {now.date().isoformat()}.")
        save_state(state)
        return

    failures = []

    for entry in candidates:
        key = entry_key(entry)
        title_de = entry_title(entry)
        url = entry_url(entry)

        if not url:
            print(f"Skipping item without URL: {title_de}")
            continue

        title_zh = translate_title(title_de)

        try:
            send_bark(title_de, title_zh, url)
            state.setdefault("sent", []).append(key)
            sent.add(key)
            save_state(state)
        except Exception as exc:
            failures.append(f"{title_de}: {exc}")
            print(f"Failed to send: {title_de}: {exc}", file=sys.stderr)

    save_state(state)

    if failures:
        raise RuntimeError("Some Bark pushes failed:\n" + "\n".join(failures))


if __name__ == "__main__":
    main()
