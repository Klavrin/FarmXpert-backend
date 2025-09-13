# app/services/farm_profile.py
from sqlalchemy import text
from datetime import date

def load_farm_profile(conn, user_id: int) -> dict:
    # users -> get business_id
    row = conn.execute(text("""
        select userId, firstName, lastName, businessId, phone, email, verified, isOwner
        from users where userId = :uid
    """), {"uid": user_id}).mappings().first()
    if not row:
        raise ValueError("User not found")

    business_id = row["businessId"]

    animals = conn.execute(text("""
        select a.id, a.species, a.sex, a.brithDate, a.cattleId
        from animal a
        join cattle c on c.id = a.cattleId
        where c.businessId = :bid
        order by a.id
    """), {"bid": business_id}).mappings().all()

    cattle = conn.execute(text("""
        select id, type, animals, businessId, amount
        from cattle where businessId = :bid
        order by id
    """), {"bid": business_id}).mappings().all()

    fields = conn.execute(text("""
        select id, cropType, coords, size, businessId, soilType, fertiliser, herbicide
        from field where businessId = :bid
        order by id
    """), {"bid": business_id}).mappings().all()

    vehicles = conn.execute(text("""
        select v.id, v.vehicleType, v.fabricationDate, v.brand, vg.vehicleType as groupType
        from vehicle v
        join vehicleGroup vg on vg.id = v.vehicleGroupId
        where vg.businessId = :bid
        order by v.id
    """), {"bid": business_id}).mappings().all()

    finance = conn.execute(text("""
        select id, updatedAt, yearlyIncome, yearlyExpenses
        from finance
        where businessId = :bid
        order by updatedAt desc
        limit 1
    """), {"bid": business_id}).mappings().first()

    return {
        "user": {
            "id": row["userId"],
            "businessId": business_id,
            "firstName": row["firstName"],
            "lastName": row["lastName"],
            "phone": row["phone"],
            "email": row["email"],
            "verified": bool(row["verified"]),
            "isOwner": bool(row["isOwner"]),
        },
        "as_of": date.today().isoformat(),
        "animals": [dict(x) for x in animals],
        "cattle":  [dict(x) for x in cattle],
        "fields":  [dict(x) for x in fields],
        "vehicles":[dict(x) for x in vehicles],
        "finance": dict(finance) if finance else None,
    }
