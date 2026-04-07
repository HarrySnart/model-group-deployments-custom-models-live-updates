from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

import ads
import oci
import requests
from ads.model import ModelVersionSet
from oci.signer import Signer

from build_model_artifacts import (
    BUILD_DIR,
    PreparedModelArtifact,
    SERVICE_MODEL_SPECS,
    build_all_artifacts,
)
# UPDATE HERE WITH OCIDs
COMPARTMENT_OCID = "ocid1.compartment...."
PROJECT_OCID = "ocid1.datascienceproject...."
LOG_GROUP_OCID = "ocid1.loggroup...."
LOG_OCID = "ocid1.log...." # update this for separate PREDICT and ACCESS logs
DEFAULT_PROFILE = "DEFAULT"
POLL_INTERVAL_SECONDS = 30
POLL_TIMEOUT_SECONDS = 60 * 45

logger = logging.getLogger("deploy_to_oci")


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def timestamp_suffix() -> str:
    return datetime.utcnow().strftime("%Y%m%d%H%M%S")


def configure_ads_auth(profile: str) -> dict:
    ads.set_auth(auth="api_key", profile=profile)
    config = oci.config.from_file(profile_name=profile)
    config["key_file"] = os.path.expanduser(config["key_file"])
    oci.config.validate_config(config)
    return config


def create_signer(config: dict) -> Signer:
    return Signer(
        tenancy=config["tenancy"],
        user=config["user"],
        fingerprint=config["fingerprint"],
        private_key_file_location=config["key_file"],
        pass_phrase=config.get("pass_phrase"),
    )


def wait_for_state(
    label: str,
    getter: Callable[[], Any],
    success_states: Iterable[str],
    failure_states: Optional[Iterable[str]] = None,
    timeout_seconds: int = POLL_TIMEOUT_SECONDS,
    interval_seconds: int = POLL_INTERVAL_SECONDS,
):
    success = {state.upper() for state in success_states}
    failure = {state.upper() for state in (failure_states or [])}
    deadline = time.time() + timeout_seconds

    while True:
        resource = getter()
        state = (getattr(resource, "lifecycle_state", None) or "UNKNOWN").upper()
        logger.info("%s lifecycle_state=%s", label, state)
        if state in success:
            return resource
        if state in failure:
            raise RuntimeError(f"{label} entered failure state {state}")
        if time.time() >= deadline:
            raise TimeoutError(f"Timed out waiting for {label} to reach one of {sorted(success)}")
        time.sleep(interval_seconds)


def create_model_version_set(name: str, description: str) -> ModelVersionSet:
    mvs = ModelVersionSet(name=name, description=description)
    mvs.with_compartment_id(COMPARTMENT_OCID).with_project_id(PROJECT_OCID).create()
    logger.info("Created model version set %s (%s)", name, mvs.id)
    return mvs


def save_model(prepared: PreparedModelArtifact, model_version_set: ModelVersionSet) -> str:
    model_id = prepared.generic_model.save(
        compartment_id=COMPARTMENT_OCID,
        project_id=PROJECT_OCID,
        display_name=prepared.spec.display_name,
        model_version_set=model_version_set,
        version_label=prepared.spec.version_label,
        reload=False,
        ignore_introspection=True,
    )
    logger.info("Saved %s as model %s", prepared.spec.display_name, model_id)
    return model_id


def create_model_group(
    client: oci.data_science.DataScienceClient,
    display_name: str,
    description: str,
    members: list[dict],
) -> str:
    response = client.create_model_group(
        create_base_model_group_details=oci.data_science.models.CreateModelGroupDetails(
            create_type="CREATE",
            compartment_id=COMPARTMENT_OCID,
            project_id=PROJECT_OCID,
            model_group_details=oci.data_science.models.HomogeneousModelGroupDetails(type="HOMOGENEOUS"),
            member_model_entries=oci.data_science.models.MemberModelEntries(
                member_model_details=[
                    oci.data_science.models.MemberModelDetails(
                        model_id=member["model_id"],
                        inference_key=member["inference_key"],
                    )
                    for member in members
                ]
            ),
            display_name=display_name,
            description=description,
        )
    )
    model_group_id = response.data.id
    wait_for_state(
        label=f"model group {display_name}",
        getter=lambda: client.get_model_group(model_group_id).data,
        success_states={"ACTIVE"},
        failure_states={"FAILED", "DELETED"},
    )
    return model_group_id


