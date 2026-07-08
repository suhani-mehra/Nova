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
  - AUS (Australia, Oceania) -> Asia (nearest named region; folded in so the
    chart doesn't carry a near-empty "Other" bucket)

The named regions shown in the chart are "asia" | "na" | "eu". `continent_for`
returns None for a NULL / unmapped country_code (placeholder/null-country rows);
callers drop those employees from the region chart rather than rendering an
"Other" bar. There is no "other" region.
"""

# Confirmed, exhaustive mapping of every country_code present in the active
# employee population. Keep in sync with the data if new codes appear —
# continent_for() logs loudly for anything unmapped rather than silently
# mis-bucketing; NULL/unmapped codes return None and are dropped by callers.
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
    "AUS":  "asia",   # Australia (Oceania) — folded into Asia
    # NOTE: "OT" and NULL country codes are NOT mapped; continent_for returns
    # None for them and callers drop those placeholder rows from the region chart.
}

# Human-readable labels + fixed display order for the regions shown in the chart.
REGION_ORDER = ["asia", "na", "eu"]
REGION_LABELS = {
    "asia":  "Asia",
    "na":    "North America",
    "eu":    "Europe",
}


def continent_for(country_code) -> str | None:
    """
    Resolve a raw country_code to one of "asia" | "na" | "eu", or None.

    NULL/empty and any code not in the confirmed mapping return None (they are
    placeholder / unclassifiable rows). Callers drop None regions from the chart.
    An unexpected (non-NULL) code is logged as a warning — it means the source
    data grew a new code that should be classified explicitly in
    COUNTRY_TO_CONTINENT.
    """
    if country_code is None:
        return None
    code = str(country_code).strip().upper()
    if not code:
        return None
    mapped = COUNTRY_TO_CONTINENT.get(code)
    if mapped is not None:
        return mapped
    import logging
    logging.getLogger(__name__).warning(
        "geo.continent_for: unmapped country_code %r -> dropped from region chart; "
        "add it to COUNTRY_TO_CONTINENT if it should be counted.", country_code,
    )
    return None
