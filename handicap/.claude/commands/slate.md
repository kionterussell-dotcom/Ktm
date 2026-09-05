---
description: Full weekly slate scan with fetch, verify, flag, and report
---
1. Run every fetcher in scripts/. Report which succeeded and which failed BEFORE analyzing.
2. Load results into data/desk.db.
3. Read anything new in manual/ and ingest it as USER-PROVIDED with source and timestamp.
4. Scan the full slate for the sport and week I name.
5. Output cards only for games hitting BOOK NEED, RLM, SHARP DIVERGENCE, or STEAM.
6. Write the report to reports/YYYY-WW-<sport>.md and log every play to desk.db.
7. State explicitly which books are missing from this week's read.
