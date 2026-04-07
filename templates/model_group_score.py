import gc
import logging
import os
import sys

try:
    import cloudpickle as serializer
except ImportError:  # pragma: no cover
    import pickle as serializer

logging.basicConfig(format="%(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("model-group-score")

_MODEL_CACHE = {}


def _ensure_model_folder_on_path(model_folder):
    if model_folder not in sys.path:
        sys.path.insert(0, model_folder)


def load_model(model_folder):
    if model_folder in _MODEL_CACHE:
        return _MODEL_CACHE[model_folder]

    model_path = os.path.join(model_folder, "model.pickle")
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model path does not exist: {model_path}")

    _ensure_model_folder_on_path(model_folder)
    with open(model_path, "rb") as file_handle:
        model = serializer.load(file_handle)

    _MODEL_CACHE[model_folder] = model
    logger.info("Loaded model from %s", model_folder)
    return model


def unload_model(model_folder):
    logger.info("Unloading model from %s", model_folder)
    _MODEL_CACHE.pop(model_folder, None)
    gc.collect()


def predict(data, model):
    return {"prediction": model.predict(data)}
