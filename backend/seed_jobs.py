from app import db, Job, app

sample_jobs = [
    {"title": "Agro-Processing Internship", "location": "Freetown", "duration": "3 months"},
    {"title": "ICT Training Assistant", "location": "Bo", "duration": "Full-time"},
    {"title": "Solar Panel Installer", "location": "Kenema", "duration": "6 months"},
    {"title": "Mobile Money Agent Trainer", "location": "Makeni", "duration": "Part-time"},
    {"title": "Fisheries Data Clerk", "location": "Bonthe", "duration": "Contract"},
]

with app.app_context():
    for job in sample_jobs:
        new_job = Job(title=job["title"], location=job["location"], duration=job["duration"])
        db.session.add(new_job)
    db.session.commit()

print("✅ Seeded sample jobs into database!")
