# Social Content Agent

Generates Instagram/Facebook posts from your listings: English and Arabic
captions, Qatar hashtags, and branded 1080x1080 image cards drawn in the
browser (no Pillow, no imaging packages).

## Install

1. Copy `ai_social.py` next to `app.py`, and `social.html` + `social_review.html`
   into `templates/`.

2. Create the table:

```
python -c "import sqlite3;c=sqlite3.connect('instance/crm.sqlite3');c.execute('CREATE TABLE IF NOT EXISTS social_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, property_ids TEXT NOT NULL, kind TEXT NOT NULL, content_json TEXT NOT NULL, status TEXT NOT NULL, post_urls TEXT, error TEXT, created_by INTEGER, created_at TEXT NOT NULL, posted_by INTEGER, posted_at TEXT)');c.commit();print('done')"
```

3. Register in `app.py`, next to the intake line:

```python
from ai_social import social_bp
app.register_blueprint(social_bp)
```

4. Restart, then open http://localhost:5000/social

It works immediately in generate-only mode: pick listings, edit the captions,
download the cards, post from your phone.

## Turning on direct posting

Requires all four of these in `instance/ai_keys.env`:

```
SOCIAL_PUBLIC_BASE_URL=https://your-app.onrender.com
FB_PAGE_ID=...
FB_PAGE_TOKEN=...
IG_USER_ID=...
```

What you need to obtain, in order:

1. A Facebook Page for Planned Real Estate (not a personal profile).
2. An Instagram **Business** account, linked to that Page in Instagram settings.
   A personal or Creator account cannot be posted to via the API.
3. A Meta app at developers.facebook.com, with the Instagram Graph API and
   Facebook Login products added.
4. Permissions: `pages_manage_posts`, `pages_read_engagement`,
   `instagram_basic`, `instagram_content_publish`.
5. A long-lived Page access token. Short-lived tokens expire in about an hour;
   the long-lived version lasts around 60 days and must be refreshed.
6. Business verification, if you want to post outside development mode.

`IG_USER_ID` is the Instagram Business account ID, not the handle. Get it from
the Page: `GET /{page-id}?fields=instagram_business_account`.

## Why a public URL is required

Instagram's publishing API does not accept file uploads. You give it a URL and
Meta's servers fetch the image. `localhost` is unreachable from the internet, so
the card images have to be served from somewhere public — which is what your
Render deployment can do.

Until that is in place, generate-only mode is the working path.

## Tuning the writing

The voice lives in `SYSTEM_PROMPT` in `ai_social.py`. It currently bans generic
openers, requires the price and area in the first two lines, and forbids
inventing features not present in the listing data. When a post comes out wrong,
add a rule there rather than editing captions by hand every time.

## Stories, reels and the publishing schedule

On a post's page, **Step 2 · The design** has three tabs:

* **Post** — the feed picture(s), 1080×1350. Listing posts come in four
  layouts with the same identity: **Classic**, **Split** (photo on top,
  details centred below), **Panel** (full photo, details on a floating cream
  card) and **Mosaic** (photo grid, details below). "Auto" (the default)
  turns through them by post number, so consecutive posts never look alike;
  the choice can be fixed per post on the Post tab. Step 1 takes up to 10 photos per
  listing: 1 is the main photo of the design, 2 and 3 the small ones. With
  more than one photo the post is a **carousel** (switch on the Post tab):
  the design, then every photo after the main one as its own slide (photo,
  logo tab, contact footer), with dots, a "2/6" counter and a swipe arrow;
  up to 10 pictures in the post.
* **Story** — the same design in 1080×1920, one story per picture (with the
  carousel on, each photo is its own story too). Everything
  that matters sits between y=250 and y=1670, clear of Instagram's bars.
* **Video** — a 1080×1920 MP4 for a **reel** or a **video story**: any mix of
  listing videos and photos (up to 20), clicked in the order they play, with
  the Planned frame on top and a closing card with the price and contact.
  Several videos can be uploaded at once. The length follows what was picked
  (`social_video.plan()`): about 3.2 s a photo plus each video's length, never
  under 10 s (one or two photos stay longer) and never over 59 s (videos are
  shortened first, then photos). **Music**: a built-in track
  (`static/audio/open-house.mp3`, original, royalty-free) or one the team
  uploads (kept in `uploads/music/`), cut to the video's length with a 1 s
  fade-in and 2.5 s fade-out; it replaces the videos' own sound unless "keep
  the videos' own sound" is ticked. Picture posts and picture stories can't
  carry music through Meta's API, so music goes on reels and video stories.
  Every post has the Video tab: **the design itself** is a piece too (the
  9:16 story picture, shown whole and slowly zooming in), so a tip or a
  market post becomes a reel; those start with the design and without the
  closing card. **Length**: Auto (as above) or **Exactly** 5-90 s: photos
  and designs share the time the videos leave, a video that's too short
  holds on its last frame, and validating a video story over 60 s is
  refused. Music can **start N seconds into the track**. Tracks are added by
  upload, **from a link** (e.g. an Artlist download link; the server fetches
  it, public https addresses only, 60 MB max) or from the **free music
  library** (`social_music.py`): Openverse search (Jamendo, Freesound,
  ccMixter), music only, licences CC0 / Public Domain / CC BY (BY-SA is left
  out: it would bind the reel to the same licence). CC BY needs a credit: it
  is saved next to the track (`<file>.json`) and added to the end of the
  reel's caption on validation. Tracks the team added can be removed. Made on
  the server by `social_video.py` with ffmpeg from the `imageio-ffmpeg`
  package (no separate install). One video at a time; it uses ~250 MB while
  it runs. On a smaller server set `REEL_SIZE=720x1280`.

**Validate & schedule** stores that format exactly as designed, with the
caption as it is at that moment, on the schedule. Optionally give it a time
right there. `/social/schedule` lists everything validated: set times one by
one, or tick several and **Schedule one a day** from a start date at a time
you pick (days that already have the same kind are skipped if you want). The
next two weeks are shown so empty days stand out.

Publishing on time is done by a background thread (`social_queue.py`), on
the live server only: it runs when `RENDER` is set (Render sets it) or when
`SOCIAL_SCHEDULER=1`; `SOCIAL_SCHEDULER=0` turns it off. An office copy
restored from a backup therefore never posts the same schedule twice.
Every item is claimed with one conditional update, so with two workers only
one publishes it. If publishing is interrupted it is marked failed (never
retried by itself, it may have gone out); if the server was off for more
than 6 hours past an item's time, it goes back to "ready" for a new time.
Failures send a notification to whoever validated the item.

Meta endpoints used: Instagram `media` with `media_type=STORIES` / `REELS`;
Facebook `photo_stories`, `video_stories` and `video_reels` (video uploaded
by link through `rupload.facebook.com`). Same permissions as feed posts.

## TikTok

See the top of `social_tiktok.py` for the full setup. In short: create an app
on developers.tiktok.com with Login Kit + Content Posting API (Direct Post),
scopes `user.info.basic, video.publish, video.upload`, add the redirect URI
shown in Social settings, paste the Client key / secret there and click
**Connect TikTok**. Reels go to TikTok as videos (uploaded directly), feed
posts as photo posts (TikTok downloads them, so the URL prefix shown in
settings must be verified in the TikTok app). No stories on TikTok. Until
TikTok audits the app, posts can only be "Only me".
