import sys
import time
from datetime import datetime, timedelta, timezone

from .client import DEFAULT_SESSION_TIMEOUT, PrismaXClient
from .data_upload import DataUpload
from .errors import PrismaxApiError, PrismaxValidationError
from .manifest import build_manifest_payload, manifest_placeholder
from .scanner import scan_folder, validate_mcap_mp4, episode_keys


TERMINAL_STATUSES = {
    "DERIVED_READY",
    "DERIVED_VALIDATION_FAILED",
    "FAILED",
    "DERIVED_PARTIALLY_READY",
}
DEFAULT_POLL_ERROR_LIMIT = 3


class _FileProgress:
    def __init__(
        self,
        *,
        upload_id,
        total_files,
        completed_files,
        total_episodes,
        completed_episodes,
        enabled,
    ):
        self.upload_id = upload_id
        self.total_files = total_files
        self.completed_files = completed_files
        self.total_episodes = total_episodes
        self.completed_episodes = completed_episodes
        self.enabled = bool(enabled)
        if self.enabled:
            self._print("ready")

    def file_completed(self, item):
        self.completed_files += 1
        self._print(item.get("relative_path") or "file uploaded")

    def episode_completed(self, episode_key):
        self.completed_episodes += 1
        self._print(f"{episode_key} submitted")

    def _print(self, detail):
        if not self.enabled:
            return
        print(
            f"Upload {self.upload_id}: files {self.completed_files}/{self.total_files}; "
            f"episodes {self.completed_episodes}/{self.total_episodes}; {detail}",
            file=sys.stderr,
            flush=True,
        )


def _require_data_upload(value):
    if not isinstance(value, DataUpload):
        raise PrismaxValidationError(
            "data_upload must be a DataUpload. Use DataUpload.from_json(path) first."
        )
    return value


def _build_client(
    *, api_key, base_url, timeout, session_timeout, concurrency, retries
):
    return PrismaXClient(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        session_timeout=session_timeout,
        concurrency=concurrency,
        retries=retries,
    )


def _build_files_payload(files, keys):
    payload = [item.as_api_payload() for item in files]
    payload.extend(manifest_placeholder(key) for key in keys)
    return payload


def _normalize_task_name(value):
    return " ".join(str(value or "").strip().lower().split())


def _task_display_name(task):
    for key in ("scenario", "task_name", "name", "title"):
        value = task.get(key)
        if value:
            return str(value)
    return f"task_id {task.get('task_id')}"


def resolve_task_id(client, *, task_id=None, scenario=None, task_name=None):
    if task_id is not None:
        try:
            return int(task_id)
        except (TypeError, ValueError):
            raise PrismaxValidationError(f"task_id must be an integer, got: {task_id!r}") from None

    requested_name = scenario if scenario is not None else task_name
    normalized_name = _normalize_task_name(requested_name)
    if not normalized_name:
        raise PrismaxValidationError("Either task_id or scenario is required.")

    tasks = client.list_tasks()
    matches = []
    for task in tasks or []:
        candidate_values = [
            task.get("scenario"),
            task.get("task_name"),
            task.get("name"),
            task.get("title"),
        ]
        if any(_normalize_task_name(value) == normalized_name for value in candidate_values if value):
            matches.append(task)

    if not matches:
        available = ", ".join(_task_display_name(task) for task in (tasks or [])[:10])
        suffix = f" Available tasks include: {available}." if available else ""
        raise PrismaxValidationError(
            f"No task found for scenario/task name: {requested_name!r}.{suffix}"
        )
    if len(matches) > 1:
        choices = ", ".join(f"{_task_display_name(task)} (task_id {task.get('task_id')})" for task in matches)
        raise PrismaxValidationError(
            f"Multiple tasks matched scenario/task name {requested_name!r}: {choices}. Use task_id instead."
        )

    resolved = matches[0].get("task_id")
    if resolved is None:
        raise PrismaxValidationError(f"Matched task has no task_id for scenario/task name: {requested_name!r}.")
    return int(resolved)


def create_upload_session(
    data_upload,
    *,
    task_id=None,
    api_key=None,
    base_url=None,
    timeout=60,
    session_timeout=DEFAULT_SESSION_TIMEOUT,
    concurrency=5,
    retries=3,
):
    data_upload = _require_data_upload(data_upload)
    client = _build_client(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        session_timeout=session_timeout,
        concurrency=concurrency,
        retries=retries,
    )
    task_id = resolve_task_id(
        client,
        task_id=task_id,
        scenario=data_upload.scenario,
    )
    session = client.create_upload_session(
        task_id=task_id,
        serial_number=data_upload.serial_number,
        files=_build_files_payload(data_upload.files, data_upload.episode_keys),
    )
    session = dict(session)
    session.setdefault("task_id", task_id)
    upload_id = session.get("upload_id")
    if upload_id is None:
        raise PrismaxApiError("Create upload session response did not include upload_id.")
    data_upload._store_session(session)
    return int(upload_id)


