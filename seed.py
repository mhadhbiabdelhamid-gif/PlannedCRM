"""Load a set of realistic demo records so the team can try the CRM before going live.

    python seed.py            add demo data alongside whatever is already there
    python seed.py --reset    clear the demo data and reload it

--reset NEVER touches user accounts, and it refuses to run if it finds records
that don't look like demo data. It takes a backup first either way. If you really
do want to wipe real records, add --force, and read the warning it prints.

Delete instance/crm.sqlite3 to start completely fresh.
"""
import os
import random
import shutil
import sys
from datetime import datetime, timedelta

from app import create_app
from auth import create_user
from db import execute, log, next_ref, notify, now, query, set_setting

AREAS = ["The Pearl", "Lusail", "West Bay", "Al Sadd", "Msheireb",
         "Al Waab", "Old Airport", "Al Wakrah"]

PROPERTIES = [
    ("Sea-view apartment, Porto Arabia", "Tower 12, Porto Arabia", "The Pearl",
     "Apartment", "Rent", "Available", 14000, 165, 3, 3,
     "Sea view, Maid's room, Covered parking, Gym access, Balcony"),
    ("Standalone villa with private pool", "Street 940, Al Waab", "Al Waab",
     "Villa", "Sale", "Available", 6800000, 620, 6, 7,
     "Private pool, Driver's room, Landscaped garden, Majlis, Two kitchens"),
    ("Furnished studio, Marina District", "Marina 21, Lusail", "Lusail",
     "Apartment", "Rent", "Rented", 5500, 58, 1, 1,
     "Fully furnished, Marina view, Bills included, Pool"),
    ("Grade-A office floor, West Bay", "Al Fardan Tower, West Bay", "West Bay",
     "Office", "Rent", "Available", 42000, 480, 0, 2,
     "Fitted out, Raised floor, Parking bays, 24h access"),
    ("Retail unit, Msheireb Downtown", "Barahat Msheireb", "Msheireb",
     "Commercial", "Rent", "Reserved", 28000, 210, 0, 1,
     "Corner unit, High footfall, Shell and core"),
    ("Family compound villa", "Compound 7, Al Sadd", "Al Sadd",
     "Villa", "Rent", "Available", 16500, 340, 4, 4,
     "Shared pool, Gym, Maid's room, Compound security"),
    ("Two-bedroom, Viva Bahriya", "Tower 8, Viva Bahriya", "The Pearl",
     "Apartment", "Sale", "Sold", 2350000, 128, 2, 3,
     "Sea view, Beach access, Covered parking"),
    ("Residential land plot", "Al Wakrah North", "Al Wakrah",
     "Land", "Sale", "Available", 3100000, 900, 0, 0,
     "Corner plot, Ready utilities, G+1+P permitted"),
]

OWNERS = [
    ("Mohammed Al-Kuwari", "+974 5512 4478", "m.alkuwari@example.qa", "Al-Kuwari Holdings"),
    ("Fatima Al-Mansouri", "+974 3390 1122", "fatima.m@example.qa", ""),
    ("Qatar Gulf Investments", "+974 4441 8800", "assets@example.qa", "QGI"),
    ("Hassan Al-Emadi", "+974 5566 2031", "h.emadi@example.qa", ""),
]

PARTNERS = [
    ("Barwa Real Estate", "Developer", "+974 4408 0000", "info@example.qa"),
    ("Al Tamimi & Company", "Legal", "+974 4457 2777", "doha@example.qa"),
    ("Doha Facility Care", "Maintenance", "+974 3311 9090", "service@example.qa"),
    ("Qatar National Bank", "Bank", "+974 4440 7777", "mortgages@example.qa"),
    ("Pearl Media Studio", "Marketing", "+974 5544 3311", "hello@example.qa"),
]

