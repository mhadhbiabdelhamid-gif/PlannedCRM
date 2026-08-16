# Planned Real Estate — CRM

Internal web app for listings, leads and team collaboration. Python + Flask + SQLite,
no build step, no external services.

---

## Run it on your machine

```bash
cd planned_crm
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open <http://localhost:5000>.

First run creates the admin account and prints it in the terminal:

| | |
|---|---|
| Email | `admin@plannedrealestate.qa` |
| Password | `Planned@2026` |

**Change that password immediately** under My account.

To try the app with realistic Doha data first:

```bash
python seed.py            # adds 8 listings, 8 leads, owners, partners, 3 agents
```

Seeded agents sign in with `yousef@plannedrealestate.qa` / `Agent@2026`.

`python seed.py --reset` clears the demo listings, leads, deals and directory entries and
reloads them. It never touches user accounts, it takes a backup first, and it refuses to
run if it finds records that don't look like demo data. **Once real client data is in the
system, stop using `--reset`.**

## A desktop shortcut

Double-click **`create-desktop-shortcut.bat`**. It puts **Planned CRM** on the desktop using
your uploaded logo as the icon (a gold monogram if you haven't uploaded one yet). Clicking it
opens the CRM, starting the server first if it isn't already running.

Upload the logo under Settings before running this, so the icon is right first time. If you
change the logo later, run it again to refresh the icon.

## Moving the folder somewhere else

The app is self-contained — the database, uploads and settings all live inside the project
folder, and it finds them relative to itself. Moving it does not break the CRM. Three things
are tied to the old path, though, and need redoing:

1. The `.venv` folder has the old location baked into it.
2. The autostart task points at the old path.
3. The desktop shortcut points at the old path.

After moving, run **`move-checklist.bat`**. It stops anything still running, rebuilds `.venv`
in the new location, and reinstalls the dependencies. Then re-run `install-autostart.bat`
(as administrator) and `create-desktop-shortcut.bat` if you were using them.

`start-office.bat` also repairs a stale `.venv` on its own, so if you forget the checklist it
will still start — just more slowly the first time.

**Move the whole folder, including `instance`.** That is where your database, photos and
documents are.

## If it won't start

Double-click **`check-setup.bat`** (Windows) or run **`python diagnose.py`**. It checks
Python, the project files, the required packages, the database, the network address and
the Windows firewall, then prints exactly what to fix. The window stays open so you can
read it, and the output is safe to copy and send to someone for help.

The four causes that account for almost everything:

| What you see | Cause | Fix |
|---|---|---|
| Window flashes and vanishes | Python not installed, or installed without PATH | Reinstall from python.org and tick *Add python.exe to PATH* |
| `'python' is not recognized` | Same as above | Same as above |
| Works on the host, nobody else can connect | Windows firewall blocked it | Allow Python on **Private networks** |
| Browser shows nothing at all | The CRM isn't running, or you typed `https://` | Start it, and use `http://` |

To see an error that disappears too fast: open Command Prompt, type `cd `, drag the project
folder onto the window, press Enter, then run `start-office.bat`. The message stays put.

## If you get locked out

`manage.py` is the rescue tool. Stop the server, then run these from the project folder:

```bash
python manage.py doctor              # what state is everything in?
python manage.py users               # list every account
python manage.py password <email>    # set a new password for an account
python manage.py promote <email>     # make an account an admin and switch it on
python manage.py newadmin <email>    # create a fresh admin from scratch
python manage.py backup              # timestamped copy of the database
python manage.py restore <file>      # put a backup back
```

`doctor` is the place to start — it shows where the database is, how many records are in
each table, whether an active admin exists, and whether anything is actually listening on
port 5000.

Two situations worth naming:

**Pages won't load at all.** The server isn't running. `doctor` will say so under Network.
Start it with `python serve_office.py` (or `start-office.bat`). Note that running
`seed.py` doesn't start the server — if you stopped it to run a script, start it again.

**You can sign in but there's no Settings or Team members menu**, or you can't sign in at
all: there's no active admin account. Run `python manage.py promote your@email` to make
your account an admin, or `newadmin` to create one.

---

## Add the logo

Sign in as an admin, go to **Settings → Branding**, and upload it. That's the whole job.

If you'd rather drop the file in by hand, put it in `static/img/` named `logo` with any
common extension — `logo.png`, `logo.jpg`, `logo.webp` and `logo.svg` all work, and the
capitalisation doesn't matter. Square, 256×256 or larger, transparent background.

Still seeing the old logo or the placeholder? The app adds a version stamp to the image
address so browsers pick up changes straight away, but if you replaced the file while a
page was already open, reload with **Ctrl+Shift+R** (**Cmd+Shift+R** on a Mac).

---

## What's in it

