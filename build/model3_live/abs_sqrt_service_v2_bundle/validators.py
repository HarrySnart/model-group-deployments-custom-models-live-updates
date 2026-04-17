def ensure_supported_value(number: float, minimum: float = -1000000.0, maximum: float = 1000000.0) -> float:
    if number < minimum or number > maximum:
        raise ValueError(f"Number {number} is outside the supported range [{minimum}, {maximum}].")
    return number
