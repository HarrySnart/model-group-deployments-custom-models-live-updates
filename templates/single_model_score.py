import os
import sys
from functools import lru_cache

try:
    import cloudpickle as serializer
except ImportError:  # pragma: no cover
    import pickle as serializer

MODEL_FILE_NAME = "model.pickle"


@lru_cache(maxsize=1)
def load_model(model_file_name=MODEL_FILE_NAME):
    model_dir = os.path.dirname(os.path.realpath(__file__))
    if model_dir not in sys.path:
        sys.path.insert(0, model_dir)

    model_path = os.path.join(model_dir, model_file_name)
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model file not found: {model_path}")

    with open(model_path, "rb") as file_handle:
        return serializer.load(file_handle)


def predict(data, model=None):
    if model is None:
        model = load_model()
    return {"prediction": model.predict(data)}