**Dashboard** — total listings, new leads today, pending deals, upcoming viewings, a
pipeline snapshot and the team activity feed.

**Properties** — grid or table view with filters for type, sale/rent, status, location,
price range, bedrooms and agent. Each listing carries a building number and flat number
alongside the address. The building box suggests names already in use, with the area and
unit count beside each, so several buildings in one district stay distinct and nobody
invents a second spelling of the same tower. Listings are grouped by building by default, with the units inside each one in flat order —
and it sorts on the number rather than the text, so 402 comes before 1102 rather than after
it. An **Order by** control switches to newest, oldest, or price. The Excel export uses the
same order. Both fields are searchable — typing a tower name brings up every unit
in it, typing a flat number goes straight to that one. Each listing has a photo gallery with a selectable
cover, full specs, key features, the linked owner with call/WhatsApp/email buttons,
document storage, internal notes and its own change history. **Duplicate** copies a listing
for near-identical units — it asks only for the new flat number and keeps the building — the photos are shared rather than copied, so ten units in one
tower don't store ten sets of the same images, and leads, notes and history stay with the
original. Sale and Rent carry
opposite gold/black tags so they read apart at a glance.

**Follow-ups** — every open lead can carry a next-follow-up date. The dashboard opens with
what is overdue and what is due today, each with a one-tap call button, plus a count of open
leads with no date set at all. On a lead, one panel records a call, WhatsApp, meeting, email
or note and rolls the follow-up forward in the same action — three days is the default. A
lead still marked New moves to Contacted automatically when you log the first call. Won and
Lost leads drop out of every follow-up count.

**Leads** — a drag-and-drop board across New → Contacted → Qualified → Viewing → Offer →
Negotiation → Won → Lost,
plus a searchable table. Each lead file holds contact details, budget, requirements,
the interaction log, scheduled viewings, and an audit trail.

**Deals** — every closed sale or rental with its commission. Type a percentage and the
amount works itself out, or type your own figure and it stays. Deals move through Agreed →
Signed → Collected, the top of the page totals value, commission earned, commission
collected and what's still outstanding, and the dashboard shows the current month's
commission. From a lead file, **Record this deal** carries the property, client, agent and
price straight across.

**Owners and Partners** — separate directories. Partners are filtered by type
(Developer, Legal, Maintenance, Bank, Marketing).

**Agent profiles and performance** — every member has a profile with photo, job title,
department, year joined, employment type, languages, areas covered, a short biography and
their manager. Photos are cropped square and resized on upload, and appear on the team page,
profiles, the top bar and internal notes; anyone without one gets an initials avatar. The
team page shows a card per person with deals, commission, conversion rate, active leads,
live listings and contacts logged, filterable by this month, last month, this quarter, this
year or all time. A profile adds commission collected, leads won and lost, follow-ups due
and overdue, recent deals and live listings. Every figure is derived from records the team
already creates — nothing is entered twice. Agents can edit their own profile and photo and
see their own figures; colleagues' contact details are visible to everyone, but their
performance figures are not.

**Team** — three roles. **Admins** see everything, manage the team, change settings, export, delete records and roll
back imports. **Managers** see every lead, listing and deal, can edit and reassign any of
them, see the whole team's performance and export — but cannot manage accounts, change
settings, delete records or roll back an import. **Agents** see all listings plus their own
leads and anything still unassigned, which they can claim, and see their own performance
figures only.

Long lists are paged at 50–60 rows. Filters and sorting are applied in the database, not to
the page on screen, so sorting by price sorts every match rather than only the visible rows,
and deal totals cover the whole filtered set. Assigning a lead or a listing, changing a status, or leaving a note notifies the
agent in charge.

**Two languages** — English and Arabic, switched from the bottom of the sidebar or the
sign-in screen. Arabic flips the whole layout to right-to-left and swaps to Cairo, since
neither Archivo nor Bodoni carries Arabic glyphs. Phone numbers, prices and reference codes
stay left-to-right inside Arabic text. The choice is remembered per user. Your own data —
property titles, client names, notes — is stored exactly as typed and never translated.

**WhatsApp templates** — four editable messages (listing details, follow-up, viewing
confirmation, owner contact) under Settings. Placeholders like `{name}`, `{property}`,
`{price}` and `{agent}` are filled in before the chat opens, so the message is ready to
send. An unknown placeholder is left blank rather than breaking the message.

