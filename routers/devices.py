from fastapi import APIRouter, HTTPException, Depends

from services import tuya
from database import log_lamp_event
from routers.auth import get_current_user

router = APIRouter()


@router.get("/temperature")
async def get_temperature(_user: str = Depends(get_current_user)):
    temp = tuya.get_sensor("thermometer", "va_temperature")
    if temp is not None:
        return {"temperature": temp}
    raise HTTPException(status_code=400, detail="Failed to get temperature")


@router.get("/humidity")
async def get_humidity(_user: str = Depends(get_current_user)):
    hum = tuya.get_sensor("humidifier", "va_humidity")
    if hum is not None:
        return {"humidity": hum}
    raise HTTPException(status_code=400, detail="Failed to get humidity")


@router.post("/lamp/{lamp_type}/on")
async def turn_lamp_on(lamp_type: str, _user: str = Depends(get_current_user)):
    if lamp_type not in ("uv", "heat"):
        raise HTTPException(status_code=400, detail="Invalid lamp type")
    if tuya.switch_lamp(lamp_type, True):
        await log_lamp_event(lamp_type, "on", "web")
        return {"message": f"{lamp_type} lamp turned on"}
    raise HTTPException(status_code=400, detail="Failed to turn on lamp")


@router.post("/lamp/{lamp_type}/off")
async def turn_lamp_off(lamp_type: str, _user: str = Depends(get_current_user)):
    if lamp_type not in ("uv", "heat"):
        raise HTTPException(status_code=400, detail="Invalid lamp type")
    if tuya.switch_lamp(lamp_type, False):
        await log_lamp_event(lamp_type, "off", "web")
        return {"message": f"{lamp_type} lamp turned off"}
    raise HTTPException(status_code=400, detail="Failed to turn off lamp")


@router.get("/device/{device_id}/functions")
async def get_device_functions(device_id: str, _user: str = Depends(get_current_user)):
    return tuya.get_device_functions(device_id)


@router.get("/device/{device_id}/status")
async def get_device_status(device_id: str, _user: str = Depends(get_current_user)):
    return tuya.get_device_status(device_id)
