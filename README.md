# OCI Data Science Model Group live updates for models using custom logic

This folder contains an example of how to prepare OCI Data Science Models for Model Group Deployments using custom logic.

This package can be reused by  **Data Science teams**, to make it easier to prepare artefacts for group deployments.

Please note:

- your team should mainly edit the **model package under `source_models/`**
- your team should usually **leave the shared boilerplate alone** (except for updating the `runtime.yaml`)

## What is included

- `build_model_artifacts.py`
  - builds the example model artifacts and the generic Model Group artifact locally
- `deploy_to_oci.py`
  - creates the OCI resources and performs the LIVE update workflow
- `templates/`
  - generic runtime and score files
- `source_models/`
  - example model packages showing how to bundle business logic and local dependencies inside each model artifact

## Breakdown of the repo for usage

You can think of it in three layers:

### 1. The part you own: `source_models/`
This is where your team puts:
- your custom business logic
- helper `.py` files
- local config files
- lookup tables
- templates
- validators
- transforms
- any other Python modules your model needs at runtime

This is the part you are expected to adapt.

### 2. The shared platform boilerplate: `templates/`
This is the OCI-specific loader layer.
It exists so OCI can load the right artifact correctly.

Most teams should **not** need to change this often. An exception here is likely the `runtime.yaml` which will be used to set the Python dependencies of your deployment.

### 3. The automation scripts
- `build_model_artifacts.py`
- `deploy_to_oci.py`

These are wrappers that package and deploy your model artifacts.
They are important, but not something you need to regularly update.

## What to edit vs what to leave alone

### Files your team will usually edit
- `source_models/.../model.py`
- `source_models/.../operations.py`
- `source_models/.../parsing.py`
- `source_models/.../resources.py`
- any additional helper modules you add
- bundled config/resources such as `settings.json`

### Files your team will usually leave alone
- `templates/model_group_score.py`
- `templates/single_model_score.py`
- `templates/runtime.yaml`
- most of `build_model_artifacts.py`
- most of `deploy_to_oci.py`

## Important distinction between the two main scripts

### `build_model_artifacts.py`
This script does **local artifact assembly only**.

It:
- creates the local model artifact folders under `build/`
- creates the local Model Group artifact folder and zip file
- serializes each model into `model.pickle`
- copies the bundled Python packages and local resources into each model artifact
- uses `GenericModel.prepare(...)` only to generate the standard local OCI artifact files such as `score.py`, `runtime.yaml`, and `.model-ignore`

It does **not** create resources in OCI by itself. For new models, you need to update the *SERVICE_MODEL_SPECS* object.

### `deploy_to_oci.py`
This script is the one that actually **creates and updates OCI resources**. 

It:
- calls `build_model_artifacts.py`
- creates the Model Version Sets
- saves/uploads the Model Catalog entries for the model artifacts
- creates the Model Group
- uploads the Model Group artifact zip
- creates the Model Group Version History
- creates the Model Deployment
- performs the LIVE update on the existing deployment using a new Model Group

So in short:
- `build_model_artifacts.py` prepares the **local artifact contents**
- `deploy_to_oci.py` uses those prepared contents to create the **actual OCI resources**


## Core design pattern

### 1. Keep the Model Group artifact generic
The Model Group artifact should contain only:
- `runtime.yaml`
- generic `score.py`

Its job is only to:
1. receive the selected model folder from OCI MMS
2. add that folder to `sys.path`
3. load `model.pickle`
4. call `model.predict(data)`

This is why `templates/model_group_score.py` is the most important file in the Model Group artifact.

### 2. Put model-specific logic in each model artifact
Each model artifact should contain:
- `model.pickle`
- `runtime.yaml`
- `score.py`
- one or more bundled Python modules/packages
- any local resources such as JSON config, templates, lookup files, validators, transforms, or helper code

That separation is the key to making **LIVE update** work.

### 3. Treat each model version as self-contained
If business logic changes, create a **new model artifact** and publish it as a **new model version**.
Then:
- create a new immutable Model Group that references the new model OCID
- keep the same inference key if desired
- LIVE update the existing deployment to the new Model Group

## What we learned

### Lesson 1: do not put custom business logic in the Model Group artifact
If you do, LIVE update will not refresh that code because OCI does not rebuild the Model Group runtime artifact during a live update.

### Lesson 2: inference keys can stay stable
The **model key / inference key** can remain the same while the underlying **model OCID** changes.
This is ideal for SaaS-style routing because clients keep calling the same friendly key.

### Lesson 3: package names must be unique per model/version
Use unique top-level package names such as:
- `square_service_v1_bundle`
- `sqrt_service_v1_bundle`
- `abs_sqrt_service_v2_bundle`

This avoids Python import collisions when multiple models are loaded into the same serving process.

### Lesson 4: local resources must be bundled with the model
If the model code reads files such as `settings.json`, those files must exist inside the model artifact.
The model should resolve them relative to `__file__`, not from an external location.

### Lesson 5: `.model-ignore` matters
A bad ignore rule can silently strip files from the uploaded artifact.
In our case, ignoring `build/` caused staged artifacts to upload as empty zip files because the artifact directories themselves lived under a build folder.

