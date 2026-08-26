from __future__ import annotations

import html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GROUPS = {
    "4B": 4,
    "5B": 5,
    "6B": 6,
    "8B": 8,
}

MIN_RECORDS = 101

# Keep only fields needed for analysis.
FIELD_MAP = (
    ("title", "title"),
    ("name", "name"),
    ("dlcCode", "dlc"),
    ("pattern", "pattern"),
    ("level", "level"),
    ("floor", "floor"),
    ("floorName", "floorName"),
    ("maxRating", "maxRating"),
    ("score", "score"),
    ("maxCombo", "maxCombo"),
    ("rating", "rating"),
    ("djpower", "djpower"),
    ("maxDjpower", "maxDjpower"),
)


def request_json(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "djmax-snapshot-sync/1.7",
        },
        method="GET",
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def build_url(template: str, source_id: str, button: int) -> str:
    encoded_id = urllib.parse.quote(source_id, safe="")
    return template.format(id=encoded_id, button=button)


def extract(payload):
    if not isinstance(payload, dict):
        raise ValueError("unsupported_response")

    if isinstance(payload.get("records"), list):
        records = payload["records"]
        count = int(payload.get("count", len(records)))
        ok = bool(payload.get("success", True))
        return ok, count, records

    data = payload.get("data")

    if isinstance(data, dict) and isinstance(data.get("records"), list):
        records = data["records"]
        count = int(data.get("count", len(records)))
        ok = bool(payload.get("success", data.get("success", True)))
        return ok, count, records

    raise ValueError("unsupported_response")


def compact_record(record):
    if not isinstance(record, dict):
        return None

    out = {}

    for source_key, public_key in FIELD_MAP:
        if source_key in record:
            out[public_key] = record[source_key]

    return out


def page_shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,noarchive">
<title>{html.escape(title)}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:760px;margin:32px auto;padding:0 18px;line-height:1.5}}
.card{{padding:18px;border:1px solid #ddd;border-radius:16px;margin:14px 0}}
.download{{display:inline-block;padding:14px 18px;border:1px solid #111;border-radius:12px;text-decoration:none;font-weight:700}}
small{{opacity:.7}}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def main() -> int:
    source_id = os.environ.get("SOURCE_ID", "").strip()
    template = os.environ.get("SOURCE_TEMPLATE", "").strip()

    if not source_id or not template:
        print("missing repository secrets", file=sys.stderr)
        return 2

    if "{id}" not in template or "{button}" not in template:
        print("invalid source template", file=sys.stderr)
        return 2

    site = Path("build/site")
    site.mkdir(parents=True, exist_ok=True)

    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    snapshot = {
        "schema": "djmax-analysis-snapshot-v1.7",
        "generatedAt": generated,
        "minimumRecordsPerButton": MIN_RECORDS,
        "buttons": {},
    }

    any_ok = False

    for label, button in GROUPS.items():
        try:
            payload = request_json(
                build_url(
                    template,
                    source_id,
                    button,
                )
            )

            ok, count, raw_records = extract(payload)

            if not ok:
                raise ValueError("source_rejected")

            records = []

            for raw in raw_records:
                item = compact_record(raw)

                if item is not None:
                    records.append(item)

            snapshot["buttons"][label] = {
                "ok": True,
                "count": count,
                "analysisAllowed": count >= MIN_RECORDS,
                "need": max(0, MIN_RECORDS - count),
                "records": records,
            }

            any_ok = True
            print(f"{label}: ok ({count})")

        except urllib.error.HTTPError as exc:
            snapshot["buttons"][label] = {
                "ok": False,
                "count": 0,
                "analysisAllowed": False,
                "need": MIN_RECORDS,
                "records": [],
                "error": f"http_{exc.code}",
            }

            print(f"{label}: failed (http_{exc.code})")

        except Exception as exc:
            code = (
                "unsupported_response"
                if str(exc) == "unsupported_response"
                else "fetch_failed"
            )

            snapshot["buttons"][label] = {
                "ok": False,
                "count": 0,
                "analysisAllowed": False,
                "need": MIN_RECORDS,
                "records": [],
                "error": code,
            }

            print(f"{label}: failed ({code})")

    snapshot_path = site / "analysis_snapshot.json"

    snapshot_path.write_text(
        json.dumps(
            snapshot,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    cards = []

    for label in GROUPS:
        info = snapshot["buttons"][label]

        if info["ok"]:
            cards.append(
                f'<div class="card"><h2>{label}</h2>'
                f'<p>records: {info["count"]}</p>'
                f'<p>analysisAllowed: {str(info["analysisAllowed"]).lower()}</p>'
                f'<p>need: {info["need"]}</p></div>'
            )
        else:
            cards.append(
                f'<div class="card"><h2>{label}</h2>'
                f'<p>fetch failed</p></div>'
            )

    body = (
        "<h1>Dataset snapshot</h1>"
        '<p><small>v1.7 attachment mode</small></p>'
        f'<p><small>updated: {html.escape(generated)}</small></p>'
        '<p><a class="download" href="analysis_snapshot.json" download>'
        'Download analysis snapshot</a></p>'
        + "".join(cards)
        + "<p><small>Attach the downloaded JSON file to the ChatGPT project.</small></p>"
    )

    (site / "index.html").write_text(
        page_shell("Dataset snapshot", body),
        encoding="utf-8",
    )

    # Ask Cloudflare Pages to serve the snapshot as a download.
    (site / "_headers").write_text(
        """/analysis_snapshot.json
  Content-Type: application/json; charset=utf-8
  Content-Disposition: attachment; filename="analysis_snapshot.json"
  Cache-Control: no-cache
""",
        encoding="utf-8",
    )

    (site / ".nojekyll").write_text("", encoding="utf-8")

    if not any_ok:
        print("all groups failed", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
