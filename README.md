# PrismaX Python SDK

Minimal upload SDK for PrismaX data uploads.

## License

This SDK is source-available for noncommercial use under the PolyForm
Noncommercial License 1.0.0. Commercial use is not permitted unless PrismaX
grants a separate commercial license.

## Quickstart

```bash
pip install prismax
export PRISMAX_API_KEY="pxu_your_upload_api_key"
```

Use a PrismaX upload API key with the `pxu_` prefix. Download API keys are not
valid for uploads.

```python
import prismax

result = prismax.upload("./data", task_id=123, machine_id="machine_id")
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
    task_id=123,
    machine_id="machine_id",
    api_key="pxu_your_upload_api_key",
)
```

## Expected Folder Structure

```text
data/
  1.mcap
  1/
    high.mp4
    left.mp4
    right.mp4
```

Each episode must contain one root `{episode}.mcap` file and exactly three MP4
files under `{episode}/`: one filename containing `left`, one containing `right`,
and one environment video filename containing neither.

## CLI

```bash
prismax upload ./data --task-id 123 --machine-id machine_id
prismax status 123
```

Use `--wait` to wait for the worker to finish. The default maximum wait time is
30 minutes.

```bash
prismax upload ./data --task-id 123 --machine-id machine_id --wait
prismax upload ./data --task-id 123 --machine-id machine_id --wait --max-wait 3600
```

## Status and Resume

```python
import prismax

upload_status = prismax.status(123)

resume_result = prismax.resume(
    123,
    "./data",
)
```

```bash
prismax status 123
prismax resume 123 ./data
```

## Custom API Base URL

The SDK defaults to the PrismaX production data API. For beta or local testing,
set `PRISMAX_BASE_URL` or pass `base_url`.

```bash
export PRISMAX_BASE_URL="https://data.prismaxserver.com"
```

```python
import prismax

prismax.upload(
    "./data",
    task_id=123,
    machine_id="machine_id",
    base_url="http://127.0.0.1:8082",
)
```

## Error Handling

```python
import prismax

try:
    prismax.upload("./data", task_id=123, machine_id="machine_id")
except prismax.PrismaxValidationError as exc:
    print(f"Invalid upload folder: {exc}")
except prismax.PrismaxApiError as exc:
    print(f"PrismaX API error: {exc}")
```
