"""Make the reel and video-story MP4s (1080x1920, H.264 + AAC).

The browser draws two pictures with static/js/postcards.js and sends them
here: a transparent frame (logo, series label and a details panel) laid over
the moving part, and the story design used as the closing card. This module
only puts pictures together; nothing about the design lives here.

    render(out_path, overlay_png, end_jpg, items=[("photo", path),
                                                   ("video", path), ...])

Any mix of photos and listing videos, in the order given. Photos pan slowly
across (wide photos side to side, tall ones up and down); a video is fitted
on a blurred copy of itself; every piece dips to the brand cream between
them. The closing card follows for a few seconds.

The length follows what was picked (see plan()): about 3 seconds a photo plus
each video's own length, never under MIN_TOTAL and never over MAX_TOTAL
(videos are shortened first when there is too much).

Each piece is made by its own ffmpeg run and the pieces are then joined
without re-encoding, which keeps memory low (see render()).

ffmpeg comes from the imageio-ffmpeg package (a ready-made copy for Windows,
Mac and Linux, installed by pip), or from the system if that isn't there.
"""
import os
import re
import shutil
import subprocess
import tempfile

# 1080x1920 is what Instagram and Facebook recommend. Encoding at that size
# takes about 250 MB of memory while it runs; on a server tighter than that,
# set REEL_SIZE=720x1280 (still accepted by both).
SIZE_W, SIZE_H = (int(v) for v in os.environ.get("REEL_SIZE", "1080x1920").split("x"))
FPS = 30
PHOTO_SECONDS = 3.2        # each photo on screen, normally
MIN_PHOTO_SECONDS = 2.0    # when there are many photos and videos
MAX_PHOTO_SECONDS = 6.5    # when one or two photos stretch to MIN_TOTAL
MIN_CLIP_SECONDS = 3.0     # a video is never shortened below this
FADE = 0.35                # each piece dips to the brand cream and back
CREAM = "0xFAF8F4"
END_SECONDS = 3.5          # the closing card
MIN_TOTAL = 10.0           # the shortest reel or story we make
MAX_TOTAL = 59.0           # stories allow 60 s
MAX_ITEMS = 20             # photos + videos in one reel
MAX_PHOTOS = MAX_ITEMS     # kept for older callers
PAN_MAX = 640              # px a photo travels; more feels hurried


def plan(kinds, video_lengths=()):
    """Seconds for each piece and for the closing card.

    kinds: "photo"/"video" per piece, in order; video_lengths: the real
    length of each video, in the same order as the videos appear.
    Returns ([seconds per piece], end_seconds)."""
    n = kinds.count("photo")
    vids = [max(float(v), 1.0) for v in video_lengths]
    budget = MAX_TOTAL - END_SECONDS
    pd = PHOTO_SECONDS
    if n * pd + sum(vids) > budget:
        # Too long: shorten the videos first (each keeps MIN_CLIP_SECONDS),
        # then the photos.
        floor = sum(min(v, MIN_CLIP_SECONDS) for v in vids)
        if n * pd + floor > budget and n:
            pd = max(MIN_PHOTO_SECONDS, (budget - floor) / n)
        room = budget - n * pd
        long_part = sum(v - min(v, MIN_CLIP_SECONDS) for v in vids)
        spare = max(room - floor, 0)
        scale = min(1.0, spare / long_part) if long_part else 1.0
        vids = [min(v, MIN_CLIP_SECONDS) + (v - min(v, MIN_CLIP_SECONDS)) * scale
                for v in vids]
    end = END_SECONDS
    short = MIN_TOTAL - (n * pd + sum(vids) + end)
    if short > 0:
        if n:                                   # photos stay a bit longer
            pd = min(MAX_PHOTO_SECONDS, pd + short / n)
            short = MIN_TOTAL - (n * pd + sum(vids) + end)
        if short > 0:                           # the closing card holds on
            end += short
    out, vi = [], iter(vids)
    for k in kinds:
        out.append(round(pd if k == "photo" else next(vi), 2))
    return out, round(end, 2)


