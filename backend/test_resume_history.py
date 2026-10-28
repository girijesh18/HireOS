"""parse_history: work/education from the master resume's structure. Run: python test_resume_history.py"""
from resume_profile import parse_history

MD = """# Name
## PROFESSIONAL EXPERIENCE
### Acme Corp, USA || Sep 2022 – Present
**Lead Data Scientist** (Jan 2024 – Present)
Leads the platform.
- **Scale:** Served 30,000+ queries a day.
**Senior Data Scientist** (Sep 2022 – Jan 2024)
### Beta Ltd (India) || Jun 2018 – Apr 2022
**ML Engineer**
- Built models.
## PUBLICATIONS & EDUCATION
### Master of Computer Science || May 2021 – May 2023
*Lakehead University, Thunder Bay, Canada*
"""

h = parse_history(MD)
assert [(w["company"], w["location"], w["title"], w["start"], w["end"]) for w in h["work"]] == [
    ("Acme Corp", "USA", "Lead Data Scientist", "Jan 2024", "Present"),
    ("Acme Corp", "USA", "Senior Data Scientist", "Sep 2022", "Jan 2024"),
    ("Beta Ltd", "India", "ML Engineer", "Jun 2018", "Apr 2022")], h["work"]
assert h["work"][0]["description"] == "Leads the platform.\n- Scale: Served 30,000+ queries a day."
assert h["work"][1]["description"] == ""
assert h["education"] == [{"degree": "Master of Computer Science", "school": "Lakehead University",
                           "location": "Thunder Bay, Canada", "start": "May 2021", "end": "May 2023"}]
print("ok")
