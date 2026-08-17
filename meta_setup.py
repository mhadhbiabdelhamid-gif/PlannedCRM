"""
Fetches the Meta values needed by /social/settings.

    python meta_setup.py

It asks for three things, talks to Meta, and prints your Page ID, Instagram
Business account ID, and a long-lived Page access token. Nothing is saved to
disk and nothing is sent anywhere except graph.facebook.com.

Before running, get these from developers.facebook.com:

1. App ID and App Secret
   Your app -> Settings -> Basic. Click Show next to the secret.

2. A short-lived User token
   Tools -> Graph API Explorer. Pick your app in the dropdown, then add these
   permissions and press Generate Access Token:
       pages_show_list
       pages_read_engagement
       pages_manage_posts
       instagram_business_basic
       instagram_business_content_publish
   Approve the dialog and choose the Planned Real Estate Page when asked.
   The token appears in the box at the top. It expires in about an hour, which
   is fine - this script trades it for a lasting one.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from getpass import getpass

GRAPH = "https://graph.facebook.com/v21.0"


def get(path, **params):
    url = f"{GRAPH}/{path}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            msg = json.loads(body)["error"]["message"]
        except Exception:
            msg = body[:300]
        sys.exit(f"\nMeta said no ({exc.code}): {msg}")
    except urllib.error.URLError as exc:
        sys.exit(f"\nCouldn't reach Meta: {exc.reason}")


def main():
    print(__doc__)
    app_id = input("App ID: ").strip()
    app_secret = getpass("App Secret (hidden as you type): ").strip()
    short = getpass("Short-lived User token (hidden as you type): ").strip()

    if not (app_id and app_secret and short):
        sys.exit("All three are needed.")

    print("\nTrading the short token for a long-lived one...")
    long_token = get("oauth/access_token",
                     grant_type="fb_exchange_token",
                     client_id=app_id,
                     client_secret=app_secret,
                     fb_exchange_token=short)["access_token"]
    print("  done")

    print("Looking up your Pages...")
    pages = get("me/accounts", access_token=long_token).get("data", [])
    if not pages:
        sys.exit("No Pages came back. Check the token has pages_show_list and "
                 "that you are an admin of the Page.")

    if len(pages) == 1:
        page = pages[0]
    else:
        print()
        for i, p in enumerate(pages, 1):
            print(f"  {i}. {p['name']}  ({p['id']})")
        choice = input("\nWhich Page? ").strip()
        try:
            page = pages[int(choice) - 1]
        except (ValueError, IndexError):
            sys.exit("That wasn't one of the options.")

    print(f"Using Page: {page['name']}")

    print("Looking up the linked Instagram account...")
    linked = get(page["id"], fields="instagram_business_account",
                 access_token=long_token)
    ig = (linked.get("instagram_business_account") or {}).get("id")

    print("\n" + "=" * 62)
    print("Paste these into /social/settings")
    print("=" * 62)
    print(f"\nFacebook Page ID:\n  {page['id']}")
    if ig:
        print(f"\nInstagram Business account ID:\n  {ig}")
    else:
        print("\nInstagram Business account ID:\n  none found")
        print("  The Instagram account is either not a Business account, or")
        print("  not linked to this Page. Fix that in the Instagram app under")
        print("  Settings -> Account type, then link it to the Page, and run")
        print("  this again.")
    print(f"\nPage access token:\n  {page['access_token']}")
    print("\n" + "=" * 62)
    print("Treat the token like a password. Anyone holding it can post as your")
    print("Page. Don't paste it into chats, screenshots or GitHub.")
    print("=" * 62)


if __name__ == "__main__":
    main()