# Background music. Built-in tracks live in static/audio; tracks the team adds
# go to the uploads folder (music/). The music is cut to the video's length,
# fades in and out, and replaces the videos' own sound unless keep_sound.
MUSIC_FADE_IN = 1.0
MUSIC_FADE_OUT = 2.5
MUSIC_LEVEL = 0.9          # music alone
MUSIC_UNDER = 0.35         # music under the videos' own sound
BUILTIN_MUSIC = {"open-house": "Open House (corporate, upbeat)"}
MUSIC_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg"}


def music_tracks(static_folder, upload_folder):
    """[{id, name, url}] - the built-in tracks, then the team's own."""
    out = [{"id": k, "name": v, "url": f"/static/audio/{k}.mp3"}
           for k, v in BUILTIN_MUSIC.items()
           if os.path.isfile(os.path.join(static_folder, "audio", k + ".mp3"))]
    folder = os.path.join(upload_folder, "music")
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if os.path.splitext(name)[1].lower() in MUSIC_EXT:
                label = os.path.splitext(name)[0].split("__", 1)[-1].replace("_", " ")
                out.append({"id": "u:" + name, "name": label,
                            "url": "/uploads/music/" + name})
    return out


def music_path(track_id, static_folder, upload_folder):
    """The file for a track id, or None (no music / unknown)."""
    if not track_id:
        return None
    if track_id.startswith("u:"):
        name = os.path.basename(track_id[2:])
        path = os.path.join(upload_folder, "music", name)
    elif track_id in BUILTIN_MUSIC:
        path = os.path.join(static_folder, "audio", track_id + ".mp3")
    else:
        return None
    return path if os.path.isfile(path) else None


def add_music(video_in, out_path, music, seconds, keep_sound=False):
    """Lay `music` under the finished video: cut to `seconds`, faded in and
    out. The picture is copied as it is, so this takes a few seconds."""
    out_fade = max(seconds - MUSIC_FADE_OUT, 0)
    m = (f"[1:a]atrim=0:{seconds:.2f},asetpts=PTS-STARTPTS,"
         f"aresample=44100,aformat=channel_layouts=stereo,"
         f"afade=t=in:st=0:d={MUSIC_FADE_IN},"
         f"afade=t=out:st={out_fade:.2f}:d={MUSIC_FADE_OUT},"
         f"volume={MUSIC_UNDER if keep_sound else MUSIC_LEVEL}")
    if keep_sound:
        fc = (m + "[m];[0:a][m]amix=inputs=2:duration=first:normalize=0,"
              "alimiter=limit=0.95[a]")
    else:
        fc = m + f",apad=whole_dur={seconds:.2f}[a]"
    _run([ffmpeg_exe(), "-i", video_in, "-stream_loop", "-1", "-i", music,
          "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
          "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
          "-t", f"{seconds:.2f}", "-movflags", "+faststart", out_path],
         seconds, lambda d: None)


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if found:
            return found
    raise RuntimeError("The video maker (ffmpeg) isn't installed on this "
                       "server. Run: pip install imageio-ffmpeg")


def probe_audio(path):
    """Check a music file can be read; returns its length in seconds."""
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path],
                          capture_output=True, text=True, errors="replace")
    if not re.search(r"Stream #.*Audio:", proc.stderr):
        raise RuntimeError("That file isn't music the video maker can read.")
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", proc.stderr)
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0.0


def available():
    try:
        ffmpeg_exe()
        return True
    except RuntimeError:
        return False


def probe(path):
    """(duration seconds, has_audio) read from ffmpeg's own report."""
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path],
                          capture_output=True, text=True, errors="replace")
    info = proc.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", info)
    duration = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                + float(m.group(3))) if m else 0.0
    if not re.search(r"Stream #.*Video:", info):
        raise RuntimeError("That file isn't a video ffmpeg can read.")
    return duration, bool(re.search(r"Stream #.*Audio:", info))


