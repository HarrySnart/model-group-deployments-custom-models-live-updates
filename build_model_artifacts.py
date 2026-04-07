from __future__ import annotations

import importlib
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from ads.model import GenericModel

try:
    import cloudpickle as serializer
except ImportError:  # pragma: no cover
    import pickle as serializer

REPO_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = REPO_ROOT / "templates"
SOURCE_MODELS_DIR = REPO_ROOT / "source_models"
BUILD_DIR = REPO_ROOT / "build"
MODEL_GROUP_DIR = BUILD_DIR / "model_group_live"
MODEL_GROUP_ZIP = BUILD_DIR / "model_group_live.zip"
INFERENCE_CONDA_ENV = "generalml_p311_cpu_x86_64_v1"
INFERENCE_PYTHON_VERSION = "3.11"


@dataclass(frozen=True)
class ModelSpec:
    artifact_name: str
    source_package: str
    class_name: str
    display_name: str
    inference_key: str
    version_label: str
    description: str


@dataclass
class PreparedModelArtifact:
    spec: ModelSpec
    artifact_dir: Path
    generic_model: GenericModel
    model_path: Path


SERVICE_MODEL_SPECS = {
    "square_v1": ModelSpec(
        artifact_name="model1_live",
        source_package="square_service_v1_bundle",
        class_name="SquareModel",
        display_name="Business Model 1",
        inference_key="square",
        version_label="Version 1",
        description="Squares the input number using bundled helper modules and packaged settings.",
    ),
    "sqrt_v1": ModelSpec(
        artifact_name="model2_live",
        source_package="sqrt_service_v1_bundle",
        class_name="SqrtModel",
        display_name="Business Model 2",
        inference_key="square-root",
        version_label="Version 1",
        description="Returns the square root using bundled helper modules and packaged settings.",
    ),
    "abs_sqrt_v2": ModelSpec(
        artifact_name="model3_live",
        source_package="abs_sqrt_service_v2_bundle",
        class_name="AbsSqrtModel",
        display_name="Business Model 2",
        inference_key="square-root",
        version_label="Version 2",
        description="Returns the square root of the absolute value using multiple bundled helper modules.",
    ),
}


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def reset_build_dir() -> None:
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)


def _prepare_generic_model_artifact(artifact_dir: Path) -> GenericModel:
    generic_model = GenericModel(artifact_dir=str(artifact_dir))
    generic_model.prepare(
        inference_conda_env=INFERENCE_CONDA_ENV,
        inference_python_version=INFERENCE_PYTHON_VERSION,
        score_py_uri=str(TEMPLATES_DIR / "single_model_score.py"),
        force_overwrite=True,
    )
    _copy_file(TEMPLATES_DIR / "runtime.yaml", artifact_dir / "runtime.yaml")
    _copy_file(TEMPLATES_DIR / ".model-ignore", artifact_dir / ".model-ignore")
    return generic_model


def _serialize_model_instance(artifact_dir: Path, package_name: str, class_name: str) -> Path:
    importlib.invalidate_caches()
    sys.path.insert(0, str(artifact_dir))
    try:
        module = importlib.import_module(f"{package_name}.model")
        model_class = getattr(module, class_name)
        model_instance = model_class()
        target_path = artifact_dir / "model.pickle"
        with target_path.open("wb") as file_handle:
            serializer.dump(model_instance, file_handle)
        return target_path
    finally:
        sys.path.pop(0)


def prepare_model_artifact(spec: ModelSpec) -> PreparedModelArtifact:
    artifact_dir = BUILD_DIR / spec.artifact_name
    artifact_dir.mkdir(parents=True, exist_ok=True)
    generic_model = _prepare_generic_model_artifact(artifact_dir)
    _copy_tree(SOURCE_MODELS_DIR / spec.source_package, artifact_dir / spec.source_package)
    model_path = _serialize_model_instance(artifact_dir, spec.source_package, spec.class_name)
    generic_model.reload_runtime_info()
    return PreparedModelArtifact(
        spec=spec,
        artifact_dir=artifact_dir,
        generic_model=generic_model,
        model_path=model_path,
    )


def prepare_model_group_artifact() -> Dict[str, Path]:
    MODEL_GROUP_DIR.mkdir(parents=True, exist_ok=True)
    _copy_file(TEMPLATES_DIR / "runtime.yaml", MODEL_GROUP_DIR / "runtime.yaml")
    _copy_file(TEMPLATES_DIR / "model_group_score.py", MODEL_GROUP_DIR / "score.py")
    archive_base = MODEL_GROUP_ZIP.with_suffix("")
    archive_path = shutil.make_archive(str(archive_base), "zip", root_dir=BUILD_DIR, base_dir="model_group_live")
    return {
        "artifact_dir": MODEL_GROUP_DIR,
        "zip_path": Path(archive_path),
    }


def build_all_artifacts(reset: bool = True) -> Dict[str, object]:
    if reset:
        reset_build_dir()

    prepared_models = {
        key: prepare_model_artifact(spec)
        for key, spec in SERVICE_MODEL_SPECS.items()
    }
    model_group = prepare_model_group_artifact()

    manifest = {
        "build_dir": str(BUILD_DIR),
        "model_group_artifact_dir": str(model_group["artifact_dir"]),
        "model_group_zip": str(model_group["zip_path"]),
        "models": {
            key: {
                "artifact_dir": str(prepared.artifact_dir),
                "model_pickle": str(prepared.model_path),
                "source_package": prepared.spec.source_package,
                "inference_key": prepared.spec.inference_key,
                "display_name": prepared.spec.display_name,
                "version_label": prepared.spec.version_label,
                "description": prepared.spec.description,
            }
            for key, prepared in prepared_models.items()
        },
    }

    (BUILD_DIR / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return {
        "prepared_models": prepared_models,
        "model_group": model_group,
        "manifest": manifest,
    }


def main() -> None:
    result = build_all_artifacts(reset=True)
    print(json.dumps(result["manifest"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