LEADS = [
    ("Ahmed Al-Thani", "+974 5501 7788", "ahmed.t@example.qa", "Property Finder", "New", 15000,
     "Looking for a 3-bed in The Pearl, move-in within a month."),
    ("Sarah Whitfield", "+974 3322 4455", "s.whitfield@example.com", "Website", "Qualified", 9000,
     "Expat family, needs compound with pool and near an international school."),
    ("Rashid Al-Naimi", "+974 5577 3311", "rashid@example.qa", "Referral", "Viewing", 7000000,
     "Buying a villa in Al Waab. Financing pre-approved with QNB."),
    ("Linh Nguyen", "+974 6612 8899", "linh.n@example.com", "Instagram", "New", 5500,
     "Single professional, wants a furnished studio near the metro."),
    ("Gulf Trading LLC", "+974 4433 2211", "office@example.qa", "Walk-in", "Offer", 45000,
     "Needs 400–500 m² of fitted office space in West Bay for 18 staff."),
    ("Maria Santos", "+974 3344 5566", "m.santos@example.com", "WhatsApp", "Qualified", 6500,
     "Two-bed apartment, budget is firm, prefers Lusail."),
    ("Khalid Al-Sulaiti", "+974 5599 1010", "khalid.s@example.qa", "Phone", "Won", 2350000,
     "Purchased in Viva Bahriya. Handover completed."),
    ("Peter Novak", "+974 6677 8080", "p.novak@example.com", "Website", "Lost", 8000,
     "Went with another agency — reconnect at renewal in October."),
]


DEMO_EMAILS = {"yousef@plannedrealestate.qa", "nadia@plannedrealestate.qa",
               "omar@plannedrealestate.qa"}


def looks_like_real_data():
    """Anything not created by a previous run of this script."""
    demo_titles = {p[0] for p in PROPERTIES}
    demo_names = {l[0] for l in LEADS}
    demo_owners = {o[0] for o in OWNERS}

    real_props = [r["title"] for r in query("SELECT title FROM properties")
                  if r["title"] not in demo_titles]
    real_leads = [r["full_name"] for r in query("SELECT full_name FROM leads")
                  if r["full_name"] not in demo_names]
    real_owners = [r["name"] for r in query("SELECT name FROM owners")
                   if r["name"] not in demo_owners]
    real_deals = query("SELECT COUNT(*) c FROM deals", one=True)["c"]
    return real_props, real_leads, real_owners, real_deals


def backup_first(app):
    src = app.config["DATABASE"]
    if not os.path.exists(src):
        return None
    folder = os.path.join(os.path.dirname(src), "backups")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder,
                        f"before-seed-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.sqlite3")
    shutil.copy2(src, dest)
    print(f"Backup saved: {dest}")
    return dest


