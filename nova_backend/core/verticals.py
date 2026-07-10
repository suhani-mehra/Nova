"""
core/verticals.py
Maps the raw `vertical_name` values from the Classmate `employee_role` table onto
the nine business-unit "industry groups" shown in the Manager Overview
"Proficiency by Vertical" chart.

The codes here are the exact distinct values found in the live employee_role
data. group_for() falls back to "Others" for anything blank or unmapped, so a
new vertical code never crashes the chart (it just lands in Others until it's
classified explicitly here).
"""

# Confirmed mapping of every vertical_name present in employee_role → group.
VERTICAL_TO_GROUP: dict[str, str] = {
    "310-KPMG": "KPMG",
    "910-Corporate": "Corporate",
    "730-Engineering-Technology": "Industrial & Consumer Technologies",
    "720-Engineering-I&CT": "Industrial & Consumer Technologies",  # I&CT = Industrial & Consumer Tech
    "110-BFSI": "BFSI",
    "410-Telecom": "Telecom, Media and Technology",
    "430-Engineering-Media": "Telecom, Media and Technology",
    "350-Professional Services": "Professional Services",
    "450-Sports": "Sports and Entertainment",
    "120-Temenos": "BFSI",              # Temenos = core banking software client
    "210-HCLS": "Healthcare & Life Sciences",

    # --- judgment calls / no clean industry match -> Others ---
    "830-Mexico-Services": "Others",     # delivery-center location tag, not an industry
    "810-Mexico-Infra": "Others",
    "230-Cloud & Infra Services(CIS)": "Others",  # internal service line
    "250-Others": "Others",              # literally named Others
    "380-Embrace": "Others",
    "490-Bus Dev 2": "Others",           # internal business-dev bucket
    "190-Bus Dev 1": "Others",
    "510-Xdesign": "Others",             # design practice, not an industry
    "": "Others",                        # blank/unassigned
}

# Canonical display order for the nine groups.
GROUP_ORDER = [
    "Sports and Entertainment",
    "Telecom, Media and Technology",
    "Industrial & Consumer Technologies",
    "Healthcare & Life Sciences",
    "KPMG",
    "Others",
    "Professional Services",
    "Corporate",
    "BFSI",
]


def group_for(vertical_name) -> str:
    """Resolve a raw vertical_name to its industry group; unknown/blank → 'Others'."""
    if vertical_name is None:
        return "Others"
    return VERTICAL_TO_GROUP.get(str(vertical_name).strip(), "Others")
