import os

from .http import request

LOOKUP_URL = "https://ipinfo.io/json"


def lookup_network_location(env=os.environ):
    """Where this device's internet connection appears to be, from its public IP
    address. The device asks by itself because the server may only see a private
    address. Approximate: usually the city of the internet provider's equipment.
    Returns None when turned off, and the last known place stays."""
    if env.get("ACU_NETWORK_LOCATION") == "0":
        return None
    status, data = request(env.get("ACU_GEO_URL") or LOOKUP_URL, timeout=8)
    if status != 200:
        raise RuntimeError(f"location lookup answered HTTP {status}")
    return {
        "ip": data.get("ip"),
        "city": data.get("city"),
        "region": data.get("region"),
        "countryCode": data.get("country"),
        "org": data.get("org"),
    }