def upload_episode(
    upload_id,
    episode_key,
    data_upload,
    *,
    api_key=None,
    base_url=None,
    progress=True,
    timeout=60,
    session_timeout=DEFAULT_SESSION_TIMEOUT,
    concurrency=5,
    retries=3,
):
    data_upload = _require_data_upload(data_upload)
    files = data_upload.files_for_episode(episode_key)
    data_upload._claim_episode_uploads([episode_key])
    try:
        client = _build_client(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            session_timeout=session_timeout,
            concurrency=concurrency,
            retries=retries,
        )
        session = _get_or_resume_data_session(client, upload_id, data_upload)
        try:
            _upload_data_session_files(
                client=client,
                session=session,
                files=files,
                episode_keys_value=[episode_key],
                progress=progress,
            )
        except PrismaxApiError as exc:
            raise PrismaxApiError(
                f"Upload {upload_id} failed while uploading episode {episode_key!r}. "
                f"Resume with prismax.resume_upload({upload_id}, data_upload). "
                f"Original error: {exc}"
            ) from exc
        return _public_session_result(session)
    finally:
        data_upload._discard_session(upload_id)
        data_upload._release_episode_uploads([episode_key])


def upload_session(
    upload_id,
    data_upload,
    *,
    api_key=None,
    base_url=None,
    progress=True,
    wait=False,
    poll_interval=10,
    max_wait=1800,
    max_poll_errors=DEFAULT_POLL_ERROR_LIMIT,
    timeout=60,
    session_timeout=DEFAULT_SESSION_TIMEOUT,
    concurrency=5,
    retries=3,
):
    data_upload = _require_data_upload(data_upload)
    claimed_episode_keys = data_upload.episode_keys
    data_upload._claim_episode_uploads(claimed_episode_keys)
    try:
        client = _build_client(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            session_timeout=session_timeout,
            concurrency=concurrency,
            retries=retries,
        )
        session = _get_or_resume_data_session(client, upload_id, data_upload)
        try:
            _upload_data_session_files(
                client=client,
                session=session,
                files=data_upload.files,
                episode_keys_value=claimed_episode_keys,
                progress=progress,
            )
        except PrismaxApiError as exc:
            raise PrismaxApiError(
                f"Upload {upload_id} failed while uploading files. "
                f"Resume with prismax.resume_upload({upload_id}, data_upload). "
                f"Original error: {exc}"
            ) from exc
        if wait:
            return wait_for_upload(
                upload_id,
                api_key=api_key,
                base_url=base_url,
                poll_interval=poll_interval,
                max_wait=max_wait,
                max_poll_errors=max_poll_errors,
                timeout=timeout,
                retries=retries,
            )
        return _public_session_result(session)
    finally:
        data_upload._discard_session(upload_id)
        data_upload._release_episode_uploads(claimed_episode_keys)


def resume_upload(
    upload_id,
    data_upload,
    *,
    api_key=None,
    base_url=None,
    progress=True,
    wait=False,
    poll_interval=10,
    max_wait=1800,
    max_poll_errors=DEFAULT_POLL_ERROR_LIMIT,
    timeout=60,
    session_timeout=DEFAULT_SESSION_TIMEOUT,
    concurrency=5,
    retries=3,
):
    data_upload = _require_data_upload(data_upload)
    claimed_episode_keys = data_upload.episode_keys
    data_upload._claim_episode_uploads(claimed_episode_keys)
    try:
        client = _build_client(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            session_timeout=session_timeout,
            concurrency=concurrency,
            retries=retries,
        )
        session = client.resume_upload_session(
            upload_id=upload_id,
            files=_build_files_payload(data_upload.files, claimed_episode_keys),
        )
        session = dict(session)
        session.setdefault("upload_id", int(upload_id))
        data_upload._store_session(session)
        try:
            _upload_data_session_files(
                client=client,
                session=session,
                files=data_upload.files,
                episode_keys_value=claimed_episode_keys,
                progress=progress,
            )
        except PrismaxApiError as exc:
            raise PrismaxApiError(
                f"Resume for upload {upload_id} failed while uploading files. "
                f"Retry prismax.resume_upload({upload_id}, data_upload). "
                f"Original error: {exc}"
            ) from exc
        if wait:
            return wait_for_upload(
                upload_id,
                api_key=api_key,
                base_url=base_url,
                poll_interval=poll_interval,
                max_wait=max_wait,
                max_poll_errors=max_poll_errors,
                timeout=timeout,
                retries=retries,
            )
        return _public_session_result(session)
    finally:
        data_upload._discard_session(upload_id)
        data_upload._release_episode_uploads(claimed_episode_keys)


