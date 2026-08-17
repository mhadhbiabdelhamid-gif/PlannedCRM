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
