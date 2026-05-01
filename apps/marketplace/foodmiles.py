import math
import requests


def _get_lat_lng(postcode: str):
    postcode = postcode.replace(" ", "").upper()
    try:
        response = requests.get(
            f"https://api.postcodes.io/postcodes/{postcode}",
            timeout=5
        )
        data = response.json()
        if data.get("status") == 200:
            result = data["result"]
            return result["latitude"], result["longitude"]
    except Exception:
        pass
    return None


def _haversine_miles(lat1, lng1, lat2, lng2):
    R = 3958.8
    lat1, lng1, lat2, lng2 = map(math.radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return round(R * c, 1)


def calculate_food_miles(customer_postcode: str, producer_postcode: str):
    if not customer_postcode or not producer_postcode:
        return None
    customer_coords = _get_lat_lng(customer_postcode)
    if not customer_coords:
        return None
    producer_coords = _get_lat_lng(producer_postcode)
    if not producer_coords:
        return None
    return _haversine_miles(*customer_coords, *producer_coords)