"""API gợi ý cơ sở y tế ở Hà Nội (docs/FACILITIES.md). Cùng logic với tool find_healthcare_facility.

    GET /api/facilities?specialty=tim_mach&emergency=false&for_child=false
    GET /api/facilities/specialties   mã chuyên khoa → tên, và nhóm bệnh → chuyên khoa
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models import User
from app.schemas import Facility
from app.security import get_current_user
from app.services.facility_service import (
    DISEASE_GROUP_SPECIALTY,
    SPECIALTIES,
    facility_card,
    search_facilities,
)

router = APIRouter(prefix="/api/facilities", tags=["facilities"])


@router.get("", response_model=list[Facility])
def list_facilities(
    specialty: str = Query(..., description="Mã chuyên khoa, xem /api/facilities/specialties"),
    emergency: bool = False,
    for_child: bool = False,
    limit: int = Query(5, ge=1, le=30),
    _: User = Depends(get_current_user),
):
    if specialty not in SPECIALTIES:
        raise HTTPException(status_code=422, detail=f"Chuyên khoa không hợp lệ: {specialty}")
    found = search_facilities(specialty, emergency=emergency, for_child=for_child, limit=limit)
    return [facility_card(item) for item in found]


@router.get("/specialties")
def list_specialties(_: User = Depends(get_current_user)) -> dict:
    return {"specialties": SPECIALTIES, "disease_groups": DISEASE_GROUP_SPECIALTY}
