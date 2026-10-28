"""Make the reel and video-story MP4s (1080x1920, H.264 + AAC).

The browser draws two pictures with static/js/postcards.js and sends them
here: a transparent frame (logo, series label and a details panel) laid over
the moving part, and the story design used as the closing card. This module
only puts pictures together; nothing about the design lives here.

    render(out_path, overlay_png, end_jpg, photos=[...])   # photo slideshow
    render(out_path, overlay_png, end_jpg, video=path)     # a listing video

Photos pan slowly across (wide photos side to side, tall ones up and down),
dipping to the brand cream between them; a listing video is fitted on a
blurred copy of itself. Either way the closing card follows for a few seconds.

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
PHOTO_SECONDS = 3.2        # each photo on screen
FADE = 0.35                # each photo dips to the brand cream and back
CREAM = "0xFAF8F4"
END_SECONDS = 3.5          # the closing card
MAX_PHOTOS = 10
MAX_VIDEO_SECONDS = 55     # stories allow 60 s; the end card adds about 3 s
PAN_MAX = 640              # px a photo travels; more feels hurried


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


def _prepare_photo(src, dst, index):
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
    d = PHOTO_SECONDS
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
           progress=None):
    """Build the MP4. progress(fraction 0..1) is called as it encodes.
    Returns the length in seconds.

    Made one piece at a time (each photo, or the video, then the closing
    card), each by its own ffmpeg run, then joined without re-encoding. A
    single ffmpeg run with every piece in it held all the upcoming pictures
    in memory, over a gigabyte; the live server has 512 MB."""
    if not photos and not video:
        raise RuntimeError("Pick a video or at least one photo.")
    exe = ffmpeg_exe()
    work = tempfile.mkdtemp(prefix="reel-")
    try:
        steps = []                  # (ffmpeg arguments, seconds)
        ovl = (f"[1:v]scale={SIZE_W}:{SIZE_H},format=rgba[ovl];[m][ovl]overlay=0:0:eof_action=repeat,"
               f"format=yuv420p")
        if video:
            dur, has_audio = probe(video)
            if dur < 1:
                raise RuntimeError("That video is too short.")
            L = min(dur, MAX_VIDEO_SECONDS)
            # A small blurred copy fills the frame behind the video, so a
            # landscape clip isn't cropped to a sliver.
            vf = (f"[0:v]fps={FPS},split[va][vb];"
                  f"[va]scale=270:480:force_original_aspect_ratio=increase,"
                  f"crop=270:480,boxblur=8:2,scale={SIZE_W}:{SIZE_H},setsar=1[bg];"
                  f"[vb]scale={SIZE_W}:{SIZE_H}:force_original_aspect_ratio=decrease,"
                  f"setsar=1[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[m];"
                  f"{ovl},fade=t=out:st={L - FADE:.2f}:d={FADE}:color={CREAM}[v]")
            if has_audio:
                vf += (f";[0:a]aresample=44100,aformat=channel_layouts=stereo,"
                       f"afade=t=out:st={max(L - 1.2, 0):.2f}:d=1.2,"
                       f"apad=whole_dur={L:.2f}[a]")
                amap = "[a]"
                extra = []
            else:
                amap, extra = "2:a", SILENCE
            steps.append(([exe, "-t", f"{L:.2f}", "-i", video, "-i", overlay_png,
                           *extra, "-filter_complex", vf, "-map", "[v]",
                           "-map", amap, "-t", f"{L:.2f}"], L))
        else:
            photos = photos[:MAX_PHOTOS]
            for i, src in enumerate(photos):
                dst = os.path.join(work, f"p{i}.jpg")
                x, y = _prepare_photo(src, dst, i)
                fades = "" if i == 0 else f",fade=t=in:st=0:d={FADE}:color={CREAM}"
                fades += (f",fade=t=out:st={PHOTO_SECONDS - FADE}:d={FADE}"
                          f":color={CREAM}")
                # loop= repeats the one decoded picture (a looped input would
                # decode it again for every frame and queue them up).
                vf = (f"[0:v]{_still(PHOTO_SECONDS)},"
                      f"crop={SIZE_W}:{SIZE_H}:x='{x}':y='{y}',setsar=1[m];"
                      f"{ovl}{fades}[v]")
                steps.append(([exe, "-i", dst,
                               "-i", overlay_png, *SILENCE,
                               "-filter_complex", vf, "-map", "[v]",
                               "-map", "2:a", "-t", f"{PHOTO_SECONDS}"],
                              PHOTO_SECONDS))
        steps.append(([exe, "-i", end_jpg, *SILENCE,
                       "-filter_complex",
                       f"[0:v]{_still(END_SECONDS)},scale={SIZE_W}:{SIZE_H},"
                       f"setsar=1,format=yuv420p,"
                       f"fade=t=in:st=0:d={FADE}:color={CREAM}[v]",
                       "-map", "[v]", "-map", "1:a", "-t", f"{END_SECONDS}"],
                      END_SECONDS))

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
        _run([exe, "-f", "concat", "-safe", "0", "-i", listing, "-c", "copy",
              "-movflags", "+faststart", tmp_out], total, lambda d: None)
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
