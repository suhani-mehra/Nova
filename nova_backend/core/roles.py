"""
core/roles.py
Classifies an employee_role row into one of six AI-proficiency "role groups"
(A–F) for the Manager Overview "Specialization Landscape" chart.

WHY department_name IS THE PRIMARY KEY
--------------------------------------
job_title / known_as_name have 1,000+ unique values (typos, language variants,
JT codes) — impractical to hand-map. designation_title (Engineer/Manager/…) is
cleaner but ambiguous ("Engineer" appears across Dev, QA, DevOps, Data, etc.).
department_name (90 distinct values) is the most reliable functional signal, so
it is the primary dictionary. A few designation_title values ALWAYS override the
department (e.g. an Architect in the Development dept is still Architecture).

THE UNRELIABLE CASES
--------------------
"Delivery" (a catch-all placeholder dept) and Intern/Trainee/blank designations
give no functional signal, so they fall back to keyword-matching the role title
text (known_as_name, then job_title). Unseen departments also route through the
keyword fallback so new departments still classify sensibly.
"""

import re

# ── Group labels / display order / colors ─────────────────────────────────────
GROUP_ORDER = ["A", "B", "C", "D", "E", "F"]

GROUP_LABELS = {
    "A": "Engineering, Development & QA",
    "B": "Architecture & Technical Leadership",
    "C": "Business Analysis, Data & Consulting",
    "D": "Delivery Coordination",
    "E": "Infra & Ops Support",
    "F": "Corporate & Enablement",
}

# Distinct brand hexes, one per group (used by the stacked bar + legend).
GROUP_COLORS = {
    "A": "#FF4398",
    "B": "#A634FF",
    "C": "#2ACCFF",
    "D": "#e08531",
    "E": "#5400DC",
    "F": "#f5b71e",
}

# ── 1. PRIMARY DICTIONARY: department_name -> group (all 90 dataset values) ────
DEPARTMENT_TO_GROUP = {
    # Group A: Engineering, Development & QA
    "Development": "A",
    "Quality Assurance": "A",
    "Quality assurance": "A",
    "UI/UX": "A",
    "DevOps": "A",
    "Adobe": "A",
    "Digital Product Engineering": "A",
    "Enterprise Platforms": "A",
    "Integration": "A",
    "Quality Engineering": "A",

    # Group B: Architecture & Technical Leadership
    "Architecture": "B",
    "Application": "B",
    "Cloud": "B",
    "Cloud & Infrastructure": "B",
    "Experience Design": "B",
    "Generative AI": "B",
    "Cybersecurity": "E",

    # Group C: Business Analysis, Data & Consulting
    "Business Analysis": "C",
    "Data Engineering": "C",
    "Data Science": "C",
    "Data": "C",
    "Data & Analytics": "C",
    "Solutions & Consulting": "C",
    "Implementation": "C",
    "BI & Visualization": "C",

    # Group D: Delivery Coordination
    "Project Management": "D",
    "Project management": "D",
    "Delivery Management": "D",
    "Program Management": "D",
    "Product Management": "D",
    "Project Management Office (PMO)": "D",
    "Delivery Operations": "D",
    "Business Transformation & Automation": "D",

    # Group E: Infra & Ops Support
    "Support Services": "E",
    "Infrastructure Management": "E",
    "Infrastructure": "E",
    "Managed Services": "E",
    "Database Administration": "E",
    "IT Network": "E",
    "IT Security": "E",
    "Security": "E",

    # Group F: Corporate & Enablement
    "Systems": "F",
    "Talent Acquisition": "F",
    "Administration": "F",
    "Business Finance": "F",
    "IT Systems": "F",
    "Marketing": "F",
    "Financial Planning & Analysis": "F",
    "Management": "F",
    "People Operations": "F",
    "People operations": "F",
    "Business Operations": "F",
    "People Relations": "F",
    "Compliance": "F",
    "Learning & Development": "F",
    "RMG Operations": "F",
    "Deal Enablement": "F",
    "Executive Management": "F",
    "People & Culture": "F",
    "TA Operations": "F",
    "Legal": "F",
    "Shared Services": "F",
    "Facilities & Administration": "F",
    "Immigration": "F",
    "Executive Assistant": "F",
    "Executive assistant": "F",
    "Revenue Operations": "F",
    "Analytics & Excellence": "F",
    "Talent Branding": "F",
    "Finance": "F",
    "Payroll": "F",
    "People Benefits": "F",
    "Growth": "F",
    "Compensation": "F",
    "Total Rewards": "F",
    "Global Compliance": "F",
    "Alliance": "F",
    # Sales BU departments
    "Business Development": "F",
    "Client Management": "F",
    "Rainmaker": "F",
    "Industrial Solutions Group": "F",
    "Telecommunications, Media & Technology (TMT)": "F",
    "Infra & Others": "F",
    "Financial Services": "F",
    "Hybrid": "F",
}

