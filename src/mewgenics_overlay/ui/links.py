"""Single home for the project's website URLs.

Every UI module that hands a link to the OS browser (update notice, About
dialog, report button) reads it from here, so the domain lives in exactly one
place. GitHub API/release constants stay in ``ui/update_check.py``: those
describe the release check, not the user-facing website.
"""

from __future__ import annotations

# the project website: landing page, #download section and /report form
SITE_URL = "https://mewgenics.thebeard.be/"
DOWNLOAD_URL = SITE_URL + "#download"
REPORT_URL = SITE_URL + "report"