def _prepare_photo(src, dst, index, d=PHOTO_SECONDS):
    """Upright, sized so one side fits the frame exactly and the other is
    longer (that's the room to pan). Returns the crop expressions."""
    from PIL import Image, ImageOps
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        w, h = im.size
        if w / h >= SIZE_W / SIZE_H:                   # wide: pan sideways
            nh = SIZE_H
            nw = max(SIZE_W, round(w * nh / h))
        else:                                          # tall: pan up/down
            nw = round(SIZE_W * 1.12)
            nh = max(round(SIZE_H * 1.12), round(h * nw / w))
        im = im.resize((nw, nh), Image.LANCZOS)
        im.save(dst, "JPEG", quality=92)
    def path(span):
        travel = min(span, PAN_MAX)
        a = (span - travel) / 2
        b = a + travel
        if index % 2:                                  # alternate direction
            a, b = b, a
        return f"{a:.1f}+({b - a:.1f})*min(t/{d},1)"
    if nw > SIZE_W and nh == SIZE_H:
        return path(nw - SIZE_W), "0"
    return f"{(nw - SIZE_W) / 2:.1f}", path(nh - SIZE_H)


ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
          "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
          "-r", str(FPS), "-g", str(FPS * 2), "-video_track_timescale", "15360",
          "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
          "-threads", "2", "-filter_complex_threads", "1"]
SILENCE = ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]


def _still(seconds):
    """Filter that turns one picture into `seconds` of video."""
    return (f"loop=loop={round(seconds * FPS) - 1}:size=1:start=0,"
            f"setpts=N/{FPS}/TB")


def _run(cmd, seconds, on_seconds):
    """Run one ffmpeg step, reporting how many seconds of it are done."""
    proc = subprocess.Popen(cmd[:1] + ["-hide_banner", "-y", "-nostats",
                                       "-progress", "pipe:1"] + cmd[1:],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, errors="replace")
    err_lines = []
    import threading
    t = threading.Thread(target=lambda: err_lines.extend(proc.stderr),
                         daemon=True)
    t.start()
    for line in proc.stdout:
        if line.startswith("out_time_us="):
            try:
                on_seconds(min(int(line.split("=", 1)[1]) / 1e6, seconds))
            except ValueError:
                pass
    proc.wait()
    t.join(timeout=5)
    if proc.returncode != 0:
        tail = "".join(err_lines[-8:]).strip()[-700:]
        raise RuntimeError("The video couldn't be made. " + tail)


