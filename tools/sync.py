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

# Public labels are deliberately neutral.
# The private ChatGPT project maps s0/s1/s2/s3 to the actual button modes.
GROUPS = {"s0": 4, "s1": 5, "s2": 6, "s3": 8}
MIN_RECORDS = 101
CHUNK_SIZE = 120

# Only fields needed for analysis are exported.
# Keys are shortened to make the public dataset less self-explanatory.
FIELD_MAP = (
    ("title", "i"),
    ("name", "n"),
    ("dlcCode", "c"),
    ("pattern", "p"),
    ("level", "l"),
    ("floor", "f"),
    ("floorName", "q"),
    ("maxRating", "a"),
    ("score", "s"),
    ("maxCombo", "m"),
    ("rating", "r"),
    ("djpower", "d"),
    ("maxDjpower", "z"),
)


def request_json(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "static-dataset-sync/1.6",
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
    for src, dst in FIELD_MAP:
        if src in record:
            out[dst] = record[src]
    return out


def page_shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,noarchive">
<title>{html.escape(title)}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;line-height:1.45}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}
a{{text-decoration:none}}
small{{opacity:.72}}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def write_group_pages(site: Path, group: str, records):
    folder = site / group
    folder.mkdir(parents=True, exist_ok=True)

    links = []
    total = len(records)

    for idx, start in enumerate(range(0, total, CHUNK_SIZE), start=1):
        chunk = records[start:start + CHUNK_SIZE]
        filename = f"{idx:03d}.html"

        lines = "\n".join(
            html.escape(
                json.dumps(
                    item,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            for item in chunk
        )

        end = start + len(chunk)

        body = (
            f"<h1>{group} / {idx:03d}</h1>"
            f"<p><small>range {start + 1}-{end} / {total}</small></p>"
            f"<pre>{lines}</pre>"
            f'<p><a href="../index.html">index</a></p>'
        )

        (folder / filename).write_text(
            page_shell(f"{group}-{idx:03d}", body),
            encoding="utf-8",
        )

        links.append((filename, start + 1, end))

    return links


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

    generated = (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    summary = {}
    group_links = {}
    any_ok = False

    for group, button in GROUPS.items():
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

            for record in raw_records:
                compact = compact_record(record)

                if compact is not None:
                    records.append(compact)

            # count from the API is kept for the 101-record gate.
            summary[group] = {
                "ok": True,
                "n": count,
                "need": max(0, MIN_RECORDS - count),
            }

            group_links[group] = write_group_pages(
                site,
                group,
                records,
            )

            any_ok = True

            print(f"{group}: ok ({count})")

        except urllib.error.HTTPError as exc:
            summary[group] = {
                "ok": False,
                "n": 0,
                "need": MIN_RECORDS,
                "error": f"http_{exc.code}",
            }

            group_links[group] = []

            print(f"{group}: failed (http_{exc.code})")

        except Exception as exc:
            code = (
                "unsupported_response"
                if str(exc) == "unsupported_response"
                else "fetch_failed"
            )

            summary[group] = {
                "ok": False,
                "n": 0,
                "need": MIN_RECORDS,
                "error": code,
            }

            group_links[group] = []

            print(f"{group}: failed ({code})")

    rows = []

    for group in GROUPS:
        info = summary[group]

        if not info.get("ok"):
            rows.append(
                f"<section><h2>{group}</h2>"
                f"<p>ok=0; n=0; need={MIN_RECORDS}</p></section>"
            )

            continue

        links_html = " ".join(
            f'<a href="{group}/{html.escape(filename)}">'
            f"p{idx:03d}</a>"
            for idx, (filename, _, _) in enumerate(
                group_links[group],
                start=1,
            )
        )

        rows.append(
            f"<section><h2>{group}</h2>"
            f"<p>ok=1; n={info['n']}; need={info['need']}</p>"
            f"<p>{links_html}</p></section>"
        )

    body = (
        "<h1>Dataset</h1>"
        '<p><small>v=1.6-web</small></p>'
        f'<p><small>ts={html.escape(generated)}</small></p>'
        + "".join(rows)
    )

    (site / "index.html").write_text(
        page_shell("Dataset", body),
        encoding="utf-8",
    )

    # Useful for the project to validate the build version without exposing identity.
    (site / "meta.json").write_text(
        json.dumps(
            {
                "v": "1.6-web",
                "ts": generated,
                "g": summary,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    # Keep Pages from invoking Jekyll semantics on generated files.
    (site / ".nojekyll").write_text(
        "",
        encoding="utf-8",
    )

    if not any_ok:
        print(
            "all groups failed",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
