import os
import sys

from app import db, Job, app

# Guard against accidentally seeding a real database: this inserts sample
# rows with no dedupe key other than title, so running it twice -- or
# against production by mistake -- creates duplicate listings with no way
# to tell them apart from real employer postings later.
if os.getenv("FLASK_ENV", "development") == "production":
    print("Refusing to run: FLASK_ENV=production. This script is for local/dev seeding only.")
    sys.exit(1)

sample_jobs = [
    {"title": "Agro-Processing Internship", "location": "Freetown", "duration": "3 months"},
    {"title": "ICT Training Assistant", "location": "Bo", "duration": "Full-time"},
    {"title": "Solar Panel Installer", "location": "Kenema", "duration": "6 months"},
    {"title": "Mobile Money Agent Trainer", "location": "Makeni", "duration": "Part-time"},
    {"title": "Fisheries Data Clerk", "location": "Bonthe", "duration": "Contract"},
]

with app.app_context():
    added = 0
    for job in sample_jobs:
        if Job.query.filter_by(title=job["title"], location=job["location"]).first():
            continue
        new_job = Job(title=job["title"], location=job["location"], duration=job["duration"])
        db.session.add(new_job)
        added += 1
    db.session.commit()

print(f"✅ Seeded {added} new sample job(s) into database ({len(sample_jobs) - added} already present).")
