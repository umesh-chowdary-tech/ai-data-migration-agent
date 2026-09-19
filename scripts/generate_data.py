"""Generate the fictitious client's raw exports (v1) and a later re-export (v2, for the delta demo).

The three files describe the same employees from three different legacy systems, each with its own
column names, formats and mistakes. Every "trap" below is deliberate and documented so the escalation
boundary can be demonstrated and defended.

  data/samples/v1/hrms_legacy_export.csv   - old HRMS (DD/MM/YYYY dates, "Full Name", dept abbreviations)
  data/samples/v1/payroll_export.xlsx      - payroll (Excel dates, "12 LPA" salaries, EMP-0001 style IDs)
  data/samples/v1/onboarding_tracker.csv   - recruiter sheet for new joiners ("Last, First", MM/DD dates)

Run:  python scripts/generate_data.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import random

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "samples"
DOMAIN = "acmecorp.com"

FIRST = ["Aarav", "Vivaan", "Aditya", "Arjun", "Sai", "Reyansh", "Ishaan", "Kabir", "Rohan", "Karthik",
         "Siddharth", "Aniket", "Manish", "Deepak", "Harsh", "Nikhil", "Pranav", "Varun", "Yash", "Rajesh",
         "Aadhya", "Diya", "Saanvi", "Ira", "Meera", "Kavya", "Anika", "Riya", "Pooja", "Sneha", "Divya",
         "Lakshmi", "Shreya", "Tanvi", "Nandini", "Isha", "Pallavi", "Swati", "Aishwarya", "Bhavana"]
LAST = ["Sharma", "Patel", "Reddy", "Nair", "Menon", "Rao", "Das", "Joshi", "Kulkarni", "Bose",
        "Chatterjee", "Pillai", "Desai", "Mehta", "Malhotra", "Kapoor", "Banerjee", "Hegde", "Shetty",
        "Naidu", "Mishra", "Pandey", "Saxena"]

DEPTS = ["Engineering", "Sales", "Marketing", "Human Resources", "Finance", "Operations"]
DEPT_WEIGHTS = [35, 20, 12, 8, 10, 15]
TITLES = {
    "Engineering": ("VP Engineering", ["Software Engineer", "Senior Software Engineer", "QA Engineer", "DevOps Engineer"]),
    "Sales": ("VP Sales", ["Account Executive", "Sales Manager", "Inside Sales Rep"]),
    "Marketing": ("Head of Marketing", ["Marketing Specialist", "Content Strategist", "Growth Manager"]),
    "Human Resources": ("Head of People", ["HR Business Partner", "Talent Acquisition Specialist", "HR Generalist"]),
    "Finance": ("Finance Controller", ["Accountant", "Financial Analyst", "Payroll Specialist"]),
    "Operations": ("Head of Operations", ["Operations Executive", "Facilities Manager", "Operations Analyst"]),
}
DEPT_SPELLINGS = {
    "Engineering": ["Engineering", "Engg", "Tech", "engineering"],
    "Sales": ["Sales", "sales", "BD"],
    "Marketing": ["Marketing", "Mktg"],
    "Human Resources": ["HR", "People", "Human Resources"],
    "Finance": ["Finance", "Accounts", "Fin"],
    "Operations": ["Operations", "Ops", "Admin"],
}
CITIES = ["Bengaluru", "Hyderabad", "Mumbai", "Pune", "Delhi NCR", "Chennai"]
CITY_SPELLINGS = {
    "Bengaluru": ["Bangalore", "Bengaluru", "BLR"], "Hyderabad": ["Hyderabad", "Hyd"],
    "Mumbai": ["Mumbai", "Bombay"], "Pune": ["Pune"], "Delhi NCR": ["Gurgaon", "Delhi", "Noida"],
    "Chennai": ["Chennai"],
}
BLOOD = ["A+", "A-", "B+", "B-", "O+", "O-", "AB+"]

# Planted people (index -> (first, last)) - see README "Test data traps".
PLANTED_NAMES = {
    12: ("Priya", "Nair"),       # messy casing / whitespace in HRMS
    29: ("Rahul", "Verma"),      # email missing TLD -> fails validation twice -> escalate
    33: ("Neha", "Gupta"),       # email "acmecorp,com" -> fails once, auto-repaired
    38: ("Ananya", "Iyer"),      # already exists in target as EMP9001 -> push 409
    40: ("Amit", "Kumar"),       # same name as 44 but different DOB -> treated as different people
    44: ("Amit", "Kumar"),
    50: ("Vikram", "Singh"),     # re-entered in onboarding as EMP0056 with same DOB -> fuzzy duplicate
    56: ("Vikram", "Singh"),
}


def rand_date(rng: random.Random, start: dt.date, end: dt.date, max_day: int = 28) -> dt.date:
    while True:
        d = start + dt.timedelta(days=rng.randint(0, (end - start).days))
        if d.day <= max_day:
            return d


def inr(n: int) -> str:
    """12,50,000 style (Indian digit grouping)."""
    s = str(n)
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail]) if groups else tail


def phone(rng: random.Random) -> str:
    return str(rng.choice([9, 8, 7, 6])) + "".join(str(rng.randint(0, 9)) for _ in range(9))


def build_people(rng: random.Random, count: int) -> dict[int, dict]:
    used = {n for n in PLANTED_NAMES.values()}
    people: dict[int, dict] = {}
    for i in range(1, count + 1):
        if i in PLANTED_NAMES:
            first, last = PLANTED_NAMES[i]
        else:
            while True:
                first, last = rng.choice(FIRST), rng.choice(LAST)
                if (first, last) not in used:
                    used.add((first, last))
                    break
        dept = DEPTS[i - 1] if i <= 6 else rng.choices(DEPTS, DEPT_WEIGHTS)[0]
        head_title, titles = TITLES[dept]
        new_joiner = i >= 52
        emp_type = "Full-Time" if i <= 6 else rng.choices(
            ["Full-Time", "Contract", "Part-Time", "Intern"],
            [80, 12, 4, 4] if not new_joiner else [60, 5, 0, 35])[0]
        title = head_title if i <= 6 else (f"{dept.split()[0]} Intern" if emp_type == "Intern" else rng.choice(titles))
        if i <= 6:
            doj = rand_date(rng, dt.date(2014, 1, 1), dt.date(2016, 12, 31))
        elif new_joiner:
            doj = rand_date(rng, dt.date(2025, 1, 1), dt.date(2026, 8, 31), max_day=12)  # day <= 12 -> ambiguous
        else:
            doj = rand_date(rng, dt.date(2015, 1, 1), dt.date(2024, 12, 31))
        dob = rand_date(rng, dt.date(2000, 1, 1), dt.date(2004, 12, 31)) if emp_type == "Intern" \
            else rand_date(rng, dt.date(1975, 1, 1), dt.date(2001, 12, 31))
        if i <= 6:
            salary = rng.randrange(3_500_000, 5_000_000, 10_000)
        elif emp_type == "Intern":
            salary = rng.randrange(300_000, 420_000, 10_000)
        else:
            salary = rng.randrange(600_000, 2_400_000, 10_000)
        suffix = "2" if i in (44, 56) else ""
        people[i] = dict(
            id=f"EMP{i:04d}", first=first, last=last,
            email=f"{first.lower()}.{last.lower()}{suffix}@{DOMAIN}",
            dept=dept, title=title, doj=doj, dob=dob, emp_type=emp_type, salary=salary,
            city=rng.choice(CITIES), phone=phone(rng),
            status="Inactive" if i in (9, 26, 35, 47) else "Active",
        )
    heads = {people[i]["dept"]: people[i]["email"] for i in range(1, 7)}
    for i, p in people.items():
        p["manager"] = "" if i <= 6 else heads[p["dept"]]
    people[56]["dob"] = people[50]["dob"]          # same person entered twice?
    people[42]["manager"] = f"suresh.menon@{DOMAIN}"  # manager who is not in any file / target
    return people


def hrms_rows(people: dict[int, dict], version: int) -> list[dict]:
    rows = []
    for i in range(1, 51):
        rng = random.Random(1000 + i)  # per-row RNG so v2 edits do not reshuffle other rows
        p = dict(people[i])
        if version == 2 and i == 10:
            p["dept"], p["title"] = "Sales", "Account Executive"             # transfer (was Marketing)
        if version == 2 and i == 20:
            p["title"] = "Senior " + p["title"].replace("Senior ", "")      # promotion
        if version == 2 and i == 33:
            p["phone"] = "9812345670"                                         # new mobile number
        full = f"{p['first']} {p['last']}"
        if i == 12:
            full = "  priya   nair "
        elif i in (19, 36):
            full = full.upper()
        elif i in (27, 45):
            full = full.lower()
        email = p["email"]
        if i in (8, 21, 34):
            email = email.replace(p["first"].lower(), p["first"]).replace(DOMAIN, DOMAIN.upper()) + " "
        if i == 29:
            email = f"rahul.verma@acmecorp"
        if i == 33:
            email = f"neha.gupta@acmecorp,com"
        if i == 26:
            email = email.replace(".com", ".co")  # payroll has the right one -> valid value wins
        dept = rng.choice(DEPT_SPELLINGS[p["dept"]])
        if i in (23, 31):
            dept = "Special Projects"
        ph = p["phone"]
        mobile = rng.choice([ph, f"+91 {ph[:5]} {ph[5:]}", f"0{ph[:5]}-{ph[5:]}", f"+91-{ph}"])
        if i == 4:
            mobile = "N/A"
        elif i == 22:
            mobile = "-"
        elif i == 41:
            mobile = ""
        elif i == 17:
            mobile = "98765"
        status = rng.choice(["Active", "Y", "active", "A"]) if p["status"] == "Active" else rng.choice(["Inactive", "N", "Resigned"])
        rows.append({
            "Emp ID": p["id"], "Full Name": full, "E-mail": email, "Dept": dept, "Designation": p["title"],
            "DOJ": p["doj"].strftime("%d/%m/%Y"), "Birth Date": p["dob"].strftime("%d-%b-%Y"),
            "Mobile No": mobile, "Status": status, "Location": rng.choice(CITY_SPELLINGS[p["city"]]),
            "Blood Group": rng.choice(BLOOD),
        })
        if i == 7:
            rows.append(dict(rows[-1]))  # exact duplicate row
    return rows


def payroll_rows(people: dict[int, dict]) -> list[dict]:
    rows = []
    for i in [i for i in range(1, 49) if i not in (3, 9, 29, 33)] + [51, 52]:
        rng = random.Random(2000 + i)
        p = people[i]
        code = [f"emp{i:04d}", f"EMP{i:04d}", f"EMP-{i:04d}"][i % 3]
        sal = p["salary"]
        ctc = [sal, inr(sal), f"{sal / 100000:g} LPA", f"₹ {inr(sal)}"][i % 4]
        start: object = dt.datetime.combine(p["doj"], dt.time())
        if i == 15:
            start = dt.datetime.combine(p["doj"] + dt.timedelta(days=10), dt.time())  # disagrees with HRMS
        elif i % 5 == 0:
            start = p["doj"].isoformat()
        et = {"Full-Time": ["FT", "Permanent", "Full Time", "Full-Time"], "Contract": ["Contractor", "Contract", "C2H"],
              "Part-Time": ["PT", "Part Time"], "Intern": ["Intern", "Trainee"]}[p["emp_type"]]
        rows.append({
            "employee_code": code, "first_name": p["first"], "last_name": p["last"], "work_email": p["email"],
            "annual_ctc": ctc, "start_date": start, "emp_type": rng.choice(et), "reporting_manager": p["manager"],
        })
    return rows


def onboarding_rows(people: dict[int, dict], version: int) -> list[dict]:
    rows = []
    ids = list(range(52, 61)) + ([61, 62] if version == 2 else [])
    for i in ids:
        rng = random.Random(3000 + i)
        p = people[i]
        email = p["email"]
        if i == 57:
            email = f"{p['first'].lower()}{p['last'].lower()}94@gmail.com"
        if i == 59:
            email = f"{p['first'].lower()}.{p['last'][0].lower()}@yahoo.co.in"
        team = rng.choice([p["dept"], p["dept"], "People Ops" if p["dept"] == "Human Resources" else p["dept"]])
        if i == 62:
            team = "Data Science"  # new, unknown department value in the re-export
        rows.append({
            "ID": p["id"], "Name": f"{p['last']}, {p['first']}", "Email": email,
            "Contact Number": f"+91 {p['phone']}", "Team": team,
            "Joining Date": p["doj"].strftime("%m/%d/%Y"), "Date of Birth": p["dob"].isoformat(),
            "Type": p["emp_type"], "Base Location": p["city"],
        })
    return rows


def main() -> None:
    for version in (1, 2):
        rng = random.Random(7)
        people = build_people(rng, 62)
        folder = OUT / f"v{version}"
        folder.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(hrms_rows(people, version)).to_csv(folder / "hrms_legacy_export.csv", index=False)
        pd.DataFrame(payroll_rows(people)).to_excel(folder / "payroll_export.xlsx", index=False)
        pd.DataFrame(onboarding_rows(people, version)).to_csv(folder / "onboarding_tracker.csv", index=False)
        print(f"wrote {folder}")


if __name__ == "__main__":
    main()