**Import from partner lists** — agencies each send a differently shaped availability list,
and typing them in by hand is the slowest job in the office. **Import from Excel** on the
Properties page reads the file, works out which row the headings are on and which column is
which, and shows you what it found before saving anything. It copes with headings buried
under a logo, unit numbers inside codes like `ARPQ02-B00-F01-A101`, rent written as
`9000 qrs`, sizes as `113 sqm`, and bedrooms written as Studio, 1 BHK, Two Bedrooms or
`3 Beds; 5 Baths`. Vacant and Booked become statuses; Google Maps links are picked up even
when hidden behind friendly text. Every sheet in the file is handled separately, so a
six-sheet list imports in one pass. Anything guessed wrong can be corrected on the review
screen. Four modes control what an import does: **preview only** (reads the file and reports what
would change, writing nothing), **add new only**, **update and add** (the usual monthly
choice), and **replace**, which deletes everything previously imported under that partner
name that is no longer in the new file. Replace warns before it runs and names the partner.
Matching is on building plus flat number. Rows with something missing — no price, no bedroom
count, a map link that could not be read, a unit appearing twice — are counted on the review
screen and flagged row by row, but still imported: nothing is discarded silently.

**Import history** records every import: who ran it, the file, the partner name, the mode,
and how many rows were read, added, updated, skipped, removed and unusable. Admins can
**roll back** any import from there — listings it added are deleted, listings it changed go
back to exactly how they were, and listings a replace removed are put back. Rollback only
touches what that import touched, so anything typed in by hand is unaffected. Imported listings are
always marked third-party, never as your own stock.

**Excel export** — an **Export Excel** button on Properties, Leads, Deals and Settings
produces a branded workbook: your company name, address, both phone numbers, email, CR and
P.O. Box across the top of every sheet, a confidential footer with page numbers, frozen
headers, filter dropdowns and live totals. Four sheets — **Our Properties**, **Other
Properties**, **Leads**, **Deals**. Fill in the company fields under Settings first; they
feed the header on every export. CSV exports remain for feeding other systems.

**Map links** — each listing takes a Google Maps link. Paste what Google gives you (the
long link, a `maps.app.goo.gl` short link, or plain coordinates like `25.3702, 51.5487`) and
it becomes an **Open in Maps** button on the property page and a clickable *Open map* cell in
the Excel export. Anything that isn't a Maps link is refused rather than saved.

**Our stock versus everyone else's** — a tick box on each listing marks it as owned by your
company. Filter the listings page by it, and the Excel export puts the two groups on
separate sheets. Existing listings start unticked.

**Everywhere** — global search on address, client name or phone (press `/` to jump to it),
an activity log of every meaningful change with timestamp and user, and CSV export of
leads, properties and owners.

---

## Share it with the office

Run it on one computer in the office — whichever one is usually on. Everyone else opens it
in a browser. Nothing gets installed on their machines.

**On the computer that will host it:**

- Windows: double-click `start-office.bat`
- Mac or Linux: run `./start-office.sh`

Either one sets everything up the first time and then prints two addresses:

```
On this computer:      http://localhost:5000
For everyone else:     http://192.168.1.47:5000
```

Send the team that second address. It works on any phone, tablet or laptop on the office
wifi. Leave the window open — closing it stops the CRM for everyone.

This uses Waitress rather than the development server in `app.py`, which is single-threaded
and will crawl once several people are using it at once.

**Four things worth doing:**

1. **Let the app through the firewall.** On Windows you'll get a prompt the first time —
   tick *Private networks* and allow it. Without this nobody else can connect.
2. **Reserve the IP address.** That `192.168.1.47` can change when the computer restarts,
   and then the team's bookmarks break. In your router's admin page, find DHCP reservation
   (sometimes called static lease) and pin that address to this computer.
3. **Don't let the computer sleep.** Windows: Settings → System → Power → Screen and
   sleep → set sleep to Never while plugged in. Otherwise the CRM disappears at lunchtime.
4. **Back up.** Everything lives in the `instance` folder — copy it somewhere safe on a
   schedule. See Backups below.

**Keeping it running (do this once).** Right-click `install-autostart.bat` and choose
**Run as administrator**. From then on the CRM starts a minute after the computer boots,
runs with no window to close by accident, and restarts itself within five seconds if it
ever stops. That removes the single most common failure: someone closes the black window,
or the machine reboots overnight, and the whole office finds the CRM gone in the morning.

`stop-crm.bat` stops it. Everything it prints is written to `instance/server.log`, so if it
does crash you can see why — `python diagnose.py` reads that log and interprets the last
error for you.

If `ERR_CONNECTION_REFUSED` appears on `localhost:5000`, that always means one thing: the
CRM is not running on that machine. It is never a firewall or network issue, because
localhost never leaves the computer.

**Working from outside the office** — home, site visits, phones on mobile data — is what
the hosted option below is for. A local install only reaches as far as the office wifi.

## Deploy it

### Render

1. Push this folder to a GitHub repository.
2. In Render: **New → Blueprint**, point it at the repo. `render.yaml` sets up the
   service and a 5 GB persistent disk at `/var/data`.
3. Set `ADMIN_EMAIL` and `ADMIN_PASSWORD` in the dashboard before the first deploy.

