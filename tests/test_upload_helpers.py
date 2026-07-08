import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from prismax.client import PrismaXClient
from prismax.manifest import build_manifest_payload, manifest_placeholder
from prismax.errors import PrismaxApiError, PrismaxAuthError, PrismaxValidationError
from prismax.scanner import episode_keys, scan_folder, select_primary_video_paths, validate_mcap_mp4
from prismax.upload import resolve_task_id, upload, wait_for_upload


class UploadHelperTests(unittest.TestCase):
    def test_scan_validate_and_build_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1").mkdir()
            (root / "1.mcap").write_bytes(b"mcap")
            (root / "1" / "left.mp4").write_bytes(b"left")
            (root / "1" / "right.mp4").write_bytes(b"right")
            (root / "1" / "high.mp4").write_bytes(b"env")
            (root / "1" / "_MANIFEST.json").write_text("ignored")

            files = scan_folder(root)

            self.assertEqual(
                sorted(item.relative_path for item in files),
                ["1.mcap", "1/high.mp4", "1/left.mp4", "1/right.mp4"],
            )
            self.assertEqual(validate_mcap_mp4(files), [])
            self.assertEqual(episode_keys(files), ["1"])
            self.assertEqual(
                manifest_placeholder("1"),
                {
                    "relative_path": "1/_MANIFEST.json",
                    "size_bytes": None,
                    "content_type": "application/json",
                },
            )

            manifest = build_manifest_payload(
                episode_key="1",
                upload_id=123,
                machine_id="machine-1",
                task_id=9,
                files=files,
            )

            self.assertEqual(manifest["manifest_version"], 1)
            self.assertEqual(manifest["upload_id"], 123)
            self.assertEqual(manifest["episode_key"], "1")
            self.assertEqual(len(manifest["files"]), 4)

    def test_scan_skips_hidden_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".DS_Store").write_bytes(b"ignored")
            (root / "1").mkdir()
            (root / "1.mcap").write_bytes(b"mcap")
            (root / "1" / "high.mp4").write_bytes(b"env")
            (root / "1" / "left.mp4").write_bytes(b"left")
            (root / "1" / "right.mp4").write_bytes(b"right")
            (root / "1" / "._left.mp4").write_bytes(b"appledouble")
            (root / "1" / ".hidden.mp4").write_bytes(b"hidden")

            files = scan_folder(root)

            self.assertEqual(
                sorted(item.relative_path for item in files),
                ["1.mcap", "1/high.mp4", "1/left.mp4", "1/right.mp4"],
            )

    def test_validate_accepts_additional_videos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1").mkdir()
            (root / "1.mcap").write_bytes(b"mcap")
            for name in ["high.mp4", "left.mp4", "right.mp4", "high2.mp4", "left2.mp4", "right2.mp4"]:
                (root / "1" / name).write_bytes(name.encode("utf-8"))

            files = scan_folder(root)

            self.assertEqual(validate_mcap_mp4(files), [])
            self.assertEqual(len(build_manifest_payload(
                episode_key="1",
                upload_id=123,
                machine_id="machine-1",
                task_id=9,
                files=files,
            )["files"]), 7)

    def test_select_primary_video_paths_prefers_exact_names(self):
        self.assertEqual(
            select_primary_video_paths([
                "1/high2.mp4",
                "1/high.mp4",
                "1/left2.mp4",
                "1/left.mp4",
                "1/right2.mp4",
                "1/right.mp4",
            ]),
            {
                "env": "1/high.mp4",
                "left": "1/left.mp4",
                "right": "1/right.mp4",
            },
        )

    def test_validate_rejects_missing_mcap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1").mkdir()
            (root / "1" / "left.mp4").write_bytes(b"left")
            (root / "1" / "right.mp4").write_bytes(b"right")
            (root / "1" / "high.mp4").write_bytes(b"env")

            files = scan_folder(root)
            errors = validate_mcap_mp4(files)

            self.assertTrue(any("exactly 1 .mcap" in error for error in errors))

    def test_validate_rejects_missing_primary_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1").mkdir()
            (root / "1.mcap").write_bytes(b"mcap")
            (root / "1" / "high.mp4").write_bytes(b"env")
            (root / "1" / "left.mp4").write_bytes(b"left")
            (root / "1" / "left2.mp4").write_bytes(b"left2")

            files = scan_folder(root)
            errors = validate_mcap_mp4(files)

            self.assertTrue(any("missing: right" in error for error in errors))

    def test_validate_rejects_uppercase_mp4_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1").mkdir()
            (root / "1.mcap").write_bytes(b"mcap")
            (root / "1" / "high.mp4").write_bytes(b"env")
            (root / "1" / "left.MP4").write_bytes(b"left")
            (root / "1" / "right.mp4").write_bytes(b"right")

            files = scan_folder(root)
            errors = validate_mcap_mp4(files)

            self.assertTrue(any("Only .mp4 files are allowed" in error for error in errors))

    def test_validate_rejects_nested_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1" / "nested").mkdir(parents=True)
            (root / "1.mcap").write_bytes(b"mcap")
            (root / "1" / "nested" / "left.mp4").write_bytes(b"left")

            files = scan_folder(root)
            errors = validate_mcap_mp4(files)

            self.assertTrue(any("Nested folders are not allowed" in error for error in errors))

    def test_wait_for_upload_times_out(self):
        mock_client = Mock()
        mock_client.get_upload.return_value = {"upload_id": 123, "status": "UPLOADING"}
        upload_module = importlib.import_module("prismax.upload")

        with patch.object(upload_module, "PrismaXClient", return_value=mock_client):
            with self.assertRaises(PrismaxApiError):
                wait_for_upload(
                    123,
                    api_key="test",
                    poll_interval=1,
                    max_wait=0,
                )

    def test_wait_for_upload_accepts_partially_ready_terminal_status(self):
        mock_client = Mock()
        mock_client.get_upload.return_value = {
            "upload_id": 123,
            "status": "DERIVED_PARTIALLY_READY",
        }
        upload_module = importlib.import_module("prismax.upload")

        with patch.object(upload_module, "PrismaXClient", return_value=mock_client):
            result = wait_for_upload(123, api_key="test", poll_interval=1, max_wait=10)

        self.assertEqual(result["status"], "DERIVED_PARTIALLY_READY")

    def test_wait_for_upload_tolerates_transient_poll_errors(self):
        mock_client = Mock()
        mock_client.get_upload.side_effect = [
            PrismaxApiError("bad gateway"),
            {"upload_id": 123, "status": "DERIVED_READY"},
        ]
        upload_module = importlib.import_module("prismax.upload")

        with patch.object(upload_module, "PrismaXClient", return_value=mock_client):
            result = wait_for_upload(
                123,
                api_key="test",
                poll_interval=1,
                max_wait=10,
                max_poll_errors=3,
            )

        self.assertEqual(result["status"], "DERIVED_READY")

    def test_wait_for_upload_raises_after_consecutive_poll_errors(self):
        mock_client = Mock()
        mock_client.get_upload.side_effect = [
            PrismaxApiError("bad gateway"),
            PrismaxApiError("bad gateway again"),
        ]
        upload_module = importlib.import_module("prismax.upload")

        with patch.object(upload_module, "PrismaXClient", return_value=mock_client):
            with self.assertRaises(PrismaxApiError) as ctx:
                wait_for_upload(
                    123,
                    api_key="test",
                    poll_interval=1,
                    max_wait=10,
                    max_poll_errors=2,
                )

        self.assertIn("2 consecutive errors", str(ctx.exception))

    def test_create_upload_session_sends_serial_number(self):
        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {"success": True, "data": {"upload_id": 1}}

        with patch("prismax.client.requests.request", return_value=mock_response) as request_mock:
            client = PrismaXClient(api_key="pxu_test", base_url="https://example.test")
            client.create_upload_session(
                task_id=12,
                serial_number="MD100101000019205Z00082",
                files=[],
            )

        request_mock.assert_called_once()
        _, _, kwargs = request_mock.mock_calls[0]
        self.assertEqual(kwargs["json"]["serial_number"], "MD100101000019205Z00082")
        self.assertNotIn("machine_id", kwargs["json"])
        self.assertRegex(kwargs["headers"]["User-Agent"], r"^prismax-sdk/")

    def test_client_wraps_request_exceptions(self):
        client = PrismaXClient(api_key="pxu_test", base_url="https://example.test")

        with patch("prismax.client.requests.request", side_effect=requests.Timeout("timed out")):
            with self.assertRaises(PrismaxApiError) as ctx:
                client.get_upload(123)

        self.assertIn("PrismaX API request failed", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, requests.Timeout)

    def test_client_raises_auth_error_for_unauthorized_responses(self):
        mock_response = Mock()
        mock_response.ok = False
        mock_response.status_code = 403
        mock_response.json.return_value = {"success": False, "msg": "forbidden"}

        with patch("prismax.client.requests.request", return_value=mock_response):
            client = PrismaXClient(api_key="pxu_test", base_url="https://example.test")
            with self.assertRaises(PrismaxAuthError):
                client.get_upload(123)

    def test_upload_file_error_includes_relative_path(self):
        mock_response = Mock()
        mock_response.ok = False
        mock_response.status_code = 500
        mock_response.text = "broken"

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "left.mp4"
            path.write_bytes(b"video")
            with patch("prismax.client.requests.put", return_value=mock_response):
                client = PrismaXClient(api_key="pxu_test", base_url="https://example.test", retries=1)
                with self.assertRaises(PrismaxApiError) as ctx:
                    client.upload_file_to_signed_url(
                        signed_url="https://storage.example.test/upload",
                        path=path,
                        content_type="video/mp4",
                        relative_path="1/left.mp4",
                    )

        self.assertIn("1/left.mp4", str(ctx.exception))

    def test_upload_failure_mentions_resume_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1").mkdir()
            (root / "1.mcap").write_bytes(b"mcap")
            (root / "1" / "high.mp4").write_bytes(b"env")
            (root / "1" / "left.mp4").write_bytes(b"left")
            (root / "1" / "right.mp4").write_bytes(b"right")

            mock_client = Mock()
            mock_client.create_upload_session.return_value = {
                "upload_id": 999,
                "machine_id": "machine-1",
                "signed_urls": [
                    {
                        "relative_path": "1/high.mp4",
                        "signed_url": "https://storage.example.test/high",
                    },
                ],
            }
            mock_client.upload_files.side_effect = PrismaxApiError("Failed to upload 1/high.mp4")
            upload_module = importlib.import_module("prismax.upload")

            with patch.object(upload_module, "PrismaXClient", return_value=mock_client):
                with self.assertRaises(PrismaxApiError) as ctx:
                    upload(root, task_id=12, serial_number="serial", api_key="pxu_test")

        self.assertIn("Upload 999 was created", str(ctx.exception))
        self.assertIn("prismax resume 999", str(ctx.exception))

    def test_resolve_task_id_accepts_task_id(self):
        mock_client = Mock()

        self.assertEqual(resolve_task_id(mock_client, task_id=12), 12)
        mock_client.list_tasks.assert_not_called()

    def test_resolve_task_id_matches_scenario_case_insensitive(self):
        mock_client = Mock()
        mock_client.list_tasks.return_value = [
            {"task_id": 7, "scenario": "Pick and place packaged food items"},
        ]

        self.assertEqual(
            resolve_task_id(mock_client, scenario="pick AND place packaged FOOD items"),
            7,
        )

    def test_resolve_task_id_missing_scenario_reports_task_name(self):
        mock_client = Mock()
        mock_client.list_tasks.return_value = [
            {"task_id": 7, "scenario": "Pick and place packaged food items"},
        ]

        with self.assertRaises(PrismaxValidationError) as ctx:
            resolve_task_id(mock_client, scenario="missing task")

        self.assertIn("No task found for scenario/task name", str(ctx.exception))
        self.assertIn("Pick and place packaged food items", str(ctx.exception))

    def test_upload_resolves_scenario_before_creating_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1").mkdir()
            (root / "1.mcap").write_bytes(b"mcap")
            (root / "1" / "high.mp4").write_bytes(b"env")
            (root / "1" / "left.mp4").write_bytes(b"left")
            (root / "1" / "right.mp4").write_bytes(b"right")

            mock_client = Mock()
            mock_client.list_tasks.return_value = [
                {"task_id": 12, "scenario": "Pick and place packaged food items"},
            ]
            mock_client.create_upload_session.return_value = {
                "upload_id": 999,
                "machine_id": "machine-1",
                "signed_urls": [],
            }
            upload_module = importlib.import_module("prismax.upload")

            with patch.object(upload_module, "PrismaXClient", return_value=mock_client):
                upload(root, scenario="pick and place packaged food items", serial_number="serial", api_key="pxu_test")

        mock_client.create_upload_session.assert_called_once()
        self.assertEqual(mock_client.create_upload_session.call_args.kwargs["task_id"], 12)

    def test_base_url_rejects_remote_http(self):
        with self.assertRaises(PrismaxValidationError):
            PrismaXClient(api_key="pxu_test", base_url="http://evil.example.com")

    def test_base_url_allows_localhost_http(self):
        client = PrismaXClient(api_key="pxu_test", base_url="http://127.0.0.1:8082")
        self.assertEqual(client.base_url, "http://127.0.0.1:8082")

    def test_base_url_allows_https(self):
        client = PrismaXClient(api_key="pxu_test", base_url="https://data.prismaxserver.com")
        self.assertEqual(client.base_url, "https://data.prismaxserver.com")

    def test_default_base_url_uses_beta_and_env_var_is_not_honored(self):
        with patch.dict(os.environ, {"PRISMAX_BASE_URL": "http://evil.example.com"}, clear=False):
            client = PrismaXClient(api_key="pxu_test")
        self.assertEqual(client.base_url, "https://app-prismax-data-pipeline-beta-1053158761087.us-west1.run.app")


if __name__ == "__main__":
    unittest.main()
