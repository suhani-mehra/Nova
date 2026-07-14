# Deploying Nova on Azure App Service — seeding the pre-built databases

## Why this matters

The expensive part of Nova's startup is `score_all_courses`, which uses GPT to
score the whole course/certificate catalogue (~24k items, ~8 hours, many GPT
calls). Those scores live in the **persistent** `course_vertical_scores` table
inside `nova_local.db` and are **never re-scored once present** — the job only
scores items it hasn't seen before.

Everything else the app computes (per-user skill/AI scores, region/vertical
breakdowns, tiers) is derived from the warehouse + that scored-course table with
**no GPT calls**, so it rebuilds cheaply.

**The goal:** ship the already-scored `nova_local.db` so a deploy never re-runs
the 8-hour GPT job. The databases are intentionally **not** in git or the
Veracode scan (they hold employee PII and are large binaries), so they must be
seeded out-of-band — this is what caused the earlier "0 courses scored → full
rescore" incident when the DB was removed from git.

## The two database files

| File | Holds | Size |
|------|-------|------|
| `nova_local.db` | GPT-scored course catalogue (the 8h work), tiers, badges, congrats, settings, daily cache | ~13 MB |
| `nova_warehouse.db` | Nightly-synced copy of the Classmate tables | ~185 MB |

Both must live on **writable, persistent** storage — the app writes to them
(nightly warehouse sync, tier/score refresh).

## Recommended setup: persistent `/home` storage (survives deploys)

On Azure App Service, everything under **`/home`** persists across code deploys
and restarts; code deploys land in `/home/site/wwwroot` and must not hold the
DBs. Put the DBs in a sibling data dir.

### One-time seed

1. Create the data dir and upload the current, fully-scored DBs (via the Kudu
   console at `https://<app-name>.scm.azurewebsites.net` → **Debug console**, or
   FTPS):
   ```
   /home/data/nova_local.db
   /home/data/nova_warehouse.db
   ```
   Copy them from a machine where scoring is already complete (e.g. your local
   `nova_backend/nova_local.db`).

2. Set these **Application settings** (Configuration → Application settings):
   ```
   NOVA_LOCAL_DB_PATH        = /home/data/nova_local.db
   WAREHOUSE_DB_PATH         = /home/data/nova_warehouse.db
   NOVA_COURSE_SCORING_ENABLED = false
   NOVA_ENV                  = production
   NOVA_DEV_BYPASS           = false
   NOVA_CORS_ORIGINS         = ["https://<your-prod-host>"]
   EXEC_USER_IDS             = [5575,16467,16465,16470]
   EXEC_DEV_USER_IDS         = []
   ```
   (Secrets — OpenAI/Azure/API keys, `NOVA_SECRET_KEY` — come from Key Vault
   references, not from a committed `.env`.)

3. Deploy the code (zip deploy / GitHub Actions / container). Because the DBs
   live under `/home/data`, a code deploy never touches them.

### Verify the seed worked

On startup the log prints one of:
```
course scores: 24890 courses already scored (path=/home/data/nova_local.db)   ✅
course scores: 0 courses scored — nova_local.db appears unseeded (path=...)    ❌ reseed
```

## The safety backstop

`NOVA_COURSE_SCORING_ENABLED=false` (set in step 2) means `score_all_courses`
**never calls GPT** — even if the DB were empty, production can't silently burn
8 hours of GPT. The app just serves whatever scores are seeded.

To score newly-added courses later: set `NOVA_COURSE_SCORING_ENABLED=true`
temporarily, restart (it scores only the *new* items — fast), then set it back
to `false`. Or run the scoring locally and re-upload `nova_local.db`.

## Alternative: bake into a container image

If deploying a container, `COPY` both DBs into the image and point the env vars
at their in-container location on a **writable** path (or a mounted Azure Files
share for persistence). The scored catalogue travels with the image, so
`score_all_courses` finds everything scored and makes zero GPT calls. Note:
without a mounted volume, nightly warehouse updates are lost on restart and
re-derived — which is fine (no GPT involved).

## What NOT to do

- Do **not** re-add `*.db` to git or the Veracode upload (PII + size; they are
  in `.gitignore` / `.veracodeignore`).
- Do **not** store the DBs only in `/home/site/wwwroot` — a clean/zip deploy can
  remove files there.