def upload_model_group_artifact(
    client: oci.data_science.DataScienceClient,
    model_group_id: str,
    zip_path: Path,
) -> None:
    content_disposition = f"attachment;filename={zip_path.name}"
    with zip_path.open("rb") as artifact_file:
        client.create_model_group_artifact(
            model_group_id=model_group_id,
            model_group_artifact=artifact_file,
            content_disposition=content_disposition,
        )
    wait_for_state(
        label=f"model group artifact {model_group_id}",
        getter=lambda: client.get_model_group(model_group_id).data,
        success_states={"ACTIVE"},
        failure_states={"FAILED", "DELETED"},
    )


def create_model_group_version_history(
    client: oci.data_science.DataScienceClient,
    display_name: str,
    description: str,
    latest_model_group_id: str,
) -> str:
    response = client.create_model_group_version_history(
        oci.data_science.models.CreateModelGroupVersionHistoryDetails(
            compartment_id=COMPARTMENT_OCID,
            display_name=display_name,
            description=description,
            project_id=PROJECT_OCID,
            latest_model_group_id=latest_model_group_id,
        )
    )
    history_id = response.data.id
    logger.info("Created model group version history %s", history_id)
    return history_id


def update_model_group_version_history(
    client: oci.data_science.DataScienceClient,
    model_group_version_history_id: str,
    display_name: str,
    description: str,
    latest_model_group_id: str,
) -> None:
    client.update_model_group_version_history(
        model_group_version_history_id=model_group_version_history_id,
        update_model_group_version_history_details=oci.data_science.models.UpdateModelGroupVersionHistoryDetails(
            display_name=display_name,
            description=description,
            latest_model_group_id=latest_model_group_id,
        ),
    )
    logger.info("Updated model group version history %s", model_group_version_history_id)


def create_model_deployment(
    client: oci.data_science.DataScienceClient,
    display_name: str,
    description: str,
    model_group_id: str,
) -> oci.data_science.models.ModelDeployment:
    instance_shape_config_details = oci.data_science.models.ModelDeploymentInstanceShapeConfigDetails(
        memory_in_gbs=16,
        ocpus=1,
    )
    instance_configuration = oci.data_science.models.InstanceConfiguration(
        instance_shape_name="VM.Standard.E4.Flex",
        model_deployment_instance_shape_config_details=instance_shape_config_details,
    )
    scaling_policy = oci.data_science.models.FixedSizeScalingPolicy(
        policy_type="FIXED_SIZE",
        instance_count=1,
    )
    infrastructure_config_details = oci.data_science.models.InstancePoolInfrastructureConfigurationDetails(
        infrastructure_type="INSTANCE_POOL",
        instance_configuration=instance_configuration,
        scaling_policy=scaling_policy,
    )
    environment_config_details = oci.data_science.models.DefaultModelDeploymentEnvironmentConfigurationDetails(
        environment_configuration_type="DEFAULT",
        environment_variables={"WEB_CONCURRENCY": "1"},
    )
    model_group_config_details = oci.data_science.models.ModelGroupConfigurationDetails(
        model_group_id=model_group_id,
    )
    deployment_config = oci.data_science.models.ModelGroupDeploymentConfigurationDetails(
        deployment_type="MODEL_GROUP",
        model_group_configuration_details=model_group_config_details,
        infrastructure_configuration_details=infrastructure_config_details,
        environment_configuration_details=environment_config_details,
    )
    category_log_details = oci.data_science.models.CategoryLogDetails(
        access=oci.data_science.models.LogDetails(log_group_id=LOG_GROUP_OCID, log_id=LOG_OCID),
        predict=oci.data_science.models.LogDetails(log_group_id=LOG_GROUP_OCID, log_id=LOG_OCID),
    )
    response = client.create_model_deployment(
        oci.data_science.models.CreateModelDeploymentDetails(
            display_name=display_name,
            description=description,
            compartment_id=COMPARTMENT_OCID,
            project_id=PROJECT_OCID,
            model_deployment_configuration_details=deployment_config,
            category_log_details=category_log_details,
        )
    )
    deployment_id = response.data.id
    return wait_for_state(
        label=f"model deployment {display_name}",
        getter=lambda: client.get_model_deployment(deployment_id).data,
        success_states={"ACTIVE"},
        failure_states={"FAILED", "DELETED"},
        timeout_seconds=60 * 90,
    )


