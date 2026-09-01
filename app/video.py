"""Pull the final frame out of a rendered clip so the next segment can continue from it."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


class FrameError(RuntimeError):
    pass


async def extract_last_frame(video: Path, dest: Path, ffmpeg: str = "ffmpeg") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which(ffmpeg):
        for args in (
            [ffmpeg, "-y", "-sseof", "-0.5", "-i", str(video), "-update", "1",
             "-frames:v", "1", "-q:v", "2", str(dest)],
            [ffmpeg, "-y", "-i", str(video), "-vf", "reverse", "-frames:v", "1",
             "-q:v", "2", str(dest)],
        ):
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                return dest
        raise FrameError(f"ffmpeg couldn't read {video.name}: {err.decode()[-300:]}")

    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise FrameError(
            "No ffmpeg on PATH. Install ffmpeg, set its path in Settings, "
            "or pip install opencv-python-headless."
        ) from exc

    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame = None
    if total > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total - 1))
        ok, frame = cap.read()
        if not ok:
            frame = None
    if frame is None:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        while True:
            ok, img = cap.read()
            if not ok:
                break
            frame = img
    cap.release()
    if frame is None:
        raise FrameError(f"Couldn't decode any frame from {video.name}.")
    cv2.imwrite(str(dest), frame)
    return dest


async def probe_duration(path: Path, ffprobe: str = "ffprobe") -> float:
    """Length of a clip in seconds, for placing crossfade offsets."""
    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    try:
        return float(out.decode().strip())
    except ValueError:
        raise FrameError(f"Couldn't read the length of {path.name}: {err.decode()[-200:]}")


async def crossfade(clips: list[Path], dest: Path, seconds: float,
                    ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> Path:
    """Join clips with an overlapping fade between each pair.

    Each transition eats `seconds` from the running time, since the clips
    overlap rather than butt together — three 15s clips with a 0.5s fade make
    44s, not 45s. Audio is cross-faded over the same window.
    """
    durations = [await probe_duration(c, ffprobe) for c in clips]
    shortest = min(durations)
    if seconds >= shortest:
        raise FrameError(
            f"A {seconds:g}s crossfade needs clips longer than that; the shortest "
            f"here is {shortest:.1f}s.")

    has_audio = await _has_audio(clips[0], ffprobe)
    args = [ffmpeg, "-y"]
    for c in clips:
        args += ["-i", str(c)]

    steps, offset = [], 0.0
    last_v, last_a = "[0:v]", "[0:a]"
    for i in range(1, len(clips)):
        # Offset is where the fade starts in the timeline built so far.
        offset += durations[i - 1] - seconds
        out_v = f"[v{i}]"
        steps.append(
            f"{last_v}[{i}:v]xfade=transition=fade:duration={seconds:g}:"
            f"offset={offset:.3f}{out_v}")
        last_v = out_v
        if has_audio:
            out_a = f"[a{i}]"
            steps.append(f"{last_a}[{i}:a]acrossfade=d={seconds:g}{out_a}")
            last_a = out_a

    args += ["-filter_complex", ";".join(steps), "-map", last_v]
    if has_audio:
        args += ["-map", last_a, "-c:a", "aac", "-b:a", "192k"]
    args += ["-c:v", "libx264", "-preset", "medium", "-crf", "17",
             "-pix_fmt", "yuv420p", str(dest)]

    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise FrameError(f"ffmpeg couldn't crossfade the clips: {err.decode()[-400:]}")
    return dest


async def _has_audio(path: Path, ffprobe: str = "ffprobe") -> bool:
    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "error", "-select_streams", "a", "-show_entries",
        "stream=index", "-of", "csv=p=0", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return bool(out.decode().strip())


async def concat(clips: list[Path], dest: Path, ffmpeg: str = "ffmpeg",
                 crossfade_seconds: float = 0.0, ffprobe: str = "ffprobe") -> Path:
    """Join clips in order. Tries stream copy first, re-encodes if that fails."""
    clips = [Path(c) for c in clips]
    missing = [c for c in clips if not c.exists()]
    if missing:
        raise FrameError(f"Missing clips: {', '.join(c.name for c in missing)}")
    if not clips:
        raise FrameError("Nothing to join.")
    if not shutil.which(ffmpeg):
        raise FrameError("Joining the clips needs ffmpeg on PATH.")

    if crossfade_seconds and len(clips) > 1:
        return await crossfade(clips, dest, crossfade_seconds, ffmpeg, ffprobe)

    dest.parent.mkdir(parents=True, exist_ok=True)
    listing = dest.with_suffix(".txt")
    listing.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))

    attempts = (
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(dest)],
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", str(dest)],
    )
    err = b""
    try:
        for args in attempts:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                return dest
    finally:
        listing.unlink(missing_ok=True)
    raise FrameError(f"ffmpeg couldn't join the clips: {err.decode()[-300:]}")