# ── 2. TITLE OVERRIDES: win over department (distinct seniority/coordination) ──
DESIGNATION_OVERRIDES = {
    "Architect": "B",
    "Senior Architect": "B",
    "Principal Architect": "B",
    "Technical Architect": "B",
    "Senior Technical Architect": "B",
    "Solution Architect": "B",
    "Senior Solution Architect": "B",
    "Associate Architect": "B",
    "Lead": "B",
    "Senior Lead": "B",
    "Product Owner": "D",
    "Senior Product Owner": "D",
    "Associate Product Owner": "D",
    "Scrum Master": "D",
    "Project Coordinator": "D",
}

# Designations with no signal by themselves -> fall back to department/keywords.
FALLBACK_DESIGNATIONS = {"Intern", "Trainee", ""}

# Departments that give no functional signal (generic placeholders).
FALLBACK_DEPARTMENTS = {"Delivery"}

# ── 3. KEYWORD FALLBACK ───────────────────────────────────────────────────────
# Used when department + designation give no signal (Delivery / Intern-Trainee-
# blank) AND for unseen departments, so new roles still classify. Matched against
# known_as_name (then job_title). First match wins — specific rules FIRST, generic
# developer/engineer catch-all LAST.
KEYWORD_RULES = [
    # Architecture & technical leadership
    (r"architect|solution architect", "B"),
    (r"\btech(nical)? ?lead\b|\bteam lead\b|\blead\b|staff engineer|principal engineer", "B"),
    # Delivery coordination
    (r"scrum master|product owner|\bpm\b|project manager|program manager|"
     r"delivery manager|delivery lead|director,? delivery|project coordinator|"
     r"release manager|agile coach", "D"),
    # Business analysis, data & consulting
    (r"data scientist|data engineer|\bml engineer\b|machine learning|"
     r"business analyst|data analyst|\bbi\b|analytics|\bconsult|analyst\b", "C"),
    # Corporate & enablement (HR / finance / legal / marketing / admin / sales)
    (r"marketing|finance|financial|account(ant|ing)|\bhr\b|human resources|"
     r"recruit|talent|payroll|legal|complian|people operations|administrat|"
     r"sales|business development|account manager|customer success", "F"),
    # Infra & ops support
    (r"support|help ?desk|service desk|monitoring|\bnoc\b|\bsoc\b|infrastructure|"
     r"network|\bdba\b|database admin|sysadmin|system admin|security|infosec|"
     r"cyber|site reliability|\bsre\b", "E"),
    # Engineering / dev / QA (generic catch-all — must stay last)
    (r"devops|developer|engineer|programmer|\bui\b|\bux\b|designer|"
     r"tester|\bqa\b|\bsdet\b|automation", "A"),
]


def classify_role(department_name, designation_title, known_as_name="",
                  job_title="", business_unit=""):
    """
    Return one of 'A'..'F' for an employee_role row.
    Priority:
      1. Title overrides (Architect-family, Lead, Product Owner, Scrum Master,
         Project Coordinator) — always win.
      2. Placeholder designation (Intern/Trainee/blank) OR placeholder/unseen
         department -> keyword-match the role title text.
      3. Otherwise -> department_name lookup.
      4. Safety net -> business_unit (Sales/Enablement => F), else default 'A'.
    """
    designation_title = (designation_title or "").strip()
    department_name = (department_name or "").strip()
    known_as_name = (known_as_name or "").strip()
    job_title = (job_title or "").strip()
    business_unit = (business_unit or "").strip()

    # 1. Title overrides win regardless of department
    if designation_title in DESIGNATION_OVERRIDES:
        return DESIGNATION_OVERRIDES[designation_title]

    known_dept = department_name in DEPARTMENT_TO_GROUP

    # 2. Keyword fallback for placeholder designation / placeholder dept / unseen dept
    needs_fallback = (
        designation_title in FALLBACK_DESIGNATIONS
        or department_name in FALLBACK_DEPARTMENTS
        or not known_dept
    )
    if needs_fallback:
        text = (known_as_name or job_title).lower()
        for pattern, group in KEYWORD_RULES:
            if re.search(pattern, text):
                return group
        # Nothing matched — last resort: business unit, then default.
        if business_unit in ("Sales", "Enablement"):
            return "F"
        # Known department but a placeholder designation: trust the department.
        if known_dept:
            return DEPARTMENT_TO_GROUP[department_name]
        return "A"  # Technology BU default when title gives zero signal

    # 3. Straight department lookup
    return DEPARTMENT_TO_GROUP[department_name]
