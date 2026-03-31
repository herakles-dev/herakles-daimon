"""Re-transcode tracks with corrupt HLS playlists (TARGETDURATION:0).

Uses asyncio.Semaphore(6) to run 6 FFmpeg processes concurrently.
"""
import asyncio
import shutil
from pathlib import Path
from transcoder import transcode_to_hls
import db as _db

CORRUPT = [
    88, 89, 99, 108, 109, 110, 111, 112, 113, 115, 116, 117, 118,
    119, 120, 121, 123, 124, 126, 127, 128, 129, 130, 132, 133, 134,
    135, 136, 137, 138, 139, 140, 141, 142, 143, 144, 147, 148, 152,
]


async def retranscode_one(tid: int, pool, sem: asyncio.Semaphore) -> tuple[int, bool, str]:
    """Retranscode a single track under the semaphore. Returns (tid, success, message)."""
    async with sem:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, file_path FROM tracks WHERE id = $1", tid
            )

        if not row or not row["file_path"]:
            return tid, False, "skip (no row/path)"

        src = Path(row["file_path"])
        if not src.exists():
            return tid, False, f"skip (file missing: {src})"

        hls_dir = Path("/music/hls") / str(tid)
        if hls_dir.exists():
            shutil.rmtree(hls_dir)

        result = await transcode_to_hls(str(src), tid)
        if result:
            m3u8 = hls_dir / "128k.m3u8"
            segs = m3u8.read_text().count("EXTINF") if m3u8.exists() else 0
            return tid, True, f"OK ({segs} segments)"
        else:
            return tid, False, "FAILED"


async def main():
    pool = await _db.get_pool()
    sem = asyncio.Semaphore(6)
    fixed = 0
    failed = 0

    print(f"Starting parallel retranscode of {len(CORRUPT)} tracks (concurrency=6)...")

    tasks = [retranscode_one(tid, pool, sem) for tid in CORRUPT]

    for coro in asyncio.as_completed(tasks):
        tid, success, msg = await coro
        status = "OK" if success else "FAIL"
        print(f"[{status}] track {tid}: {msg}")
        if success:
            fixed += 1
        else:
            failed += 1

    print(f"\nDone: {fixed} fixed, {failed} failed out of {len(CORRUPT)}")


if __name__ == "__main__":
    asyncio.run(main())
