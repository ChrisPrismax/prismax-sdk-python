import argparse
import json
import sys

from .errors import PrismaxError
from .client import DEFAULT_SESSION_TIMEOUT
from .data_upload import DataUpload
from .scenarios import list_scenarios
from .upload import (
    create_upload_session,
    recent_uploads,
    resume,
    resume_upload,
    status,
    upload,
    upload_session,
)


def _print_json(payload):
    print(json.dumps(payload, indent=2, default=str))


def _print_summary(payload):
    fields = [
        ("Upload ID", payload.get("upload_id")),
        ("Status", payload.get("status")),
        ("Episodes", payload.get("episode_count")),
        ("Serial number", payload.get("serial_number")),
        ("Created at", payload.get("created_at")),
    ]
    for label, value in fields:
        if value is not None:
            print(f"{label}: {value}")


def _print_lines(values):
    for value in values:
        print(value)


def _print_uploads(uploads):
    for upload_item in uploads:
        values = [
            str(upload_item.get("upload_id") or ""),
            str(upload_item.get("status") or ""),
            str(upload_item.get("created_at") or ""),
            str(upload_item.get("scenario") or ""),
        ]
        print(" | ".join(values))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="prismax")
    subparsers = parser.add_subparsers(dest="command", required=True)

    upload_parser = subparsers.add_parser("upload")
    upload_parser.add_argument("folder")
    upload_parser.add_argument("--task-id", type=int)
    upload_parser.add_argument("--scenario")
    upload_parser.add_argument("--task-name", dest="scenario")
    upload_parser.add_argument("--serial-number", required=True)
    upload_parser.add_argument("--api-key")
    upload_parser.add_argument("--base-url")
    upload_parser.add_argument("--wait", action="store_true")
    upload_parser.add_argument("--max-wait", type=int, default=1800)
    upload_parser.add_argument("--poll-interval", type=int, default=10)
    upload_parser.add_argument("--timeout", type=int, default=60)
    upload_parser.add_argument("--session-timeout", type=int, default=DEFAULT_SESSION_TIMEOUT)
    upload_parser.add_argument("--retries", type=int, default=3)
    upload_parser.add_argument("--max-poll-errors", type=int, default=3)
    upload_parser.add_argument("--concurrency", type=int, default=5)
    upload_parser.add_argument("--json", action="store_true", help="Print the full raw API response.")

    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("upload_id", type=int)
    resume_parser.add_argument("folder", help="The same complete folder used for the original upload.")
    resume_parser.add_argument("--api-key")
    resume_parser.add_argument("--base-url")
    resume_parser.add_argument("--wait", action="store_true")
    resume_parser.add_argument("--max-wait", type=int, default=1800)
    resume_parser.add_argument("--poll-interval", type=int, default=10)
    resume_parser.add_argument("--timeout", type=int, default=60)
    resume_parser.add_argument("--session-timeout", type=int, default=DEFAULT_SESSION_TIMEOUT)
    resume_parser.add_argument("--retries", type=int, default=3)
    resume_parser.add_argument("--max-poll-errors", type=int, default=3)
    resume_parser.add_argument("--concurrency", type=int, default=5)
    resume_parser.add_argument("--json", action="store_true", help="Print the full raw API response.")

    upload_data_parser = subparsers.add_parser(
        "upload-data",
        help="Create and upload a session described by a PrismaX upload JSON file.",
    )
    upload_data_parser.add_argument("spec", help="Path to the PrismaX upload JSON file.")
    upload_data_parser.add_argument("--api-key")
    upload_data_parser.add_argument("--base-url")
    upload_data_parser.add_argument("--wait", action="store_true")
    upload_data_parser.add_argument("--max-wait", type=int, default=1800)
    upload_data_parser.add_argument("--poll-interval", type=int, default=10)
    upload_data_parser.add_argument("--timeout", type=int, default=60)
    upload_data_parser.add_argument(
        "--session-timeout", type=int, default=DEFAULT_SESSION_TIMEOUT
    )
    upload_data_parser.add_argument("--retries", type=int, default=3)
    upload_data_parser.add_argument("--max-poll-errors", type=int, default=3)
    upload_data_parser.add_argument("--concurrency", type=int, default=5)
    upload_data_parser.add_argument("--no-progress", action="store_true")
    upload_data_parser.add_argument("--json", action="store_true", help="Print the full raw API response.")

    resume_data_parser = subparsers.add_parser(
        "resume-data",
        help="Resume a session using its original PrismaX upload JSON file.",
    )
    resume_data_parser.add_argument("upload_id", type=int)
    resume_data_parser.add_argument("spec", help="The same upload JSON file used to create the session.")
    resume_data_parser.add_argument("--api-key")
    resume_data_parser.add_argument("--base-url")
    resume_data_parser.add_argument("--wait", action="store_true")
    resume_data_parser.add_argument("--max-wait", type=int, default=1800)
    resume_data_parser.add_argument("--poll-interval", type=int, default=10)
    resume_data_parser.add_argument("--timeout", type=int, default=60)
    resume_data_parser.add_argument(
        "--session-timeout", type=int, default=DEFAULT_SESSION_TIMEOUT
    )
    resume_data_parser.add_argument("--retries", type=int, default=3)
    resume_data_parser.add_argument("--max-poll-errors", type=int, default=3)
    resume_data_parser.add_argument("--concurrency", type=int, default=5)
    resume_data_parser.add_argument("--no-progress", action="store_true")
    resume_data_parser.add_argument("--json", action="store_true", help="Print the full raw API response.")

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("upload_id", type=int)
    status_parser.add_argument("--api-key")
    status_parser.add_argument("--base-url")
    status_parser.add_argument("--timeout", type=int, default=60)
    status_parser.add_argument("--retries", type=int, default=3)
    status_parser.add_argument("--json", action="store_true", help="Print the full raw API response.")

    uploads_parser = subparsers.add_parser("uploads")
    uploads_parser.add_argument("--limit", type=int, default=10)
    uploads_parser.add_argument("--api-key")
    uploads_parser.add_argument("--base-url")
    uploads_parser.add_argument("--timeout", type=int, default=60)
    uploads_parser.add_argument("--retries", type=int, default=3)
    uploads_parser.add_argument("--json", action="store_true", help="Print the full raw API response.")

    scenarios_parser = subparsers.add_parser("scenarios")
    scenarios_parser.add_argument("--api-key")
    scenarios_parser.add_argument("--base-url")
    scenarios_parser.add_argument("--timeout", type=int, default=60)
    scenarios_parser.add_argument("--json", action="store_true", help="Print the full raw API response.")

    args = parser.parse_args(argv)

    try:
        if args.command == "upload":
            result = upload(
                args.folder,
                task_id=args.task_id,
                scenario=args.scenario,
                serial_number=args.serial_number,
                api_key=args.api_key,
                base_url=args.base_url,
                wait=args.wait,
                poll_interval=args.poll_interval,
                max_wait=args.max_wait,
                max_poll_errors=args.max_poll_errors,
                timeout=args.timeout,
                session_timeout=args.session_timeout,
                concurrency=args.concurrency,
                retries=args.retries,
            )
            if args.json:
                _print_json(result)
            else:
                _print_summary(result)
            return 0

        if args.command == "resume":
            result = resume(
                args.upload_id,
                args.folder,
                api_key=args.api_key,
                base_url=args.base_url,
                wait=args.wait,
                poll_interval=args.poll_interval,
                max_wait=args.max_wait,
                max_poll_errors=args.max_poll_errors,
                timeout=args.timeout,
                session_timeout=args.session_timeout,
                concurrency=args.concurrency,
                retries=args.retries,
            )
            if args.json:
                _print_json(result)
            else:
                _print_summary(result)
            return 0

        if args.command == "upload-data":
            data_upload = DataUpload.from_json(args.spec)
            upload_id = create_upload_session(
                data_upload,
                api_key=args.api_key,
                base_url=args.base_url,
                timeout=args.timeout,
                session_timeout=args.session_timeout,
                concurrency=args.concurrency,
                retries=args.retries,
            )
            print(f"Created upload session: {upload_id}", file=sys.stderr, flush=True)
            result = upload_session(
                upload_id,
                data_upload,
                api_key=args.api_key,
                base_url=args.base_url,
                progress=not args.no_progress and not args.json,
                wait=args.wait,
                poll_interval=args.poll_interval,
                max_wait=args.max_wait,
                max_poll_errors=args.max_poll_errors,
                timeout=args.timeout,
                session_timeout=args.session_timeout,
                concurrency=args.concurrency,
                retries=args.retries,
            )
            if args.json:
                _print_json(result)
            else:
                _print_summary(result)
            return 0

        if args.command == "resume-data":
            data_upload = DataUpload.from_json(args.spec)
            result = resume_upload(
                args.upload_id,
                data_upload,
                api_key=args.api_key,
                base_url=args.base_url,
                progress=not args.no_progress and not args.json,
                wait=args.wait,
                poll_interval=args.poll_interval,
                max_wait=args.max_wait,
                max_poll_errors=args.max_poll_errors,
                timeout=args.timeout,
                session_timeout=args.session_timeout,
                concurrency=args.concurrency,
                retries=args.retries,
            )
            if args.json:
                _print_json(result)
            else:
                _print_summary(result)
            return 0

        if args.command == "status":
            result = status(
                args.upload_id,
                api_key=args.api_key,
                base_url=args.base_url,
                timeout=args.timeout,
                retries=args.retries,
            )
            if args.json:
                _print_json(result)
            else:
                _print_summary(result)
            return 0

        if args.command == "uploads":
            result = recent_uploads(
                limit=args.limit,
                api_key=args.api_key,
                base_url=args.base_url,
                timeout=args.timeout,
                retries=args.retries,
            )
            if args.json:
                _print_json(result)
            else:
                _print_uploads(result)
            return 0

        if args.command == "scenarios":
            result = list_scenarios(
                api_key=args.api_key,
                base_url=args.base_url,
                timeout=args.timeout,
            )
            if args.json:
                _print_json(result)
            else:
                _print_lines(result)
            return 0
    except PrismaxError as exc:
        print(f"prismax: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
