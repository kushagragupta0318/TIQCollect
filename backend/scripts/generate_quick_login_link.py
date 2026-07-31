# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-15 — Token minted here is now 15-minute, single-use (was 90-day,
#   unlimited-reuse — see core/security.py/changelog.md). Do NOT paste the
#   printed URL into script.js's managerLinks map anymore: it stops working
#   the moment anyone clicks it once, or after 15 minutes, whichever comes
#   first. Run this fresh, right before handing the link to whoever needs it.
# ───────────────────────────────────────────────────────────────────────────
# collection_dashboard: one-off CLI to mint a quick-login URL for one manager/agency.
# Not an API endpoint — run manually, hand the printed URL directly to the person who
# needs it. Single-use, 15-minute expiry — generate a fresh one each time, don't store it.
# Usage: python generate_quick_login_link.py <manager_email> <agency_code>
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import SessionLocal
from app.core.security import create_quick_login_token
from app.models.user import User

FRONTEND_ORIGIN = os.environ.get("QUICK_LOGIN_FRONTEND_ORIGIN", "http://localhost:5473")


def main():
    if len(sys.argv) != 3:
        print("Usage: python generate_quick_login_link.py <manager_email> <agency_code>")
        sys.exit(1)

    email, agency_code = sys.argv[1], sys.argv[2]
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"No user found with email {email}")
            sys.exit(1)

        token = create_quick_login_token(user.id, agency_code)
        print(f"{FRONTEND_ORIGIN}/quick-login?token={token}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
