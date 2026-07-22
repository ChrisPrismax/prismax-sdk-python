# PrismaX Python SDK

Minimal upload SDK for PrismaX robotic data.

- GitHub repository: <https://github.com/PrismaXAI/sdk-vla-foundry>
- Issues and feature requests: <https://github.com/PrismaXAI/sdk-vla-foundry/issues>

## License

This SDK is source-available for noncommercial use under the PolyForm
Noncommercial License 1.0.0. Commercial use is not permitted unless PrismaX
grants a separate commercial license.

## Start Here

Follow these steps in order:

1. [Prepare your account and data](#1-prepare-your-account-and-data)
2. [Choose an upload structure](#2-choose-an-upload-structure)
3. [Upload with Python](#3-upload-with-python)
4. [Check status and resume](#4-check-status-and-resume)

Additional reference:

- [CLI commands](#cli-reference)
- [Error handling](#error-handling)

Before uploading, the SDK needs to know which files belong to each episode and
which three videos are the primary environment, left, and right views. Choose
the option that best matches how your data is currently organized:

- **[Expected folder structure](#option-a-expected-folder-structure):** organize
  the data into the standard PrismaX folders and filenames. Each episode uses
  one root MCAP file and one matching video folder. This is the simplest option
  and does not require JSON.
- **[Templated JSON](#option-b-templated-json):** use this when every episode has
  the same folder and filename pattern. List the episode keys once and describe
  the shared paths with an `{episode_key}` placeholder. The SDK uses that
  template to find and group every episode automatically.
- **[Explicit JSON](#option-c-explicit-json):** use this when episodes have
  different filenames or folder layouts. List the exact MCAP and video paths
  for each episode so the SDK does not need to infer their relationships.

## 1. Prepare Your Account And Data

You need:

- a PrismaX Operator account
- a PrismaX upload API key with the `pxu_` prefix
- a PrismaX task scenario/name
- the serial number of the registered robot that produced the data
- one MCAP file and at least three MP4 videos for each episode

Create and find these in the PrismaX app:

- **App:** <https://app.prismax.ai>
- **Upload API key:** open <https://app.prismax.ai/account>, go to **API Keys**,
  then create an **Operator / Upload** key. The key is shown once.
- **Task scenario/name:** open <https://app.prismax.ai/data/upload> and use the
  task card title, for example `Pick and place packaged food items`. You can
  also retrieve the available scenario names with
  `prismax.list_scenarios()`.
- **Robot serial number:** open <https://app.prismax.ai/account> and use the
  serial number of the registered Operator machine that produced the data.

Download API keys are not valid for uploads. The backend verifies that the
serial number belongs to the upload API key owner.

Install and configure the SDK:

```bash
pip install prismax
export PRISMAX_API_KEY="pxu_your_upload_api_key"
```

You can list available scenario names from Python. This does not require an API
key:

```python
import prismax

print(prismax.list_scenarios())
```

Scenario matching is case-insensitive.

## 2. Choose An Upload Structure

Choose one of the following structures. The expected folder structure is the
simplest option. Use JSON when your collection software needs an explicit,
portable description of the files in each episode.

### Option A: Expected Folder Structure

No JSON file is required when the source data already follows this layout:

```text
data/
  1.mcap
  1/
    high.mp4
    left.mp4
    right.mp4
    high2.mp4
    left2.mp4
    right2.mp4

  2.mcap
  2/
    high.mp4
    left.mp4
    right.mp4
```

Each episode must have:

- one root `{episode_key}.mcap` file
- at least three MP4 files directly under `{episode_key}/`
- one primary filename containing `left`
- one primary filename containing `right`
- one primary environment/high filename containing neither

Exact primary names are preferred. For example, `high.mp4`, `left.mp4`, and
`right.mp4` are selected before `high2.mp4`, `left2.mp4`, and `right2.mp4`.
All remaining MP4 files are additional videos.

### Option B: Templated JSON

Use templated JSON when all episodes follow the same source layout. Create a
local `prismax_upload.json` next to the data:

```json
{
  "scenario": "Put away messy clothes",
  "robot": {
    "serial_number": "MD100101000019205Z00082"
  },
  "episode_set": {
    "mode": "templated",
    "episode_keys": [
      "episode_1",
      "episode_2",
      "episode_3"
    ],
    "file_layout": {
      "mcap": {
        "source_path": "{episode_key}.mcap"
      },
      "primary_videos": {
        "env": {
          "source_path": "{episode_key}/high.mp4"
        },
        "left": {
          "source_path": "{episode_key}/left.mp4"
        },
        "right": {
          "source_path": "{episode_key}/right.mp4"
        }
      },
      "additional_videos": {
        "source_glob": "{episode_key}/*.mp4"
      }
    }
  }
}
```

The `{episode_key}` placeholder is replaced with each value in `episode_keys`.
The additional video glob may match all episode MP4s; the SDK automatically
excludes the three declared primary source files.

### Option C: Explicit JSON

Use explicit JSON when episodes use different source paths:

```json
{
  "scenario": "Put away messy clothes",
  "robot": {
    "serial_number": "MD100101000019205Z00082"
  },
  "episode_set": {
    "mode": "explicit",
    "episodes": [
      {
        "episode_key": "episode_1",
        "assets": {
          "mcap": {
            "source_path": "capture-a/recording.mcap"
          },
          "primary_videos": {
            "env": {
              "source_path": "capture-a/cameras/top.mp4"
            },
            "left": {
              "source_path": "capture-a/cameras/hand-left.mp4"
            },
            "right": {
              "source_path": "capture-a/cameras/hand-right.mp4"
            }
          },
          "additional_videos": [
            {
              "source_path": "capture-a/cameras/top-stereo.mp4"
            }
          ]
        }
      },
      {
        "episode_key": "episode_2",
        "assets": {
          "mcap": {
            "source_path": "capture-b/robot.mcap"
          },
          "primary_videos": {
            "env": {
              "source_path": "capture-b/videos/environment.mp4"
            },
            "left": {
              "source_path": "capture-b/videos/left-camera.mp4"
            },
            "right": {
              "source_path": "capture-b/videos/right-camera.mp4"
            }
          },
          "additional_videos": []
        }
      }
    ]
  }
}
```

All source paths are relative to the JSON file. Additional video basenames must
be unique within each episode. The same structure can also be provided as a
Python dictionary:

```python
data = prismax.DataUpload.from_dict(payload, base_path="./data")
```

Use lowercase `.mp4` extensions. Hidden files are ignored during templated glob
matching, including macOS metadata files such as `.DS_Store` and `._left.mp4`.

Before a JSON upload begins, the SDK rejects:

- missing files or paths outside the upload folder
- uppercase `.MP4` extensions
- duplicate or unsafe episode keys
- a source file assigned more than once
- additional videos that collide with primary or other destination filenames
- uploads above the backend's 2,000-file limit, including generated manifests

Episode keys may contain letters, numbers, dots, underscores, and hyphens, and
must start with a letter or number.

## 3. Upload With Python

### Option A: Expected Folder (No JSON)

Use `prismax.upload()` when the source already follows the expected folder
structure. Replace the three uppercase values:

```python
import prismax

result = prismax.upload(
    "DATA_FOLDER_PATH",
    scenario="SCENARIO_NAME",
    serial_number="ROBOT_SERIAL_NUMBER",
)

print(result["upload_id"])
```

### Option B: Templated JSON (Same Layout For Every Episode)

Use the templated JSON from
[Option B](#option-b-templated-json), then upload all declared episodes:

```python
import prismax

data = prismax.DataUpload.from_json(
    "/path/to/prismax_upload.json"  # CHANGE: templated JSON path
)
upload_id = prismax.create_upload_session(data)
result = prismax.upload_session(upload_id, data)
```

### Option C: Explicit JSON (Custom Paths For Each Episode)

Use the explicit JSON from
[Option C](#option-c-explicit-json), then upload all declared episodes:

```python
import prismax

data = prismax.DataUpload.from_json(
    "/path/to/prismax_upload.json"  # CHANGE: explicit JSON path
)
upload_id = prismax.create_upload_session(data)
result = prismax.upload_session(upload_id, data)
```

For either JSON option, first call `create_upload_session()` to get one
`upload_id` for every episode declared in the JSON. You can then upload all
episodes together, as shown above, or upload them individually:

```python
prismax.upload_episode(upload_id, "episode_1", data)
prismax.upload_episode(upload_id, "episode_2", data)
```

Episode uploads may run concurrently. The episode list is fixed when the
`upload_id` is created; later calls cannot append undeclared episodes.

You can pass the API key directly instead of using `PRISMAX_API_KEY`:

```python
upload_id = prismax.create_upload_session(
    data,
    api_key="pxu_your_upload_api_key",
)
```

## 4. Check Status And Resume

Status and resume require the upload API key that owns the original upload.
If you no longer have an `upload_id`, list your most recent uploads first:

```python
import prismax

uploads = prismax.recent_uploads(limit=10)
for item in uploads:
    print(item["upload_id"], item["status"], item["created_at"], item["scenario"])
```

Results are ordered from newest to oldest and only include uploads owned by the
current upload API key. Each item includes its `upload_id`, status, scenario,
robot serial number, episode count, and creation time. The limit can be from 1
to 100.

Use an `upload_id` from that list to request its detailed status:

```python
upload_status = prismax.status(123)
print(upload_status["status"])
print(upload_status.get("processing_error"))
print(upload_status.get("episodes"))
```

Resume a JSON-defined upload with the same JSON and source files:

```python
data = prismax.DataUpload.from_json("./prismax_upload.json")
resume_result = prismax.resume_upload(123, data)
```

Resume an expected-folder upload with the same complete original folder:

```python
resume_result = prismax.resume(123, "./data")
```

The SDK asks the backend which files are missing and does not upload completed
files again. Resume is allowed only while the overall upload status is
`UPLOADING`. Once it leaves `UPLOADING`, create a new upload instead.

Wait for the worker to reach a terminal status:

```python
result = prismax.wait_for_upload(123, max_wait=1800)
```

## Additional Reference

The following sections are optional references after completing the main upload
flow above.

### CLI Reference

List available scenarios:

```bash
prismax scenarios
```

Upload the expected folder structure:

```bash
prismax upload ./data \
  --scenario "Pick and place packaged food items" \
  --serial-number robot_serial_number
```

Create and upload a JSON-defined upload:

```bash
prismax upload-data ./prismax_upload.json
```

Resume either upload type:

```bash
prismax resume 123 ./data
prismax resume-data 123 ./prismax_upload.json
```

Check status:

```bash
prismax uploads
prismax uploads --limit 20
prismax status 123
prismax status 123 --json
```

Use `--wait` to wait for worker processing. The default maximum wait is 30
minutes:

```bash
prismax upload-data ./prismax_upload.json --wait
prismax upload-data ./prismax_upload.json --wait --max-wait 3600
```

Useful upload options:

```bash
prismax upload-data ./prismax_upload.json --concurrency 8 --timeout 120 --retries 5
prismax upload-data ./prismax_upload.json --no-progress
```

Creating or resuming an upload waits up to five minutes by default, while
regular API requests and individual file uploads use the `--timeout` value.
Usually, neither timeout needs to be changed.

### Error Handling

```python
import prismax

try:
    data = prismax.DataUpload.from_json("./prismax_upload.json")
    upload_id = prismax.create_upload_session(data)
    prismax.upload_session(upload_id, data)
except prismax.PrismaxValidationError as exc:
    print(f"Invalid upload definition: {exc}")
except prismax.PrismaxAuthError as exc:
    print(f"API key or permission error: {exc}")
except prismax.PrismaxApiError as exc:
    print(f"PrismaX API error: {exc}")
```

If file upload fails after the `upload_id` is created, the error includes it
and a resume instruction.