## How the model artifact is serialized

The build flow is:
1. create the artifact directory
2. generate base OCI files with `GenericModel.prepare(...)`
3. copy the bundled source package into the artifact directory
4. instantiate the model class from that bundled package
5. serialize it to `model.pickle`

Serialization uses:
- `cloudpickle` if available
- otherwise standard `pickle`

At inference time, deserialization works because the generic loader first inserts the model artifact directory into `sys.path`, which allows Python to import the bundled package.

## Design tips for customer reimplementation

### If your real model has multiple dependencies
Bundle them as a normal Python package inside the model artifact, for example:

```text
my_customer_model_v20260407/
├── __init__.py
├── model.py
├── parsing.py
├── transforms.py
├── validators.py
├── feature_logic.py
├── helper_a.py
├── helper_b.py
├── config.json
└── templates/
```

Then serialize an instance of the class defined in `model.py`.

### Use relative file loading
For any bundled non-Python resource, load it relative to the module file, for example:

```python
from pathlib import Path
config = Path(__file__).with_name("config.json")
```

### Keep the Model Group score file boring
The more generic and stable the Model Group `score.py` is, the better.
It should not know business rules. It should only know how to load the selected model artifact and call it.

### Expect immutable Model Groups
A LIVE update reuses the **deployment**, but typically points it at a **new Model Group resource** containing the new member models.

## Practical reimplementation workflow for a Data Science team

1. Copy this folder into your own project.
2. Pick one example bundle in `source_models/` as your starting point.
3. Replace the example logic with your own model logic and helper modules.
4. Add any extra local dependencies your model needs into that same package.
5. Keep the generic template files unchanged unless you have a platform-level reason to change them.
6. Run `build_model_artifacts.py` to assemble the artifacts.
7. Run `deploy_to_oci.py` to create or update the OCI resources.

## Step-by-step example: adding a new natural log model

Suppose you want to add a new model that returns the **natural log** of a number.

### Step 1: create a new model package
Under `source_models/`, add a new folder, for example:

```text
source_models/log_service_v1_bundle/
```

Inside it, create at least:

```text
log_service_v1_bundle/
├── __init__.py
├── model.py
├── operations.py
├── parsing.py
├── resources.py
└── settings.json
```

If your real model has more helper files, add them here too.

### Step 2: implement the log logic
In `operations.py`, define the actual log calculation.
For example:

```python
import math


def compute_natural_log(number: float) -> float:
    return math.log(number)
```

In `parsing.py`, use the same input-parsing pattern as the other examples.

In `model.py`, define a class such as `LogModel` with a `predict(self, data)` method that:
- parses the payload
- calls the log function
- returns the result

### Step 3: decide how invalid inputs should behave
Natural log is only defined for values greater than zero.

You need to choose the business behavior for:
- `0`
- negative values

For example, you might:
- let Python raise an error naturally, or
- implement a custom business rule

The important thing is: this behavior belongs in the **model package**, not in the generic Model Group loader.

### Step 4: add the new model to `build_model_artifacts.py`
In `SERVICE_MODEL_SPECS`, add a new `ModelSpec`, for example:

- `artifact_name="model4_live"`
- `source_package="log_service_v1_bundle"`
- `class_name="LogModel"`
- `display_name="Business Model 3"`
- `inference_key="log"`
- `version_label="Version 1"`
- `description="Returns the natural log of the input number."`

This tells the build script to create a new model artifact for the log model.

### Step 5: run `build_model_artifacts.py`
This will:
- create `build/model4_live/`
- generate `score.py`, `runtime.yaml`, and `.model-ignore`
- copy the bundled `log_service_v1_bundle/` package into the artifact
- serialize an instance of `LogModel` into `model.pickle`

### Step 6: check the built artifact
You should see something like:

```text
build/model4_live/
├── .model-ignore
├── model.pickle
├── runtime.yaml
├── score.py
└── log_service_v1_bundle/
    ├── __init__.py
    ├── model.py
    ├── operations.py
    ├── parsing.py
    ├── resources.py
    └── settings.json
```

If those files are not present, do not deploy yet.

### Step 7: update `deploy_to_oci.py`
Decide whether the new log model is:
- an additional model in the group, or
- a replacement for an existing one

If it is an additional model, add it to the Model Group membership with a new inference key such as:
- `log`

If it replaces an existing model, keep the existing inference key and swap the model OCID.

### Step 8: deploy to OCI
Run `deploy_to_oci.py`.
This will:
- save the log model into the Model Catalog
- create or update the Model Group
- upload the generic Model Group artifact
- create or update the model deployment

### Step 9: call the endpoint with the new inference key
For example:

- payload: `{"number": 10}`
- header: `model-key: log`

That will route the request to the new natural-log model.

## Files to start from

If a user wants to adapt this pattern, start with:
- `source_models/` for business logic
- `templates/model_group_score.py` for understanding the generic loader
- `build_model_artifacts.py` for packaging
- `deploy_to_oci.py` for OCI creation/update steps

Then replace the example packages under `source_models/` with the new real bundled business logic.
