from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GROUPS = {"g4": 4, "g5": 5, "g6": 6, "g8": 8}
MIN_RECORDS = 101

DROP_KEYS = {
    "nickname", "userno", "user_no", "userid", "user_id",
    "archiveno", "archive_no", "memberno", "member_no",
    "profile", "avatar", "email", "comment", "memo",
    "createdat", "created_at", "updatedat", "updated_at",
    "playedat", "played_at", "lastplayedat", "last_played_at",
    "account", "member", "user",
}

DROP_FRAGMENTS = (
    "token", "cookie", "session", "authorization", "password"
)


def _safe(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            low = str(key).lower()
            if low in DROP_KEYS or any(
                fragment in low for fragment in DROP_FRAGMENTS
            ):
                continue
            out[key] = _safe(item)
        return out

    if isinstance(value, list):
        return [_safe(item) for item in value]

    return value


def _request_json(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "cache-sync/1.0",
        },
        method="GET",
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def _build_url(template: str, source_id: str, button: int) -> str:
    encoded_id = urllib.parse.quote(source_id, safe="")
    return template.format(id=encoded_id, button=button)


def _extract(payload):
    if isinstance(payload, dict):
        if isinstance(payload.get("records"), list):
            records = payload["records"]
            count = payload.get("count", len(records))
            success = payload.get("success", True)
            return bool(success), int(count), records

        data = payload.get("data")

        if isinstance(data, dict) and isinstance(data.get("records"), list):
            records = data["records"]
            count = data.get("count", len(records))
            success = payload.get(
                "success",
                data.get("success", True),
            )
            return bool(success), int(count), records

    raise ValueError("unsupported_response")


def main() -> int:
    source_id = os.environ.get("SOURCE_ID", "").strip()
    template = os.environ.get("SOURCE_TEMPLATE", "").strip()

    if not source_id or not template:
        print("missing repository secrets", file=sys.stderr)
        return 2

    if "{id}" not in template or "{button}" not in template:
        print("invalid source template", file=sys.stderr)
        return 2

    result = {
        "v": "1.5-min",
        "ts": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "g": {},
    }

    for group, button in GROUPS.items():
        try:
            url = _build_url(template, source_id, button)
            payload = _request_json(url)

            ok, count, records = _extract(payload)

            if not ok:
                raise ValueError("source_rejected")

            clean_records = _safe(records)

            result["g"][group] = {
                "ok": True,
                "n": count,
                "need": max(0, MIN_RECORDS - count),
                "items": clean_records,
            }

            print(f"{group}: ok ({count})")

        except urllib.error.HTTPError as exc:
            result["g"][group] = {
                "ok": False,
                "n": 0,
                "need": MIN_RECORDS,
                "items": [],
                "error": f"http_{exc.code}",
            }

            print(f"{group}: failed (http_{exc.code})")

        except Exception as exc:
            code = (
                "unsupported_response"
                if str(exc) == "unsupported_response"
                else "fetch_failed"
            )

            result["g"][group] = {
                "ok": False,
                "n": 0,
                "need": MIN_RECORDS,
                "items": [],
                "error": code,
            }

            print(f"{group}: failed ({code})")

    output = Path("build/data.json")
    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    if not any(group.get("ok") for group in result["g"].values()):
        print("all groups failed", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
