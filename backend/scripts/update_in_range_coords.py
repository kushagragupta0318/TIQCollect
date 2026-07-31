"""
Move 6 pending demo customers to within ~50-80 m of agent002's location
so the geo-fence passes during record-visit testing.

Agent002 home: (28.455151, 77.071623)
Run: python -m scripts.update_in_range_coords
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.database import SessionLocal
from app.models.customer import Customer

# customer_ref  -> (new_lat, new_lon, approx_distance_m)
UPDATES = [
    ("DEMO0002", 28.455551, 77.071923, 53),   # Sunita Devi Agarwal
    ("DEMO0006", 28.454651, 77.072023, 67),   # Anita Kapoor Malhotra
    ("DEMO0007", 28.455751, 77.071323, 72),   # Suresh Chand Bansal
    ("DEMO0010", 28.454851, 77.071023, 67),   # Vikas Kumar Pandey
    ("DEMO0011", 28.455651, 77.071123, 74),   # Meena Devi Tiwari
    ("DEMO0012", 28.454551, 77.071423, 69),   # Arun Prasad Singh
]

db = SessionLocal()
try:
    updated = 0
    for ref, lat, lon, dist in UPDATES:
        rows = db.query(Customer).filter(Customer.customer_ref == ref).all()
        if not rows:
            print(f"  SKIP {ref} — not found in DB")
            continue
        for c in rows:
            c.latitude = lat
            c.longitude = lon
            print(f"  OK   {ref} ({c.full_name}) → ({lat}, {lon})  ~{dist}m from agent")
            updated += 1
    db.commit()
    print(f"\n{updated} customer(s) updated. Restart backend to pick up changes.")
finally:
    db.close()
