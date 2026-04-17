from .operations import compute_square
from .parsing import extract_number
from .resources import load_settings


class SquareModel:
    def predict(self, data):
        number = extract_number(data)
        settings = load_settings()
        return compute_square(number, settings.get("offset", 0.0))
