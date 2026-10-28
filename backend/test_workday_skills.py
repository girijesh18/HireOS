"""Workday helpers: skills from the resume, closest option. Run: python test_workday_skills.py"""
from workday import closest, month_year, resume_skills

MD = """# Name
## Summary
- **Leader:** not a skills section
## Technical Expertise
- **ML & Stats:** classification (XGBoost, CatBoost), Pandas; Kubernetes and OpenShift (familiar)
- **Python:** FastAPI, Python
## Education
- Lakehead
"""


def test_resume_skills():
    got = resume_skills(MD, "We use pandas and FastAPI.")
    assert got == ["Pandas", "FastAPI", "ML", "Stats", "classification", "XGBoost", "CatBoost", "Python"], got
    assert resume_skills("", "x") == []


def test_closest():
    opts = ["Job Boards", "Other", "Social Media", "TD Careers Website", "TD Employee"]
    assert closest(opts, "Company Website") == "TD Careers Website"
    assert closest(opts, "Company") is None                              # no meaningful word shared
    assert closest(["Yes, full time", "Yes, part time"], "Yes") is None  # tie: never guess


def test_month_year():
    assert month_year("Jun 2018") == month_year("06/2018") == month_year("6/2018") == (6, 2018)
    assert month_year("08/2014") != month_year("Jun 2018")
    assert month_year("") == (None, None)


if __name__ == "__main__":
    test_resume_skills()
    test_closest()
    test_month_year()
    print("ok")
