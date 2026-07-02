"""
core/geo.py
Maps the (non-standard) country_code values stored in Fabric's
dim_classmate_employee_profile onto the four regions used by the Manager
Overview "AI proficiency by region" chart.

The codes here are NOT ISO 3166 — they are the exact distinct values found in
the live active-employee data (verified query, 6,859 employees). Region
assignment for transcontinental / ambiguous codes was confirmed with the
product owner:
  - TUR (Turkey)  -> Asia   (Orion office located on the Asian side)
  - RUS / RF (Russia / Russian Federation) -> Europe (Orion office Europe side)
  - SWZ -> Switzerland (Europe), NOT Eswatini
  - AUS (Australia, Oceania), OT ("Other"), and NULL -> Other

Regions:  "asia" | "na" | "eu" | "other"
"""

# Confirmed, exhaustive mapping of every country_code present in the active
# employee population. Keep in sync with the data if new codes appear —
# continent_for() logs loudly for anything unmapped rather than silently
# mis-bucketing (except the explicit NULL/"other" cases below).
COUNTRY_TO_CONTINENT: dict[str, str] = {
    # Asia
    "IND":  "asia",   # India
    "PHI":  "asia",   # Philippines
    "UZKH": "asia",   # Uzbekistan / Kazakhstan (Central Asia)
    "TUR":  "asia",   # Turkey (Asian-side office)
    # North America
    "MEX":  "na",     # Mexico
    "US":   "na",     # United States
    "CAN":  "na",     # Canada
    # Europe
    "SER":  "eu",     # Serbia
    "ROM":  "eu",     # Romania
    "LIT":  "eu",     # Lithuania
    "GER":  "eu",     # Germany
    "UK":   "eu",     # United Kingdom
    "IRE":  "eu",     # Ireland
    "RUS":  "eu",     # Russia (Europe-side office)
    "RF":   "eu",     # Russian Federation
    "SWZ":  "eu",     # Switzerland
    # Other (no clean home in the three named regions)
    "OT":   "other",  # literal "Other"
    "AUS":  "other",  # Australia (Oceania)
}

# Human-readable labels + fixed display order for the four regions.
REGION_ORDER = ["asia", "na", "eu", "other"]
REGION_LABELS = {
    "asia":  "Asia",
    "na":    "North America",
    "eu":    "Europe",
    "other": "Other",
}


def continent_for(country_code) -> str:
    """
    Resolve a raw country_code to one of "asia" | "na" | "eu" | "other".

    NULL/empty and any code not in the confirmed mapping fall back to "other"
    so no employee is ever dropped from the totals. An unexpected (non-NULL)
    code is logged as a warning — it means the source data grew a new code that
    should be classified explicitly in COUNTRY_TO_CONTINENT.
    """
    if country_code is None:
        return "other"
    code = str(country_code).strip().upper()
    if not code:
        return "other"
    mapped = COUNTRY_TO_CONTINENT.get(code)
    if mapped is not None:
        return mapped
    import logging
    logging.getLogger(__name__).warning(
        "geo.continent_for: unmapped country_code %r -> bucketed as 'other'; "
        "add it to COUNTRY_TO_CONTINENT.", country_code,
    )
    return "other"
