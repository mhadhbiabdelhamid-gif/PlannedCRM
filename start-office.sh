#!/usr/bin/env bash
# Run this to start the CRM for the office (macOS / Linux).
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
    echo "Setting up for the first time..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi
python serve_office.py