def render(out_path, overlay_png, end_jpg, photos=None, video=None,
           progress=None, items=None, music=None, keep_sound=False):
    """Build the MP4. progress(fraction 0..1) is called as it encodes.
    Returns the length in seconds.

    items is the list of ("photo" | "video", path) pieces in order; the
    older photos=[...] / video=path forms still work. music is an audio file
    to lay under the whole video (see add_music).

    Made one piece at a time (each photo, each video, then the closing
    card), each by its own ffmpeg run, then joined without re-encoding. A
    single ffmpeg run with every piece in it held all the upcoming pictures
    in memory, over a gigabyte; the live server has 512 MB."""
    if items is None:
        items = [("video", video)] if video else \
            [("photo", p) for p in (photos or [])]
    items = [(k, p) for k, p in items if k in ("photo", "video") and p][:MAX_ITEMS]
    if not items:
        raise RuntimeError("Pick a video or at least one photo.")
    exe = ffmpeg_exe()
    work = tempfile.mkdtemp(prefix="reel-")
    try:
        probed = {}
        for k, path in items:
            if k == "video" and path not in probed:
                dur, has_audio = probe(path)
                if dur < 1:
                    raise RuntimeError("One of the videos is too short "
                                       "(under a second).")
                probed[path] = (dur, has_audio)
        lengths, end_seconds = plan([k for k, _ in items],
                                    [probed[p][0] for k, p in items if k == "video"])
        steps = []                  # (ffmpeg arguments, seconds)
        ovl = (f"[1:v]scale={SIZE_W}:{SIZE_H},format=rgba[ovl];[m][ovl]overlay=0:0:eof_action=repeat,"
               f"format=yuv420p")
        for i, ((kind, src), L) in enumerate(zip(items, lengths)):
            fade_in = "" if i == 0 else f",fade=t=in:st=0:d={FADE}:color={CREAM}"
            fade_out = f",fade=t=out:st={L - FADE:.2f}:d={FADE}:color={CREAM}"
            if kind == "video":
                has_audio = probed[src][1]
                # A small blurred copy fills the frame behind the video, so a
                # landscape clip isn't cropped to a sliver.
                vf = (f"[0:v]fps={FPS},split[va][vb];"
                      f"[va]scale=270:480:force_original_aspect_ratio=increase,"
                      f"crop=270:480,boxblur=8:2,scale={SIZE_W}:{SIZE_H},setsar=1[bg];"
                      f"[vb]scale={SIZE_W}:{SIZE_H}:force_original_aspect_ratio=decrease,"
                      f"setsar=1[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[m];"
                      f"{ovl}{fade_in}{fade_out}[v]")
                if has_audio:
                    vf += (f";[0:a]aresample=44100,aformat=channel_layouts=stereo,"
                           f"afade=t=in:st=0:d={FADE},"
                           f"afade=t=out:st={max(L - 1.2, 0):.2f}:d=1.2,"
                           f"apad=whole_dur={L:.2f}[a]")
                    amap, extra = "[a]", []
                else:
                    amap, extra = "2:a", SILENCE
                steps.append(([exe, "-t", f"{L:.2f}", "-i", src, "-i", overlay_png,
                               *extra, "-filter_complex", vf, "-map", "[v]",
                               "-map", amap, "-t", f"{L:.2f}"], L))
            else:
                dst = os.path.join(work, f"p{i}.jpg")
                x, y = _prepare_photo(src, dst, i, L)
                # loop= repeats the one decoded picture (a looped input would
                # decode it again for every frame and queue them up).
                vf = (f"[0:v]{_still(L)},"
                      f"crop={SIZE_W}:{SIZE_H}:x='{x}':y='{y}',setsar=1[m];"
                      f"{ovl}{fade_in}{fade_out}[v]")
                steps.append(([exe, "-i", dst,
                               "-i", overlay_png, *SILENCE,
                               "-filter_complex", vf, "-map", "[v]",
                               "-map", "2:a", "-t", f"{L:.2f}"], L))
        steps.append(([exe, "-i", end_jpg, *SILENCE,
                       "-filter_complex",
                       f"[0:v]{_still(end_seconds)},scale={SIZE_W}:{SIZE_H},"
                       f"setsar=1,format=yuv420p,"
                       f"fade=t=in:st=0:d={FADE}:color={CREAM}[v]",
                       "-map", "[v]", "-map", "1:a", "-t", f"{end_seconds}"],
                      end_seconds))

        total = sum(sec for _, sec in steps)
        done_before = 0.0
        parts = []
        for n, (args, sec) in enumerate(steps):
            part = os.path.join(work, f"part{n}.mp4")
            base = done_before
            _run(args + ENCODE + [part], sec,
                 lambda d, base=base: progress and progress(
                     min((base + d) / total, 0.98)))
            parts.append(part)
            done_before += sec
        listing = os.path.join(work, "parts.txt")
        with open(listing, "w") as f:
            for part in parts:
                # forward slashes: the list treats \ as an escape (Windows)
                f.write("file '" + part.replace("\\", "/") + "'\n")
        tmp_out = out_path + ".part.mp4"
        joined = os.path.join(work, "joined.mp4") if music else tmp_out
        _run([exe, "-f", "concat", "-safe", "0", "-i", listing, "-c", "copy",
              "-movflags", "+faststart", joined], total, lambda d: None)
        if music:
            add_music(joined, tmp_out, music, total, keep_sound)
        os.replace(tmp_out, out_path)
        if progress:
            progress(1.0)
        return total
    finally:
        shutil.rmtree(work, ignore_errors=True)


def poster(video_path, out_jpg, at=1.0):
    """One still from the video, for thumbnails."""
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-y", "-ss", f"{at}",
                    "-i", video_path, "-frames:v", "1", "-q:v", "4", out_jpg],
                   capture_output=True)
    return os.path.exists(out_jpg)
