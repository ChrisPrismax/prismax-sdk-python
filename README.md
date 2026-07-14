# PrismaX Python SDK

Minimal upload SDK for PrismaX data uploads.

GitHub repository: <https://github.com/PrismaXAI/sdk-vla-foundry>

Issues and feature requests: <https://github.com/PrismaXAI/sdk-vla-foundry/issues>

## License

This SDK is source-available for noncommercial use under the PolyForm
Noncommercial License 1.0.0. Commercial use is not permitted unless PrismaX
grants a separate commercial license.

## Quickstart

You need:

- a PrismaX upload API key with the `pxu_` prefix
- a PrismaX task ID or task scenario/name
- the robot serial number for the machine that produced the data

Create and find these in the PrismaX app:

- App: <https://app.prismax.ai>
- Upload API key: open <https://app.prismax.ai/account>, go to **API Keys**,
  then create an **Operator / Upload** key. The key is shown once, so copy it
  when it is created.
- Task scenario/name: open <https://app.prismax.ai/data/upload> and use the
  task card title, for example `Pick and place packaged food items`. The SDK
  resolves this to the database task ID automatically using a case-insensitive
  match.
- Task ID: if you already know the numeric database task ID, you can pass it
  directly instead of the scenario/name.
- Robot serial number: open <https://app.prismax.ai/account> and use the serial
  number for the registered operator machine that produced the data.

```bash
pip install prismax
export PRISMAX_API_KEY="pxu_your_upload_api_key"
```

Download API keys are not valid for uploads.

The published SDK defaults to the PrismaX production data API. Internal test
environments can override the endpoint with `PRISMAX_BASE_URL`; an explicit
`base_url=` argument takes precedence over the environment variable. Keep beta
endpoint values in environment configuration rather than source code.

List available scenarios. This does not require an API key:

```bash
prismax scenarios
```

```python
import prismax

print(prismax.list_scenarios())
```

```python
import prismax

result = prismax.upload(
    "./data",
    scenario="Pick and place packaged food items",
    serial_number="robot_serial_number",
)
print(result["upload_id"])
```

The SDK scans the folder, creates an upload session, uploads raw files to signed
Google Cloud URLs, generates episode manifests in memory, uploads manifests last,
and returns the upload summary.

You can also pass the API key directly:

```python
import prismax

result = prismax.upload(
    "./data",
    scenario="Pick and place packaged food items",
    serial_number="robot_serial_number",
    api_key="pxu_your_upload_api_key",
)
```

You can also pass `task_id=123` instead of `scenario=...`.

## Expected Folder Structure

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
```

Each episode must contain one root `{episode}.mcap` file and at least three MP4
files under `{episode}/`: one primary filename containing `left`, one containing
`right`, and one environment/high video filename containing neither. Additional
MP4 files are uploaded as raw files, included in downloads, and checked for
duration consistency. A duration mismatch across any episode videos can fail the
upload. Only the primary three videos are processed for derived previews, QA,
and duplicate detection.

Use lowercase `.mp4` extensions. Uppercase variants such as `.MP4` are rejected
so client validation, worker processing, and download manifests choose the same
primary videos. Hidden files are ignored, including macOS metadata files such as
`.DS_Store` and `._left.mp4`.

Primary video selection is based on filename:

- filenames containing `left` are treated as left videos
- filenames containing `right` are treated as right videos
- other MP4 files are treated as environment/high videos
- exact names are preferred, for example `high.mp4`, `left.mp4`, and
  `right.mp4` are selected before `high2.mp4`, `left2.mp4`, and `right2.mp4`

Example:

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
```

Primary videos:

```text
env/high: high.mp4
left: left.mp4
right: right.mp4
```

Additional videos:

```text
high2.mp4
left2.mp4
right2.mp4
```

## CLI

```bash
prismax scenarios
prismax upload ./data --scenario "Pick and place packaged food items" --serial-number robot_serial_number
prismax status 123
```

Use `--wait` to wait for the worker to finish. The default maximum wait time is
30 minutes.

```bash
prismax upload ./data --scenario "Pick and place packaged food items" --serial-number robot_serial_number --wait
prismax upload ./data --scenario "Pick and place packaged food items" --serial-number robot_serial_number --wait --max-wait 3600
```

Useful CLI options:

```bash
prismax upload ./data --scenario "Pick and place packaged food items" --serial-number robot_serial_number --timeout 120 --retries 5
prismax upload ./data --scenario "Pick and place packaged food items" --serial-number robot_serial_number --wait --poll-interval 5 --max-poll-errors 3
```

## Status and Resume

Status and resume require a PrismaX upload API key, either from
`PRISMAX_API_KEY` or the `api_key=` argument. Use an upload key with access to
the original upload; download API keys are not valid.

```python
import prismax

upload_status = prismax.status(123)

resume_result = prismax.resume(
    123,
    "./data",  # same original folder used for the upload
)
```

```bash
prismax status 123
prismax resume 123 ./data
```

Resume expects the same original complete upload folder/file list, not only the
files that are missing from cloud storage. Do not pass a folder that contains
only failed or remaining files. The SDK will ask the API which files still need
signed upload URLs. Resume is only allowed while the upload is still in
`UPLOADING` status; once processing has started or the upload reaches a
terminal status, create a new upload instead.

## Error Handling

```python
import prismax

try:
    prismax.upload(
        "./data",
        scenario="Pick and place packaged food items",
        serial_number="robot_serial_number",
    )
except prismax.PrismaxValidationError as exc:
    print(f"Invalid upload folder: {exc}")
except prismax.PrismaxAuthError as exc:
    print(f"API key or permission error: {exc}")
except prismax.PrismaxApiError as exc:
    print(f"PrismaX API error: {exc}")
```

If raw file upload fails after the session is created, the SDK error includes
the upload ID and a `prismax resume <upload_id> <original_folder>` command.
