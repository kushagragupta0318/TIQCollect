import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import SessionLocal
from app.models.customer import Customer
from app.models.case import Case
from app.models.loan import Loan

def main():
    db = SessionLocal()
    customers = db.query(Customer).filter(Customer.full_name.ilike("%Hritik Swaminathan%")).all()
    print(f"Total Customer rows with name 'Hritik Swaminathan': {len(customers)}")
    for cust in customers:
        print(f"\nCustomer ID: {cust.id}, Ref: {cust.customer_ref}, Phone: {cust.phone_primary}, PAN: {cust.pan_masked}")
        cases = db.query(Case).filter(Case.customer_id == cust.id).all()
        print(f"  Cases ({len(cases)}):")
        for c in cases:
            loan = db.query(Loan).filter(Loan.id == c.loan_id).first()
            print(f"    Case: {c.case_number}, Loan: {loan.loan_account_number if loan else 'None'} ({loan.loan_type if loan else ''}), Balance: ₹{loan.total_outstanding if loan else 0:,.2f}, Target: ₹{c.target_amount:,.2f}, Status: {c.status}")

if __name__ == "__main__":
    main()