def upload(
    folder,
    *,
    task_id=None,
    scenario=None,
    task_name=None,
    serial_number,
    api_key=None,
    base_url=None,
    wait=False,
    poll_interval=10,
    max_wait=1800,
    max_poll_errors=DEFAULT_POLL_ERROR_LIMIT,
    timeout=60,
    session_timeout=DEFAULT_SESSION_TIMEOUT,
    concurrency=5,
    retries=3,
):
    if not serial_number:
        raise PrismaxValidationError("serial_number is required.")
    client = PrismaXClient(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        session_timeout=session_timeout,
        concurrency=concurrency,
        retries=retries,
    )
    files = scan_folder(folder)
    errors = validate_mcap_mp4(files)
    if errors:
        raise PrismaxValidationError("; ".join(errors))

    task_id = resolve_task_id(client, task_id=task_id, scenario=scenario, task_name=task_name)
    keys = episode_keys(files)
    session = client.create_upload_session(
        task_id=task_id,
        serial_number=serial_number,
        files=_build_files_payload(files, keys),
    )
    resolved_machine_id = session.get("machine_id")
    try:
        _upload_session_files(
            client=client,
            session=session,
            files=files,
            episode_keys_value=keys,
            task_id=task_id,
            machine_id=resolved_machine_id,
        )
    except PrismaxApiError as exc:
        upload_id = session.get("upload_id")
        raise PrismaxApiError(
            f"Upload {upload_id} was created but file upload failed. "
            f"Resume with: prismax resume {upload_id} {folder}. Original error: {exc}"
        ) from exc

    if wait:
        return wait_for_upload(
            session["upload_id"],
            api_key=api_key,
            base_url=base_url,
            poll_interval=poll_interval,
            max_wait=max_wait,
            max_poll_errors=max_poll_errors,
            timeout=timeout,
            retries=retries,
        )
    return _public_session_result(session)


def resume(
    upload_id,
    folder,
    *,
    api_key=None,
    base_url=None,
    wait=False,
    poll_interval=10,
    max_wait=1800,
    max_poll_errors=DEFAULT_POLL_ERROR_LIMIT,
    timeout=60,
    session_timeout=DEFAULT_SESSION_TIMEOUT,
    concurrency=5,
    retries=3,
):
    client = PrismaXClient(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        session_timeout=session_timeout,
        concurrency=concurrency,
        retries=retries,
    )
    files = scan_folder(folder)
    errors = validate_mcap_mp4(files)
    if errors:
        raise PrismaxValidationError("; ".join(errors))

    keys = episode_keys(files)
    session = client.resume_upload_session(
        upload_id=upload_id,
        files=_build_files_payload(files, keys),
    )
    try:
        _upload_session_files(
            client=client,
            session=session,
            files=files,
            episode_keys_value=keys,
            task_id=session.get("task_id"),
            machine_id=session.get("machine_id"),
        )
    except PrismaxApiError as exc:
        raise PrismaxApiError(
            f"Resume for upload {upload_id} failed while uploading files. "
            f"Retry with: prismax resume {upload_id} {folder}. Original error: {exc}"
        ) from exc

    if wait:
        return wait_for_upload(
            upload_id,
            api_key=api_key,
            base_url=base_url,
            poll_interval=poll_interval,
            max_wait=max_wait,
            max_poll_errors=max_poll_errors,
            timeout=timeout,
            retries=retries,
        )
    return _public_session_result(session)


def status(upload_id, *, api_key=None, base_url=None, timeout=60, retries=3):
    client = PrismaXClient(api_key=api_key, base_url=base_url, timeout=timeout, retries=retries)
    return client.get_upload(upload_id)


def recent_uploads(*, limit=10, api_key=None, base_url=None, timeout=60, retries=3):
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise PrismaxValidationError("limit must be an integer between 1 and 100.") from exc
    if limit < 1 or limit > 100:
        raise PrismaxValidationError("limit must be between 1 and 100.")

    client = PrismaXClient(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        retries=retries,
    )
    return client.list_uploads(limit=limit)


def wait_for_upload(
    upload_id,
    *,
    api_key=None,
    base_url=None,
    poll_interval=10,
    max_wait=1800,
    timeout=60,
    retries=3,
    max_poll_errors=DEFAULT_POLL_ERROR_LIMIT,
):
    client = PrismaXClient(api_key=api_key, base_url=base_url, timeout=timeout, retries=retries)
    started_at = time.monotonic()
    last_status = None
    poll_errors = 0
    while True:
        try:
            current = client.get_upload(upload_id)
            poll_errors = 0
            last_status = str(current.get("status") or "").upper()
            if last_status in TERMINAL_STATUSES:
                return current
        except PrismaxApiError as exc:
            poll_errors += 1
            if max_poll_errors is not None and poll_errors >= int(max_poll_errors):
                raise PrismaxApiError(
                    f"Failed to poll upload {upload_id} status after {poll_errors} consecutive errors: {exc}"
                ) from exc
        if max_wait is not None and time.monotonic() - started_at >= int(max_wait):
            raise PrismaxApiError(
                f"Timed out waiting for upload {upload_id} after {int(max_wait)} seconds "
                f"(last status: {last_status or 'unknown'})."
            )
        time.sleep(max(1, int(poll_interval)))


