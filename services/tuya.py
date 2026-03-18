from tuya_connector import TuyaOpenAPI
from config import TUYA_ENDPOINT, TUYA_ACCESS_ID, TUYA_ACCESS_KEY, DEVICE_IDS

openapi = TuyaOpenAPI(TUYA_ENDPOINT, TUYA_ACCESS_ID, TUYA_ACCESS_KEY)


def connect():
    openapi.connect()


def request(func, *args, **kwargs):
    response = func(*args, **kwargs)
    if isinstance(response, dict) and response.get("code") == 1010:
        openapi.connect()
        response = func(*args, **kwargs)
    return response


def get_lamp_status(lamp_type: str) -> dict:
    device_id = DEVICE_IDS.get(f"{lamp_type}_lamp", "")
    if not device_id:
        return {"online": None, "switch": None}
    try:
        resp = request(openapi.get, f"/v1.0/devices/{device_id}/status")
        result = resp.get("result", [])
        status_list = result.get("status", []) if isinstance(result, dict) else result or []
        switch = next(
            (i.get("value") for i in status_list if i.get("code") in ("switch_1", "switch")),
            None,
        )
        return {"online": True, "switch": switch}
    except Exception:
        return {"online": None, "switch": None}


def get_sensor(sensor_type: str, code: str):
    device_id = DEVICE_IDS.get(sensor_type, "")
    if not device_id:
        return None
    try:
        resp = request(openapi.get, f"/v1.0/devices/{device_id}/status")
        if resp.get("success"):
            return next((i["value"] for i in resp["result"] if i["code"] == code), None)
    except Exception:
        pass
    return None


def switch_lamp(lamp_type: str, on: bool) -> bool:
    device_id = DEVICE_IDS.get(f"{lamp_type}_lamp", "")
    if not device_id:
        return False
    resp = request(
        openapi.post,
        f"/v1.0/devices/{device_id}/commands",
        {"commands": [{"code": "switch_1", "value": on}]},
    )
    return resp.get("success", False)


def get_device_status(device_id: str) -> dict:
    return request(openapi.get, f"/v1.0/devices/{device_id}/status")


def get_device_functions(device_id: str) -> dict:
    return request(openapi.get, f"/v1.0/devices/{device_id}/functions")


def is_connected() -> bool:
    return bool(openapi.token_info and openapi.token_info.access_token)