def live_update_model_deployment(
    client: oci.data_science.DataScienceClient,
    model_deployment_id: str,
    display_name: str,
    description: str,
    new_model_group_id: str,
) -> oci.data_science.models.ModelDeployment:
    update_model_group_configuration_details = oci.data_science.models.UpdateModelGroupConfigurationDetails(
        model_group_id=new_model_group_id,
    )
    model_deployment_configuration_details = oci.data_science.models.UpdateModelGroupDeploymentConfigurationDetails(
        deployment_type="MODEL_GROUP",
        update_type="LIVE",
        model_group_configuration_details=update_model_group_configuration_details,
    )
    client.update_model_deployment(
        model_deployment_id=model_deployment_id,
        update_model_deployment_details=oci.data_science.models.UpdateModelDeploymentDetails(
            display_name=display_name,
            description=description,
            model_deployment_configuration_details=model_deployment_configuration_details,
        ),
    )
    return wait_for_state(
        label=f"model deployment update {model_deployment_id}",
        getter=lambda: client.get_model_deployment(model_deployment_id).data,
        success_states={"ACTIVE"},
        failure_states={"FAILED", "DELETED"},
        timeout_seconds=60 * 90,
    )


def invoke_prediction(
    endpoint_url: str,
    signer: Signer,
    model_key: str,
    payload: dict,
    allow_error: bool = False,
) -> dict:
    response = requests.post(
        f"{endpoint_url.rstrip('/')}/predict",
        json=payload,
        headers={"Content-Type": "application/json", "model-key": model_key},
        auth=signer,
        timeout=120,
    )
    if not allow_error:
        response.raise_for_status()
    try:
        body = response.json()
    except ValueError:
        body = {"raw_text": response.text}
    if response.ok:
        return body
    return {
        "status_code": response.status_code,
        "error": body,
    }


def write_state_file(state: dict) -> Path:
    state_path = BUILD_DIR / "deployment_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True))
    return state_path


