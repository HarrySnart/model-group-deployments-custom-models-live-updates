from .transforms import normalize_for_square_root
from .validators import ensure_supported_value


def compute_abs_square_root(number: float, multiplier: float = 1.0) -> float:
    checked = ensure_supported_value(number)
    normalized = normalize_for_square_root(checked)
    return (normalized ** 0.5) * multiplier