def _get_or_resume_data_session(client, upload_id, data_upload):
    session = data_upload._get_session(upload_id)
    if session is not None and _session_urls_are_fresh(session):
        return session
    session = client.resume_upload_session(
        upload_id=upload_id,
        files=_build_files_payload(data_upload.files, data_upload.episode_keys),
    )
    session = dict(session)
    session.setdefault("upload_id", int(upload_id))
    data_upload._store_session(session)
    return session


def _session_urls_are_fresh(session):
    expires_at = str(session.get("expires_at") or "").strip()
    if not expires_at:
        return True
    try:
        normalized_expires_at = (
            f"{expires_at[:-1]}+00:00" if expires_at.endswith("Z") else expires_at
        )
        expires_at_value = datetime.fromisoformat(normalized_expires_at)
    except ValueError:
        return False
    if expires_at_value.tzinfo is None:
        expires_at_value = expires_at_value.replace(tzinfo=timezone.utc)
    return expires_at_value > datetime.now(timezone.utc) + timedelta(minutes=5)


def _upload_data_session_files(
    *, client, session, files, episode_keys_value, progress
):
    signed_urls = session.get("signed_urls") or []
    signed_url_by_path = {
        item.get("relative_path"): item
        for item in signed_urls
        if item.get("relative_path") and item.get("signed_url")
    }
    local_file_by_relative_path = {item.relative_path: item for item in files}
    raw_uploads = []
    for local_file in files:
        signed_item = signed_url_by_path.get(local_file.relative_path)
        if not signed_item:
            continue
        raw_uploads.append({
            "signed_url": signed_item["signed_url"],
            "relative_path": local_file.relative_path,
            "path": local_file.path,
            "content_type": local_file.content_type,
        })

    pending_manifest_keys = [
        episode_key
        for episode_key in episode_keys_value
        if f"{episode_key}/_MANIFEST.json" in signed_url_by_path
    ]
    reporter = _FileProgress(
        upload_id=session.get("upload_id"),
        total_files=len(files),
        completed_files=len(files) - len(raw_uploads),
        total_episodes=len(episode_keys_value),
        completed_episodes=len(episode_keys_value) - len(pending_manifest_keys),
        enabled=progress,
    )
    client.upload_files(raw_uploads, on_file_complete=reporter.file_completed)

    upload_id = session.get("upload_id")
    for episode_key in pending_manifest_keys:
        manifest_path = f"{episode_key}/_MANIFEST.json"
        payload = build_manifest_payload(
            episode_key=episode_key,
            upload_id=upload_id,
            machine_id=session.get("machine_id"),
            task_id=session.get("task_id"),
            files=list(local_file_by_relative_path.values()),
        )
        client.upload_json_to_signed_url(
            signed_url=signed_url_by_path[manifest_path]["signed_url"],
            payload=payload,
        )
        reporter.episode_completed(episode_key)


def _upload_session_files(*, client, session, files, episode_keys_value, task_id, machine_id):
    signed_urls = session.get("signed_urls") or []
    signed_url_by_path = {
        item.get("relative_path"): item
        for item in signed_urls
        if item.get("relative_path") and item.get("signed_url")
    }

    raw_uploads = []
    local_file_by_relative_path = {item.relative_path: item for item in files}
    for local_file in files:
        signed_item = signed_url_by_path.get(local_file.relative_path)
        if not signed_item:
            continue
        raw_uploads.append({
            "signed_url": signed_item["signed_url"],
            "relative_path": local_file.relative_path,
            "path": local_file.path,
            "content_type": local_file.content_type,
        })
    client.upload_files(raw_uploads)

    upload_id = session.get("upload_id")
    for episode_key in episode_keys_value:
        manifest_path = f"{episode_key}/_MANIFEST.json"
        signed_item = signed_url_by_path.get(manifest_path)
        if not signed_item:
            continue
        payload = build_manifest_payload(
            episode_key=episode_key,
            upload_id=upload_id,
            machine_id=machine_id,
            task_id=task_id,
            files=list(local_file_by_relative_path.values()),
        )
        client.upload_json_to_signed_url(
            signed_url=signed_item["signed_url"],
            payload=payload,
        )


def _public_session_result(session):
    hidden = {"signed_urls"}
    return {
        key: value
        for key, value in session.items()
        if key not in hidden
    }