**The persistent disk matters.** Render's free tier wipes the filesystem on every deploy
and every sleep — the database and all uploaded photos would vanish. The disk in
`render.yaml` needs a paid instance type (around $7/month at the time of writing).

### Railway

1. New project from the repo. Railway reads the `Procfile`.
2. Attach a Volume mounted at `/var/data`.
3. Set the environment variables below.

### Environment variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Signs session cookies. Long and random. Required in production. |
| `DATABASE_PATH` | e.g. `/var/data/crm.sqlite3` — must sit on the persistent disk |
| `UPLOAD_FOLDER` | e.g. `/var/data/uploads` — same |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | First admin account, created on first boot only |
| `HTTPS_ONLY` | Set to `1` so session cookies are HTTPS-only |
| `TZ_OFFSET_HOURS` | Display offset from UTC. Defaults to `3` for Doha. |

`.env.example` has the full list.

---

## Backups

**Automatic.** While the CRM is running it takes a copy of the database once a day and
keeps the last 14, in `instance/backups/`. Nothing to set up.

**From the browser.** Sign in as an admin and open Settings. The Backups panel shows when
the last copy was taken and lists recent ones. Two download buttons:

- **Download everything** — database, photos and documents in one zip, with restore
  instructions inside. This is the one to keep.
- **Database only** — much smaller, fine as a quick daily copy.

There's also a **Back up now** button for before you do anything risky.

**Get copies off the computer.** A backup on the same hard drive as the original doesn't
protect you from that drive failing or the machine being stolen. Fix it from Settings:
under Backups there's a **Where backups are saved** box. Paste the full path of a OneDrive
or Google Drive folder and press **Save folder and test it**. Every backup then syncs off
the machine on its own.

The path must be the complete one starting with a drive letter, like
`C:\Users\YourName\OneDrive\CRM-Backups`. Get it by opening the folder in File Explorer,
clicking once in the address bar so the text highlights, and copying. A partial path is
rejected with an explanation rather than silently creating a folder in the wrong place.
Pressing save writes a test backup immediately, so you know straight away that it worked.

`BACKUP_FOLDER` still works as an environment variable if you prefer, but the Settings box
takes precedence and is easier.

**Restoring.** Stop the CRM, then:

```bash
python manage.py restore instance\backups\crm-2026-08-13_0300-auto.sqlite3
```

Your current database is set aside as `.before-restore` first, so a mistaken restore is
itself reversible. If you're restoring from a downloaded zip, unpack it and copy
`crm.sqlite3` and the `uploads` folder into `instance`.

**Settings that change this behaviour:** `BACKUP_FOLDER` (where copies go),
`BACKUP_KEEP` (how many to keep, default 14), `BACKUP_INTERVAL_HOURS` (default 24),
`BACKUP_DISABLED=1` (turn the automatic copies off).

---

## Notes and limits

- SQLite handles an office of this size comfortably. If you ever grow past roughly
  20 people writing at once, that's the point to move to PostgreSQL — the data layer in
  `db.py` is the only file that would change.
- Uploads are capped at 25 MB per file.
- Timestamps are stored in UTC and displayed in Doha time (UTC+3). Times you type into
  the app are treated as local and converted on the way in, so what you enter is what you
  see. Qatar has no daylight saving, so a fixed offset is correct year-round. Set
  `TZ_OFFSET_HOURS` if the office ever moves.
- Prices, budgets, sizes and commission accept any figure — 5,500 or 14,750.75 are both
  fine. Decimals are only shown when they carry meaning, so 5500 displays as `5,500`.
- Passwords are hashed with Werkzeug's PBKDF2. Nothing is stored in plain text.
- Deleting a listing removes its photos, documents and notes with it. Only admins can
  delete anything.

## File map

```
app.py                 application factory, config, template filters
db.py                  schema, queries, activity log, notifications
auth.py                sign-in, roles, permission checks
views_main.py          dashboard, search, notifications, activity
views_properties.py    listings, images, documents
views_leads.py         pipeline board, lead files, viewings
views_deals.py         deals and commission
views_admin.py         owners, partners, team, settings, CSV export
i18n.py                English/Arabic strings
whatsapp.py            message templates and link building
excel_export.py        the branded Excel workbook
maps.py                validates pasted Google Maps links
importer.py            reads partner spreadsheets of any shape
views_imports.py       the upload, review and import screens
seed.py                demo data
manage.py              rescue tool: doctor, passwords, admins, backups
diagnose.py            setup checker for when it will not start
make_shortcut.py       builds the desktop shortcut and its icon
backups.py             automatic and downloadable backups
serve_office.py        runs it on the office network
templates/             all pages
static/css/app.css     the whole theme
static/js/app.js       drag-and-drop, modals, shortcuts
```
