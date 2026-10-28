"""Gunicorn settings for the live server (Render).

Gunicorn reads this file automatically from the folder it starts in, so these
apply whatever start command is set in the Render dashboard (anything given on
the command line still wins).

Why threads: when a post is published, the CRM waits on Facebook/Instagram,
and they in turn download the post image *from the CRM* during that wait. With
plain one-request-at-a-time workers, no one is free to serve that image and the
download times out ("Missing or invalid image file"). Threads let a worker
serve the image while another request is waiting. The app is already
thread-safe: the office PC has always run it with 8 threads under waitress.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
worker_class = "gthread"
threads = 8
timeout = 120