def run(profile: str) -> dict:
    config = configure_ads_auth(profile)
    signer = create_signer(config)
    client = oci.data_science.DataScienceClient(config)
    suffix = timestamp_suffix()

    build_result = build_all_artifacts(reset=True)
    prepared_models: Dict[str, PreparedModelArtifact] = build_result["prepared_models"]
    model_group_zip: Path = build_result["model_group"]["zip_path"]

    mvs_square = create_model_version_set(
        name=f"business-model-live-square-{suffix}",
        description="Model version set for square business logic demo.",
    )
    mvs_root = create_model_version_set(
        name=f"business-model-live-root-{suffix}",
        description="Model version set for square-root business logic demo.",
    )

    square_model_id = save_model(prepared_models["square_v1"], mvs_square)
    sqrt_model_id = save_model(prepared_models["sqrt_v1"], mvs_root)

    initial_members = [
        {"model_id": square_model_id, "inference_key": SERVICE_MODEL_SPECS["square_v1"].inference_key},
        {"model_id": sqrt_model_id, "inference_key": SERVICE_MODEL_SPECS["sqrt_v1"].inference_key},
    ]
    model_group_display_name = f"Business-Model-Group-Live-{suffix}"
    model_group_description = "Example of a homogeneous Model Group for bundled custom business logic."
    model_group_id = create_model_group(client, model_group_display_name, model_group_description, initial_members)
    upload_model_group_artifact(client, model_group_id, model_group_zip)

    model_group_history_name = f"Business-Model-Group-History-{suffix}"
    model_group_history_description = "Model group version history for bundled custom business logic."
    model_group_version_history_id = create_model_group_version_history(
        client,
        display_name=model_group_history_name,
        description=model_group_history_description,
        latest_model_group_id=model_group_id,
    )

    deployment = create_model_deployment(
        client,
        display_name=f"Business Logic Model Group {suffix}",
        description="Model Group Deployment for bundled custom business logic.",
        model_group_id=model_group_id,
    )

    before_predictions = {
        "square": invoke_prediction(deployment.model_deployment_url, signer, "square", {"number": 9}),
        "square-root": invoke_prediction(
            deployment.model_deployment_url,
            signer,
            "square-root",
            {"number": -9},
            allow_error=True,
        ),
    }

    abs_sqrt_model_id = save_model(prepared_models["abs_sqrt_v2"], mvs_root)
    updated_members = [
        {"model_id": square_model_id, "inference_key": SERVICE_MODEL_SPECS["square_v1"].inference_key},
        {"model_id": abs_sqrt_model_id, "inference_key": SERVICE_MODEL_SPECS["abs_sqrt_v2"].inference_key},
    ]
    updated_model_group_id = create_model_group(
        client,
        display_name=f"Business-Model-Group-Live-v2-{suffix}",
        description="Updated model group using the same model key with a new model version.",
        members=updated_members,
    )
    upload_model_group_artifact(client, updated_model_group_id, model_group_zip)
    update_model_group_version_history(
        client,
        model_group_version_history_id=model_group_version_history_id,
        display_name=model_group_history_name,
        description=model_group_history_description,
        latest_model_group_id=updated_model_group_id,
    )

    updated_deployment = live_update_model_deployment(
        client,
        model_deployment_id=deployment.id,
        display_name=f"Business Logic Model Group Updated {suffix}",
        description="LIVE update to model group deployment using stable inference keys.",
        new_model_group_id=updated_model_group_id,
    )

    after_predictions = {
        "square": invoke_prediction(updated_deployment.model_deployment_url, signer, "square", {"number": 9}),
        "square-root": invoke_prediction(updated_deployment.model_deployment_url, signer, "square-root", {"number": -9}),
    }

    state = {
        "profile": profile,
        "build_dir": str(BUILD_DIR),
        "model_version_sets": {
            "square": mvs_square.id,
            "square-root": mvs_root.id,
        },
        "models": {
            "square_v1": square_model_id,
            "sqrt_v1": sqrt_model_id,
            "abs_sqrt_v2": abs_sqrt_model_id,
        },
        "model_groups": {
            "initial": model_group_id,
            "updated": updated_model_group_id,
            "version_history": model_group_version_history_id,
        },
        "model_deployment": {
            "id": updated_deployment.id,
            "url": updated_deployment.model_deployment_url,
        },
        "inference_keys": {
            "square": "stable",
            "square-root": "reused-across-live-update",
        },
        "predictions": {
            "before_live_update": before_predictions,
            "after_live_update": after_predictions,
        },
    }
    state_path = write_state_file(state)
    logger.info("Wrote deployment state to %s", state_path)
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create models, model groups, and a live-updated model group deployment in OCI Data Science.")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="OCI config profile name to use.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(verbose=args.verbose)
    result = run(profile=args.profile)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
