import json
from pathlib import Path


def load_settings():
    settings_path = Path(__file__).with_name("settings.json")
    return json.loads(settings_path.read_text())
