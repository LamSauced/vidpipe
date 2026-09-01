"""The job runner: one at a time, and Stop actually stops — even mid-await."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["VIDPIPE_DATA"] = tempfile.mkdtemp(prefix="vidpipe-runner-")

from app import pipeline  # noqa: E402


async def main():
    r = pipeline.Runner()

    # A job parked in a long await — the case that used to wedge everything.
    async def hang():
        await asyncio.sleep(600)

    r.start(hang(), "stuck job")
    await asyncio.sleep(0.2)
    assert r.busy, "should be busy"
    print("  ok  job holds the slot")

    refusal = None
    try:
        r.start(hang(), "second job")
        raise AssertionError("a second job should be refused")
    except RuntimeError as exc:
        refusal = str(exc)
    assert "stuck job" in refusal and "Stop" in refusal, refusal
    print(f"  ok  second job refused: {refusal[:64]}")

    # Stop must free the slot without waiting out the 600s sleep.
    r.cancel()
    await asyncio.sleep(0.3)
    assert not r.busy, "Stop should have freed the slot"
    print("  ok  Stop frees a job parked inside an await")

    # ...and the next job starts straight away.
    done = asyncio.Event()

    async def quick():
        done.set()

    r.start(quick(), "next job")
    await asyncio.sleep(0.2)
    assert done.is_set() and not r.busy
    print("  ok  next job runs immediately afterwards")

    # A failing job frees the slot rather than wedging it.
    async def boom():
        raise ValueError("kaboom")

    r.start(boom(), "failing job")
    await asyncio.sleep(0.2)
    assert not r.busy
    assert any(e.get("type") == "failed" and "kaboom" in e.get("message", "")
               for e in pipeline.bus.log), "failure should be reported"
    print("  ok  a failing job frees the slot and reports why")

    # Cancelled is reported, not swallowed as a failure.
    pipeline.bus.log.clear()
    r.start(hang(), "another stuck job")
    await asyncio.sleep(0.1)
    r.cancel()
    await asyncio.sleep(0.3)
    types = [e.get("type") for e in pipeline.bus.log]
    assert "cancelled" in types and "failed" not in types, types
    assert types[-1] == "idle", types
    print("  ok  cancelling reports 'cancelled', then idle")

    # A wedged job is displaced by the next request rather than locking the app
    # until someone restarts the server.
    r2 = pipeline.Runner()
    r2.STALE_AFTER = 0.3
    r2.start(hang(), "ancient job")
    await asyncio.sleep(0.5)
    ran = asyncio.Event()

    async def later():
        ran.set()

    r2.start(later(), "new job")          # should not raise
    await asyncio.sleep(0.3)
    assert ran.is_set(), "the new job should have taken over"
    assert r2.label == "new job", r2.label
    print("  ok  a stale job is displaced by the next request")

    # A job that is merely slow is still protected.
    r3 = pipeline.Runner()
    r3.STALE_AFTER = 600
    r3.start(hang(), "slow but fine")
    await asyncio.sleep(0.1)
    refused = None
    try:
        r3.start(hang(), "impatient")
        raise AssertionError("a fresh job should still be refused")
    except RuntimeError as exc:
        refused = str(exc)
    assert "slow but fine" in refused
    r3.cancel()
    await asyncio.sleep(0.2)
    print("  ok  a merely slow job is not displaced")

    print("\nall runner assertions passed")


asyncio.run(main())
