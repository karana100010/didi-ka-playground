"""Vercel entry point: the whole playground (API, Bhashini routes and pages) as one Python function.

Vercel sets VERCEL=1. Environment variables: see server/settings.py and README.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'server'))

from main import app  # noqa: E402,F401
