"""Create a 'Planned CRM' shortcut on the desktop, using the company logo.

    python make_shortcut.py

Converts static/img/logo.* into a Windows .ico, then makes the shortcut. If no
logo has been uploaded, a gold monogram is drawn instead.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "static", "img", "app-icon.ico")
SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def find_logo():
    folder = os.path.join(HERE, "static", "img")
    if not os.path.isdir(folder):
        return None
    for name in sorted(os.listdir(folder)):
        stem, _, ext = name.rpartition(".")
        if stem.lower() == "logo" and ext.lower() in ("png", "jpg", "jpeg", "webp"):
            return os.path.join(folder, name)
    return None


def build_icon():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("  Pillow isn't installed, so the shortcut will use the default icon.")
        print("  Run:  pip install -r requirements.txt")
        return None

    logo = find_logo()
    if logo:
        img = Image.open(logo).convert("RGBA")
        # square it off on a transparent canvas so nothing is stretched
        side = max(img.size)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
        img = canvas
        print(f"  Using {os.path.basename(logo)} for the icon.")
    else:
        img = Image.new("RGBA", (256, 256), (11, 11, 13, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([8, 8, 247, 247], outline=(200, 162, 74, 255), width=6)
        font = None
        for candidate in ("arialbd.ttf", "segoeuib.ttf", "Arial Bold.ttf",
                          "DejaVuSans-Bold.ttf",
                          "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(candidate, 150)
                break
            except OSError:
                continue
        if font is None:
            # Pillow's built-in font is tiny at default size; ask for a real one.
            try:
                font = ImageFont.load_default(size=150)
            except TypeError:          # older Pillow has no size argument
                font = ImageFont.load_default()
        draw.text((128, 122), "P", fill=(200, 162, 74, 255), anchor="mm", font=font)
        print("  No logo uploaded yet, so a gold monogram was drawn.")

    img.save(ICON, format="ICO", sizes=SIZES)
    return ICON


def desktop_folder():
    """OneDrive redirects the Desktop on many machines, so ask Windows."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Environment]::GetFolderPath('Desktop')"],
        capture_output=True, text=True, timeout=25)
    path = out.stdout.strip()
    if path and os.path.isdir(path):
        return path
    return os.path.join(os.path.expanduser("~"), "Desktop")


def make_shortcut(icon):
    desktop = desktop_folder()
    link = os.path.join(desktop, "Planned CRM.lnk")
    target = os.path.join(HERE, "open-crm.vbs")

    ps = f"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}')
$s.TargetPath = 'wscript.exe'
$s.Arguments = '"{target}"'
$s.WorkingDirectory = '{HERE}'
$s.Description = 'Planned Real Estate CRM'
{f"$s.IconLocation = '{icon},0'" if icon else ""}
$s.Save()
"""
    result = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                            capture_output=True, text=True, timeout=40)
    if result.returncode != 0:
        print("  Could not create the shortcut:", result.stderr.strip()[:200])
        return None
    return link


if __name__ == "__main__":
    if sys.platform != "win32":
        print("This script is for Windows. On a Mac, bookmark http://localhost:5000")
        sys.exit(0)
    print("\nCreating the desktop shortcut...\n")
    icon = build_icon()
    link = make_shortcut(icon)
    if link:
        print(f"\n  Done. 'Planned CRM' is on your desktop.")
        print("  Double-click it and the CRM opens, starting the server if needed.\n")
    else:
        print("\n  Something went wrong. Tell Claude what it printed above.\n")
