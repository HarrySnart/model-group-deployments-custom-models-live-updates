import json


def extract_number(data):
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise TypeError("Input payload must be a dict or JSON string containing a 'number' field.")
    if "number" not in data:
        raise KeyError("Input payload must contain a 'number' field.")
    return float(data["number"])
