"""Generate the fictitious client's raw exports AND the ground truth they were derived from.

Three files describe the same employees from three legacy systems, each with its own column names, formats and
mistakes. Every "trap" is assigned to a role (e.g. "the person whose email lost its .com") so the same traps can be
placed on different employees, with different header spellings, for held-out evaluation.

  data/samples/v1/                 canonical first extract (documented in the README)
  data/samples/v2/                 the same client a month later (delta demo)
  <out>/heldout-N/v1, v2           randomised variants: other people, trap positions and header spellings

Each folder also gets ground_truth.json: the expected final target state, the correct mapping / date format for
every column, the genuinely ambiguous cases (what the agent SHOULD ask, with the right answer) and the traps it
should handle WITHOUT asking. evals/ scores the agent against it.

Run:  python scripts/generate_data.py                      (canonical v1 + v2)
      python scripts/generate_data.py --heldout 5 --out X   (randomised held-out sets, seeds 101..105)
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
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

FILES = {"hrms": "hrms_legacy_export.csv", "payroll": "payroll_export.xlsx", "onboarding": "onboarding_tracker.csv"}

# logical column -> (correct target field, header spellings). The first spelling is the canonical one; the rest are
# used for held-out sets - several of them are deliberately NOT in the schema's alias lists.
COLUMNS: dict[str, dict[str, tuple[str | None, list[str]]]] = {
    "hrms": {
        "id": ("employee_id", ["Emp ID", "Employee No", "Staff ID", "Employee Number"]),
        "name": ("full_name", ["Full Name", "Employee Name", "Name"]),
        "email": ("email", ["E-mail", "Official Email", "Email ID", "Work Mail"]),
        "dept": ("department", ["Dept", "Department", "Function", "Division"]),
        "title": ("job_title", ["Designation", "Role", "Job Title", "Position"]),
        "doj": ("date_of_joining", ["DOJ", "Date of Joining", "Joined On", "Hire Date"]),
        "dob": ("date_of_birth", ["Birth Date", "DOB", "Date of Birth", "Birthday"]),
        "mobile": ("phone", ["Mobile No", "Mobile", "Cell Phone", "Phone No."]),
        "status": ("status", ["Status", "Emp Status", "Employment Status", "Active?"]),
        "location": ("location", ["Location", "Office", "Work Location", "City"]),
        "blood": (None, ["Blood Group", "Blood Type"]),
    },
    "payroll": {
        "id": ("employee_id", ["employee_code", "emp_code", "EmployeeID", "Emp No"]),
        "first": ("first_name", ["first_name", "FirstName", "Given Name"]),
        "last": ("last_name", ["last_name", "LastName", "Surname"]),
        "email": ("email", ["work_email", "email", "official_email", "Email Address"]),
        "salary": ("annual_salary", ["annual_ctc", "CTC (Annual)", "Gross Salary", "Annual Pay"]),
        "start": ("date_of_joining", ["start_date", "DateOfJoining", "hire_date", "Joined"]),
        "type": ("employment_type", ["emp_type", "employment_type", "Worker Type", "Category"]),
        "manager": ("manager_email", ["reporting_manager", "manager_email", "Reports To", "Line Manager"]),
    },
    "onboarding": {
        "id": ("employee_id", ["ID", "Employee ID", "Emp Code"]),
        "name": ("full_name", ["Name", "Candidate Name", "Joiner Name"]),
        "email": ("email", ["Email", "Email Address", "E-mail"]),
        "contact": ("phone", ["Contact Number"]),  # the planted ambiguity: own mobile vs emergency contact
        "team": ("department", ["Team", "Department", "Function"]),
        "joining": ("date_of_joining", ["Joining Date", "Start Date", "Date of Joining"]),
        "dob": ("date_of_birth", ["Date of Birth", "DOB", "Birth Date"]),
        "type": ("employment_type", ["Type", "Hire Type", "Employment Type"]),
        "location": ("location", ["Base Location", "Location", "Office"]),
    },
}
DATE_FORMATS = {("hrms", "doj"): "DMY", ("hrms", "dob"): "iso", ("payroll", "start"): "iso",
                ("onboarding", "joining"): "MDY", ("onboarding", "dob"): "iso"}
SCHEMA_FIELDS = ["employee_id", "first_name", "last_name", "email", "personal_email", "phone",
                 "emergency_contact_phone", "department", "job_title", "date_of_joining", "date_of_birth",
                 "employment_type", "manager_email", "annual_salary", "location", "status"]


@dataclasses.dataclass
class Roles:
    """Which employee plays which trap. Defaults = the canonical dataset described in the README."""
    messy_name: int = 12
    upper: tuple = (19, 36)
    lower: tuple = (27, 45)
    email_case: tuple = (8, 21, 34)
    no_tld: int = 29                 # email lost its .com -> fails validation twice -> ask
    comma_email: int = 33            # acmecorp,com -> repaired automatically on the 2nd attempt
    wrong_tld: int = 26              # .co in HRMS, .com in payroll -> the valid value wins automatically
    special_projects: tuple = (23, 31)
    placeholders: tuple = ((4, "N/A"), (22, "-"), (41, ""))
    bad_phone: int = 17
    exact_dup: int = 7
    inactive: tuple = (9, 26, 35, 47)
    not_in_payroll: tuple = (3, 9)   # (no_tld and comma_email are never in payroll either)
    doj_conflict: int = 15
    target_dup: int = 38             # same email as EMP9001 already in the target -> 409 -> ask
    same_name: tuple = (40, 44)      # same name, different birth date -> different people, don't ask
    dup_pair: tuple = (50, 56)       # same name + birth date, different IDs -> ask
    manager_gone: int = 42           # manager isn't in any file or the target -> 422 -> ask
    personal: tuple = ((57, "gmail"), (59, "yahoo"))
    transfer: int = 10               # v2: department change
    promotion: int = 20              # v2: new title
    phone_change: int = 33           # v2: new mobile number


def random_roles(rng: random.Random) -> Roles:
    in_payroll = list(range(7, 49))  # HRMS people who can also appear in payroll (heads 1-6 excluded)
    rng.shuffle(in_payroll)
    dup_a = rng.choice([49, 50])
    rest = [49 if dup_a == 50 else 50]
    take = lambda n: [in_payroll.pop() for _ in range(n)]  # noqa: E731
    r = Roles()
    r.target_dup, r.doj_conflict, r.manager_gone, r.wrong_tld = take(4)
    r.no_tld, r.comma_email = take(2)
    r.not_in_payroll = tuple(take(2))
    pool = in_payroll + rest
    rng.shuffle(pool)
    pick = lambda n: [pool.pop() for _ in range(n)]  # noqa: E731
    r.messy_name, = pick(1)
    r.upper, r.lower, r.email_case = tuple(pick(2)), tuple(pick(2)), tuple(pick(3))
    r.special_projects = tuple(pick(2))
    r.placeholders = tuple(zip(pick(3), ["N/A", "-", ""]))
    r.bad_phone, r.exact_dup = pick(2)
    r.same_name = tuple(pick(2))
    r.transfer, r.promotion, r.phone_change = pick(3)
    r.inactive = tuple(pick(4))
    joiners = list(range(53, 61))
    rng.shuffle(joiners)
    r.dup_pair = (dup_a, joiners.pop())
    r.personal = ((joiners.pop(), "gmail"), (joiners.pop(), "yahoo"))
    return r


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


def planted_names(r: Roles) -> dict[int, tuple[str, str]]:
    return {r.messy_name: ("Priya", "Nair"), r.no_tld: ("Rahul", "Verma"), r.comma_email: ("Neha", "Gupta"),
            r.target_dup: ("Ananya", "Iyer"), r.same_name[0]: ("Amit", "Kumar"), r.same_name[1]: ("Amit", "Kumar"),
            r.dup_pair[0]: ("Vikram", "Singh"), r.dup_pair[1]: ("Vikram", "Singh")}


def build_people(seed: int, r: Roles, count: int = 62) -> dict[int, dict]:
    rng = random.Random(seed)
    planted = planted_names(r)
    used = set(planted.values())
    people: dict[int, dict] = {}
    for i in range(1, count + 1):
        if i in planted:
            first, last = planted[i]
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
        suffix = "2" if i in (r.same_name[1], r.dup_pair[1]) else ""
        people[i] = dict(
            id=f"EMP{i:04d}", first=first, last=last,
            email=f"{first.lower()}.{last.lower()}{suffix}@{DOMAIN}",
            dept=dept, title=title, doj=doj, dob=dob, emp_type=emp_type, salary=salary,
            city=rng.choice(CITIES), phone=phone(rng),
            status="Inactive" if i in r.inactive else "Active",
        )

    # Ground-truth adjustments (separate RNG so the main stream above is unchanged)
    fix = random.Random(seed + 99)
    for i in r.special_projects:                       # "Special Projects" was a sub-team of Operations
        p = people[i]
        p["dept"] = "Operations"
        p["title"] = "Operations Intern" if p["emp_type"] == "Intern" else fix.choice(TITLES["Operations"][1])
    a, b = r.dup_pair                                  # the re-entry really is the same person
    for k in ("dept", "title", "dob", "emp_type", "city", "salary", "status"):
        people[b][k] = people[a][k]
    if 62 in people:                                   # v2's "Data Science" joiner belongs to Engineering
        p = people[62]
        p["dept"] = "Engineering"
        p["title"] = "Engineering Intern" if p["emp_type"] == "Intern" else fix.choice(TITLES["Engineering"][1])
    heads = {people[i]["dept"]: people[i]["email"] for i in range(1, 7)}
    for i, p in people.items():
        p["manager"] = "" if i <= 6 else heads[p["dept"]]
    people[r.manager_gone]["manager"] = f"suresh.menon@{DOMAIN}"  # left the company: in no file, not in target
    return people


def apply_v2(people: dict[int, dict], r: Roles) -> dict[int, dict]:
    people = {i: dict(p) for i, p in people.items()}
    t = people[r.transfer]
    new_dept = next(d for d in ["Sales", "Marketing", "Finance"] if d != t["dept"])
    t["dept"], t["title"] = new_dept, TITLES[new_dept][1][0]
    pr = people[r.promotion]  # a promotion must actually change the title (a "Senior" becomes a "Lead")
    pr["title"] = "Lead " + pr["title"][7:] if pr["title"].startswith("Senior ") else "Senior " + pr["title"]
    people[r.phone_change]["phone"] = "9812345670"
    return people


def payroll_ids(r: Roles) -> list[int]:
    excluded = set(r.not_in_payroll) | {r.no_tld, r.comma_email}
    return [i for i in range(1, 49) if i not in excluded] + [51, 52]


def onboarding_ids(version: int) -> list[int]:
    return list(range(52, 61)) + ([61, 62] if version == 2 else [])


def hrms_rows(people, r: Roles, h: dict, row_seed: int) -> list[dict]:
    rows = []
    placeholders = dict(r.placeholders)
    for i in range(1, 51):
        rng = random.Random(row_seed + 1000 + i)  # per-row RNG so v2 edits do not reshuffle other rows
        p = people[i]
        full = f"{p['first']} {p['last']}"
        if i == r.messy_name:
            full = f"  {p['first'].lower()}   {p['last'].lower()} "
        elif i in r.upper:
            full = full.upper()
        elif i in r.lower:
            full = full.lower()
        email = p["email"]
        if i in r.email_case:
            email = email.replace(p["first"].lower(), p["first"]).replace(DOMAIN, DOMAIN.upper()) + " "
        if i == r.no_tld:
            email = email.replace(".com", "")
        if i == r.comma_email:
            email = email.replace(".com", ",com")
        if i == r.wrong_tld:
            email = email.replace(".com", ".co")
        dept = rng.choice(DEPT_SPELLINGS[p["dept"]])
        if i in r.special_projects:
            dept = "Special Projects"
        ph = p["phone"]
        mobile = rng.choice([ph, f"+91 {ph[:5]} {ph[5:]}", f"0{ph[:5]}-{ph[5:]}", f"+91-{ph}"])
        if i in placeholders:
            mobile = placeholders[i]
        elif i == r.bad_phone:
            mobile = "98765"
        status = rng.choice(["Active", "Y", "active", "A"]) if p["status"] == "Active" else rng.choice(["Inactive", "N", "Resigned"])
        rows.append({
            h["id"]: p["id"], h["name"]: full, h["email"]: email, h["dept"]: dept, h["title"]: p["title"],
            h["doj"]: p["doj"].strftime("%d/%m/%Y"), h["dob"]: p["dob"].strftime("%d-%b-%Y"),
            h["mobile"]: mobile, h["status"]: status, h["location"]: rng.choice(CITY_SPELLINGS[p["city"]]),
            h["blood"]: rng.choice(BLOOD),
        })
        if i == r.exact_dup:
            rows.append(dict(rows[-1]))  # exact duplicate row
    return rows


def payroll_rows(people, r: Roles, h: dict, row_seed: int) -> list[dict]:
    rows = []
    for i in payroll_ids(r):
        rng = random.Random(row_seed + 2000 + i)
        p = people[i]
        code = [f"emp{i:04d}", f"EMP{i:04d}", f"EMP-{i:04d}"][i % 3]
        sal = p["salary"]
        ctc = [sal, inr(sal), f"{sal / 100000:g} LPA", f"₹ {inr(sal)}"][i % 4]
        start: object = dt.datetime.combine(p["doj"], dt.time())
        if i == r.doj_conflict:
            start = dt.datetime.combine(p["doj"] + dt.timedelta(days=10), dt.time())  # disagrees with HRMS
        elif i % 5 == 0:
            start = p["doj"].isoformat()
        et = {"Full-Time": ["FT", "Permanent", "Full Time", "Full-Time"], "Contract": ["Contractor", "Contract", "C2H"],
              "Part-Time": ["PT", "Part Time"], "Intern": ["Intern", "Trainee"]}[p["emp_type"]]
        rows.append({
            h["id"]: code, h["first"]: p["first"], h["last"]: p["last"], h["email"]: p["email"],
            h["salary"]: ctc, h["start"]: start, h["type"]: rng.choice(et), h["manager"]: p["manager"],
        })
    return rows


def personal_address(p: dict, kind: str) -> str:
    if kind == "gmail":
        return f"{p['first'].lower()}{p['last'].lower()}94@gmail.com"
    return f"{p['first'].lower()}.{p['last'][0].lower()}@yahoo.co.in"


def onboarding_rows(people, r: Roles, version: int, h: dict, row_seed: int) -> list[dict]:
    rows = []
    personal = dict(r.personal)
    for i in onboarding_ids(version):
        rng = random.Random(row_seed + 3000 + i)
        p = people[i]
        email = personal_address(p, personal[i]) if i in personal else p["email"]
        team = rng.choice([p["dept"], p["dept"], "People Ops" if p["dept"] == "Human Resources" else p["dept"]])
        if i == 62:
            team = "Data Science"  # new, unknown department value in the re-export
        rows.append({
            h["id"]: p["id"], h["name"]: f"{p['last']}, {p['first']}", h["email"]: email,
            h["contact"]: f"+91 {p['phone']}", h["team"]: team,
            h["joining"]: p["doj"].strftime("%m/%d/%Y"), h["dob"]: p["dob"].isoformat(),
            h["type"]: p["emp_type"], h["location"]: p["city"],
        })
    return rows


# ------------------------------------------------------------------------------------------------
# Ground truth
# ------------------------------------------------------------------------------------------------

def expected_record(i: int, p: dict, r: Roles, version: int) -> dict:
    in_h, in_p, in_o = i <= 50, i in payroll_ids(r), i in onboarding_ids(version)
    placeholders = dict(r.placeholders)
    personal = dict(r.personal)
    rec = {f: None for f in SCHEMA_FIELDS}
    rec.update(employee_id=p["id"], first_name=p["first"], last_name=p["last"], email=p["email"],
               department=p["dept"], date_of_joining=p["doj"].isoformat(),
               status=p["status"] if in_h else "Active")
    if i in personal:
        rec["personal_email"] = personal_address(p, personal[i])
    if (in_h and i not in placeholders) or in_o:
        rec["phone"] = "+91" + p["phone"]
    if in_h:
        rec["job_title"] = p["title"]
    if in_h or in_o:
        rec["date_of_birth"] = p["dob"].isoformat()
        rec["location"] = p["city"]
    if in_p or in_o:
        rec["employment_type"] = p["emp_type"]
    if in_p:
        rec["annual_salary"] = p["salary"]
        if p["manager"] and i != r.manager_gone:
            rec["manager_email"] = p["manager"]
    return rec


def ground_truth(name: str, seed: int, r: Roles, headers: dict, people: dict, version: int,
                 previous: dict | None = None) -> dict:
    """previous = the v1 people, needed to derive the expected delta for v2."""
    ids = sorted(set(range(1, 51)) | set(payroll_ids(r)) | set(onboarding_ids(version)))
    employees = {people[i]["id"]: expected_record(i, people[i], r, version) for i in ids}
    a, b = people[r.dup_pair[0]]["id"], people[r.dup_pair[1]]["id"]
    for k, v in employees[b].items():                  # merge: the primary keeps its values, gaps are filled
        if employees[a][k] is None and v is not None and k != "employee_id":
            employees[a][k] = v
    not_migrated = {b: f"duplicate of {a} (merged)",
                    people[r.target_dup]["id"]: "already in the target as EMP9001 (skipped)"}
    for k in not_migrated:
        employees.pop(k, None)

    mappings, date_formats = {}, {}
    for src, cols in COLUMNS.items():
        for logical, (target, _) in cols.items():
            key = f"{FILES[src]}|{headers[src][logical]}"
            mappings[key] = target
            if (src, logical) in DATE_FORMATS:
                date_formats[key] = DATE_FORMATS[(src, logical)]

    E = lambda i: people[i]["id"]  # noqa: E731
    onb = FILES["onboarding"]
    if version == 1:
        expected = [
            {"type": "mapping", "key": f"{onb}|{headers['onboarding']['contact']}", "answer": "phone",
             "why": "own mobile or emergency contact - headers and values can't tell"},
            {"type": "date_format", "key": f"{onb}|{headers['onboarding']['joining']}", "answer": "MDY",
             "why": "every date reads validly both ways; only one cross-file match"},
            {"type": "unknown_value", "key": "department|special projects", "answer": "Operations",
             "why": "not an allowed value, synonym or typo - a business decision"},
            {"type": "invalid_value", "key": f"{E(r.bad_phone)}|phone", "answer": "+91" + people[r.bad_phone]["phone"],
             "why": "5-digit phone, not a placeholder"},
            {"type": "conflict", "key": f"{E(r.doj_conflict)}|date_of_joining",
             "answer": people[r.doj_conflict]["doj"].isoformat(), "why": "two valid dates disagree"},
            {"type": "duplicate", "key": f"{a}|{b}", "answer": "merge", "why": "same name and birth date, different IDs"},
            {"type": "validation", "key": E(r.no_tld), "answer": {"email": people[r.no_tld]["email"]},
             "why": "email without TLD fails twice"},
            {"type": "validation", "key": "EMP0051", "answer": {"department": people[51]["dept"]},
             "why": "department missing from every source"},
            *[{"type": "validation", "key": E(i), "answer": {"email": people[i]["email"],
                                                            "personal_email": personal_address(people[i], kind)},
               "why": "personal address given as work email"} for i, kind in r.personal],
            {"type": "push_failure", "key": E(r.target_dup), "answer": "skip", "why": "email already used in target"},
            {"type": "push_failure", "key": E(r.manager_gone), "answer": {"manager_email": None},
             "why": "manager doesn't exist in the target"},
        ]
        auto_traps = [
            {"kind": "exact duplicate row merged", "keys": [E(r.exact_dup)]},
            {"kind": "same name, different birth date kept apart", "keys": [E(i) for i in r.same_name]},
            {"kind": "email comma typo repaired on 2nd attempt", "keys": [E(r.comma_email)]},
            {"kind": "invalid value loses to the valid one from another file", "keys": [E(r.wrong_tld)]},
            {"kind": "placeholder phones (N/A, -, blank) treated as empty", "keys": [E(i) for i, _ in r.placeholders]},
            {"kind": "messy name casing / whitespace", "keys": [E(i) for i in (r.messy_name, *r.upper, *r.lower)]},
            {"kind": "email casing / trailing spaces", "keys": [E(i) for i in r.email_case]},
            {"kind": "column with no target field left out", "keys": [f"{FILES['hrms']}|{headers['hrms']['blood']}"]},
        ]
        delta = None
    else:
        expected = [{"type": "unknown_value", "key": "department|data science", "answer": "Engineering",
                     "why": "a department value never seen before"}]
        auto_traps = []
        # Derived from the truth itself (not from the roles), so a no-op edit can't produce a wrong expectation.
        before = ground_truth(name, seed, r, headers, previous, 1)["employees"]
        delta = {"update": sorted(k for k, v in employees.items() if k in before and before[k] != v),
                 "create": sorted(k for k in employees if k not in before)}
    return {
        "dataset": name, "seed": seed, "version": version, "files": list(FILES.values()),
        "roles": dataclasses.asdict(r), "mappings": mappings, "date_formats": date_formats,
        "employees": employees, "not_migrated": not_migrated, "duplicates": {f"{a}|{b}": "merge"},
        "expected_escalations": expected, "auto_traps": auto_traps, "expected_delta": delta,
    }


def generate(name: str, seed: int, out: pathlib.Path, randomised: bool) -> list[pathlib.Path]:
    """Write <out>/v1 and <out>/v2 (files + ground_truth.json). Returns the two folders."""
    hrng = random.Random(seed * 7 + 1)
    roles = random_roles(random.Random(seed * 13 + 5)) if randomised else Roles()
    headers = {src: {lg: (rng_pick(hrng, pool) if randomised else pool[0]) for lg, (_, pool) in cols.items()}
               for src, cols in COLUMNS.items()}
    row_seed = seed * 100_000 if randomised else 0
    base = build_people(seed, roles)
    assert any(base[i]["doj"].day > 12 for i in range(1, 51)), "HRMS dates must prove day-first"
    folders = []
    for version in (1, 2):
        people = base if version == 1 else apply_v2(base, roles)
        folder = out / f"v{version}"
        folder.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(hrms_rows(people, roles, headers["hrms"], row_seed)).to_csv(folder / FILES["hrms"], index=False)
        pd.DataFrame(payroll_rows(people, roles, headers["payroll"], row_seed)).to_excel(folder / FILES["payroll"], index=False)
        pd.DataFrame(onboarding_rows(people, roles, version, headers["onboarding"], row_seed)).to_csv(
            folder / FILES["onboarding"], index=False)
        truth = ground_truth(name, seed, roles, headers, people, version, previous=base)
        (folder / "ground_truth.json").write_text(json.dumps(truth, indent=1, default=str), encoding="utf-8")
        folders.append(folder)
    return folders


def rng_pick(rng: random.Random, pool: list[str]) -> str:
    return rng.choice(pool)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heldout", type=int, default=0, help="also write N randomised held-out datasets")
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "var" / "heldout")
    args = ap.parse_args()
    for folder in generate("canonical", 7, OUT, randomised=False):
        print(f"wrote {folder}")
    for n in range(1, args.heldout + 1):
        for folder in generate(f"heldout-{n}", 100 + n, args.out / f"heldout-{n}", randomised=True):
            print(f"wrote {folder}")


if __name__ == "__main__":
    main()
