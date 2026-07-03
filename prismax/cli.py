import argparse
import json

from .upload import resume, status, upload


def _print_json(payload):
    print(json.dumps(payload, indent=2, default=str))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="prismax")
    subparsers = parser.add_subparsers(dest="command", required=True)

    upload_parser = subparsers.add_parser("upload")
    upload_parser.add_argument("folder")
    upload_parser.add_argument("--task-id", required=True, type=int)
    upload_parser.add_argument("--serial-number", required=True)
    upload_parser.add_argument("--api-key")
    upload_parser.add_argument("--base-url")
    upload_parser.add_argument("--wait", action="store_true")
    upload_parser.add_argument("--max-wait", type=int, default=1800)
    upload_parser.add_argument("--concurrency", type=int, default=5)

    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("upload_id")
    resume_parser.add_argument("folder")
    resume_parser.add_argument("--api-key")
    resume_parser.add_argument("--base-url")
    resume_parser.add_argument("--wait", action="store_true")
    resume_parser.add_argument("--max-wait", type=int, default=1800)
    resume_parser.add_argument("--concurrency", type=int, default=5)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("upload_id")
    status_parser.add_argument("--api-key")
    status_parser.add_argument("--base-url")

    args = parser.parse_args(argv)

    if args.command == "upload":
        result = upload(
            args.folder,
            task_id=args.task_id,
            serial_number=args.serial_number,
            api_key=args.api_key,
            base_url=args.base_url,
            wait=args.wait,
            max_wait=args.max_wait,
            concurrency=args.concurrency,
        )
        _print_json(result)
        return 0

    if args.command == "resume":
        result = resume(
            args.upload_id,
            args.folder,
            api_key=args.api_key,
            base_url=args.base_url,
            wait=args.wait,
            max_wait=args.max_wait,
            concurrency=args.concurrency,
        )
        _print_json(result)
        return 0

    if args.command == "status":
        _print_json(status(args.upload_id, api_key=args.api_key, base_url=args.base_url))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