def seed(reset=False, force=False):
    app = create_app()
    with app.app_context():
        if reset:
            props, leads_, owners_, deals_ = looks_like_real_data()
            if (props or leads_ or owners_ or deals_) and not force:
                print("\nSTOPPED — this database has records that aren't demo data.\n")
                for label, items in (("properties", props), ("leads", leads_),
                                     ("owners", owners_)):
                    if items:
                        shown = ", ".join(items[:4])
                        more = f" and {len(items) - 4} more" if len(items) > 4 else ""
                        print(f"  {len(items)} real {label}: {shown}{more}")
                if deals_:
                    print(f"  {deals_} deals")
                print("\n--reset would delete all of it. If that is genuinely what you")
                print("want, run:  python seed.py --reset --force")
                print("To keep it and just add demo records, run:  python seed.py\n")
                return

            backup_first(app)
            # User accounts are never touched — deleting them locks people out.
            for t in ("comments", "activity", "notifications", "viewings", "documents",
                      "property_images", "deals", "leads", "properties", "owners",
                      "partners"):
                execute(f"DELETE FROM {t}")
            print("Cleared listings, leads, deals and directory records.")
            print("Team accounts were left alone.")

        set_setting("company_name", "Planned Real Estate")
        set_setting("currency", "QAR")

        admin = query("SELECT * FROM users WHERE role='admin' ORDER BY id", one=True)
        if admin is None:
            # Without this the script used to crash halfway and leave no way in.
            email = os.environ.get("ADMIN_EMAIL", "admin@plannedrealestate.qa")
            pw = os.environ.get("ADMIN_PASSWORD", "Planned@2026")
            existing = query("SELECT id FROM users WHERE lower(email)=?",
                             (email.lower(),), one=True)
            if existing:
                execute("UPDATE users SET role='admin', is_active=1 WHERE id=?",
                        (existing["id"],))
                print(f"No admin found — promoted {email} back to admin.")
            else:
                create_user("Administrator", email, pw, "admin")
                print(f"No admin found — created {email} / {pw}")
            admin = query("SELECT * FROM users WHERE role='admin' ORDER BY id", one=True)

        agents = []
        for name, email in [("Yousef Rahmani", "yousef@plannedrealestate.qa"),
                            ("Nadia Cherif", "nadia@plannedrealestate.qa"),
                            ("Omar Haddad", "omar@plannedrealestate.qa")]:
            row = query("SELECT id FROM users WHERE email = ?", (email,), one=True)
            agents.append(row["id"] if row else
                          create_user(name, email, "Agent@2026", "agent"))
        print("Demo agent accounts ready.")

        owner_ids = [execute(
            "INSERT INTO owners (name,phone,email,company,notes,created_at)"
            " VALUES (?,?,?,?,'',?)", o + (now(),)) for o in OWNERS]

        for p in PARTNERS:
            execute("INSERT INTO partners (name,partner_type,phone,email,notes,created_at)"
                    " VALUES (?,?,?,?,'',?)", p + (now(),))

        prop_ids = []
        for i, p in enumerate(PROPERTIES):
            created = (datetime.utcnow() - timedelta(days=random.randint(3, 90))
                       ).strftime("%Y-%m-%d %H:%M:%S")
            pid = execute(
                "INSERT INTO properties (title,address,area,prop_type,listing_type,status,"
                "price,size_sqm,bedrooms,bathrooms,features,description,owner_id,agent_id,"
                "ref,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                p + ("Contact the listing agent for viewing arrangements and full "
                     "documentation.", owner_ids[i % len(owner_ids)],
                     agents[i % len(agents)], next_ref("PRE-P", "properties"),
                     created, created))
            prop_ids.append(pid)
            log(agents[i % len(agents)], "Added listing", "property", pid, p[0])

        for i, l in enumerate(LEADS):
            created = (datetime.utcnow() - timedelta(days=random.randint(0, 21))
                       ).strftime("%Y-%m-%d %H:%M:%S")
            agent = agents[i % len(agents)] if i % 4 else None
            lid = execute(
                "INSERT INTO leads (full_name,phone,email,source,status,budget,notes,"
                "agent_id,property_id,ref,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                l + (agent, prop_ids[i % len(prop_ids)],
                     next_ref("PRE-L", "leads"), created, created))
            log(agent or admin["id"], "Captured lead", "lead", lid,
                f"{l[0]} via {l[3]}")
            if agent:
                notify(agent, f"New lead assigned to you: {l[0]}", f"/leads/{lid}")

        for i in (0, 2, 4):
            when = (datetime.utcnow() + timedelta(days=i + 1, hours=3)
                    ).strftime("%Y-%m-%d %H:%M")
            lead = query("SELECT * FROM leads ORDER BY id LIMIT 1 OFFSET ?", (i,), one=True)
            execute("INSERT INTO viewings (lead_id,property_id,agent_id,scheduled_at,notes,"
                    "done,created_at) VALUES (?,?,?,?,?,0,?)",
                    (lead["id"], lead["property_id"], lead["agent_id"] or agents[0],
                     when, "Meet at the tower reception.", now()))

        execute("INSERT INTO comments (entity_type,entity_id,user_id,body,created_at)"
                " VALUES ('lead',?,?,?,?)",
                (query("SELECT id FROM leads ORDER BY id", one=True)["id"], agents[0],
                 "Called at 11:00. Wants a sea view and is flexible on the floor. "
                 "Sending three options tonight.", now()))

        # a few closed deals so the commission figures aren't empty
        closed = query("SELECT id, agent_id, price, listing_type"
                       " FROM properties WHERE status IN ('Sold','Rented')")
        pct = 2.5
        for i, prop in enumerate(closed):
            lead = query("SELECT id FROM leads WHERE property_id = ? LIMIT 1",
                         (prop["id"],), one=True)
            value = float(prop["price"] or 0)
            when = (datetime.utcnow() - timedelta(days=random.randint(2, 25))
                    ).strftime("%Y-%m-%d %H:%M:%S")
            did = execute(
                "INSERT INTO deals (property_id,lead_id,agent_id,deal_type,value,"
                "commission_pct,commission_amt,status,closed_at,notes,ref,created_at,"
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (prop["id"], lead["id"] if lead else None, prop["agent_id"],
                 prop["listing_type"], value, pct, round(value * pct / 100, 2),
                 "Collected" if i % 2 == 0 else "Signed", when, "",
                 next_ref("PRE-D", "deals"), when, when))
            log(prop["agent_id"] or admin["id"], "Recorded deal", "deal", did,
                f"{value:,.0f} at {pct}%")

        set_setting("commission_pct", "2.5")

        print("Demo data loaded.")
        print("  Agent sign-in:  yousef@plannedrealestate.qa / Agent@2026")


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv, force="--force" in sys.argv)
