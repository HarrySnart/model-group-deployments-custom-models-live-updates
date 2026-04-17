from .operations import compute_square_root
from .parsing import extract_number
from .resources import load_settings


class SqrtModel:
    def predict(self, data):
        number = extract_number(data)
        settings = load_settings()
        return compute_square_root(number, settings.get("multiplier", 1.0))
