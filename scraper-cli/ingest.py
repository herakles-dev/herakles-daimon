#!/usr/bin/env python3
"""
Ingest staged JSONL files into the Daimon Play database.

Reads JSONL files from the staging directory, inserts videos into
the database via the running backend API or direct psql.
"""

import json
import subprocess
import sys
from pathlib import Path

STAGING_DIR = Path(__file__).parent / "staging"
DB_CONTAINER = "daimon-db-1"
DB_NAME = "daimon_play"
DB_USER = "play"


def psql(sql: str) -> str:
    """Run SQL in the DB container and return stdout."""
    result = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME, "-t", "-A"],
        input=sql,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def ingest_file(filepath: Path) -> int:
    """Insert videos from a JSONL file. Returns count of new insertions."""
    inserted = 0

    with filepath.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                v = json.loads(line)
            except json.JSONDecodeError:
                print(f"  [!] Skipping invalid JSON line", file=sys.stderr)
                continue

            url = v.get("url", "")
            title = v.get("title", "").replace("'", "''")
            channel = v.get("channel", "").replace("'", "''")
            duration = int(v.get("duration") or 0)
            thumbnail = v.get("thumbnail", "")
            description = v.get("description", "").replace("'", "''")[:2000]

            if not url:
                continue

            sql = f"""
                INSERT INTO videos (url, source, title, channel, duration_sec, thumbnail_url, description)
                VALUES ('{url}', 'youtube', '{title}', '{channel}', {duration}, '{thumbnail}', '{description}')
                ON CONFLICT (url) DO NOTHING
                RETURNING id;
            """

            result = psql(sql)
            if result:
                video_id = result.strip()
                # Create default tags
                psql(f"""
                    INSERT INTO video_tags (video_id, pacing, stimulation, novelty, vibe)
                    VALUES ({video_id}, 5, 5, 5, 'unknown')
                    ON CONFLICT (video_id) DO NOTHING;
                """)
                inserted += 1

    return inserted


def main():
    files = sorted(STAGING_DIR.glob("*.jsonl"))
    files = [f for f in files if f.parent.name != "processed"]

    if not files:
        print("[!] No staging files found")
        return

    processed_dir = STAGING_DIR / "processed"
    processed_dir.mkdir(exist_ok=True)

    total = 0
    for f in files:
        count = ingest_file(f)
        total += count
        print(f"  [+] {f.name}: {count} new video(s)")
        f.rename(processed_dir / f.name)

    print(f"\n  Total inserted: {total}")


if __name__ == "__main__":
    main()
