import inspect
import importlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from prismax.data_upload import DataUpload
from prismax.errors import PrismaxValidationError
from prismax import cli
from prismax.upload import (
    create_upload_session,
    resume_upload,
    upload_episode,
    upload_session,
)


upload_module = importlib.import_module("prismax.upload")


def _write_episode(root, key, *, extra_names=()):
    (root / key).mkdir(parents=True, exist_ok=True)
    (root / f"{key}.mcap").write_bytes(b"mcap")
    for name in ("high.mp4", "left.mp4", "right.mp4", *extra_names):
        (root / key / name).write_bytes(name.encode("utf-8"))


def _templated_spec(keys):
    return {
        "format_version": 1,
        "scenario": "Put away messy clothes",
        "robot": {"serial_number": "MD100101000019205Z00082"},
        "episode_set": {
            "mode": "templated",
            "episode_keys": keys,
            "file_layout": {
                "mcap": {"source_path": "{episode_key}.mcap"},
                "primary_videos": {
                    "env": {"source_path": "{episode_key}/high.mp4"},
                    "left": {"source_path": "{episode_key}/left.mp4"},
                    "right": {"source_path": "{episode_key}/right.mp4"},
                },
                "additional_videos": {
                    "source_glob": "{episode_key}/*.mp4"
                },
            },
        },
    }


class DataUploadTests(unittest.TestCase):
    def test_templated_spec_maps_primary_and_additional_videos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1", extra_names=("high2.mp4", "left2.mp4"))
            spec_path = root / "prismax_upload.json"
            spec_path.write_text(json.dumps(_templated_spec(["episode_1"])))

            data_upload = DataUpload.from_json(spec_path)

            self.assertEqual(data_upload.scenario, "Put away messy clothes")
            self.assertEqual(data_upload.episode_keys, ["episode_1"])
            self.assertEqual(
                [item.relative_path for item in data_upload.files],
                [
                    "episode_1.mcap",
                    "episode_1/high.mp4",
                    "episode_1/high2.mp4",
                    "episode_1/left.mp4",
                    "episode_1/left2.mp4",
                    "episode_1/right.mp4",
                ],
            )

    def test_explicit_spec_supports_arbitrary_source_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "capture" / "videos").mkdir(parents=True)
            (root / "capture" / "recording.mcap").write_bytes(b"mcap")
            for name in ("camera.mp4", "hand_l.mp4", "hand_r.mp4", "camera2.mp4"):
                (root / "capture" / "videos" / name).write_bytes(b"video")
            payload = {
                "format_version": 1,
                "scenario": "Put away messy clothes",
                "robot": {"serial_number": "MD100101000019205Z00082"},
                "episode_set": {
                    "mode": "explicit",
                    "episodes": [{
                        "episode_key": "episode_1",
                        "assets": {
                            "mcap": {"source_path": "capture/recording.mcap"},
                            "primary_videos": {
                                "env": {"source_path": "capture/videos/camera.mp4"},
                                "left": {"source_path": "capture/videos/hand_l.mp4"},
                                "right": {"source_path": "capture/videos/hand_r.mp4"},
                            },
                            "additional_videos": [
                                {"source_path": "capture/videos/camera2.mp4"}
                            ],
                        },
                    }],
                },
            }

            data_upload = DataUpload.from_dict(payload, base_path=root)

            self.assertEqual(
                [item.relative_path for item in data_upload.files],
                [
                    "episode_1.mcap",
                    "episode_1/camera2.mp4",
                    "episode_1/high.mp4",
                    "episode_1/left.mp4",
                    "episode_1/right.mp4",
                ],
            )

    def test_explicit_spec_rejects_case_insensitive_destination_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1")
            (root / "extras").mkdir()
            (root / "extras" / "LEFT.mp4").write_bytes(b"extra")
            spec = {
                "scenario": "Put away messy clothes",
                "robot": {"serial_number": "MD100101000019205Z00082"},
                "episode_set": {
                    "mode": "explicit",
                    "episodes": [{
                        "episode_key": "episode_1",
                        "assets": {
                            "mcap": {"source_path": "episode_1.mcap"},
                            "primary_videos": {
                                "env": {"source_path": "episode_1/high.mp4"},
                                "left": {"source_path": "episode_1/left.mp4"},
                                "right": {"source_path": "episode_1/right.mp4"},
                            },
                            "additional_videos": [{
                                "source_path": "extras/LEFT.mp4"
                            }],
                        },
                    }],
                },
            }

            with self.assertRaises(PrismaxValidationError) as ctx:
                DataUpload.from_dict(spec, base_path=root)

            self.assertIn("regardless of letter case", str(ctx.exception))

    def test_upload_spec_rejects_symbolic_link_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1")
            target = root / "episode_1" / "high.mp4"
            link = root / "episode_1" / "high-link.mp4"
            link.symlink_to(target)
            spec = _templated_spec(["episode_1"])
            spec["episode_set"]["file_layout"]["primary_videos"]["env"] = {
                "source_path": "episode_1/high-link.mp4"
            }

            with self.assertRaises(PrismaxValidationError) as ctx:
                DataUpload.from_dict(spec, base_path=root)

            self.assertIn("symbolic link", str(ctx.exception))

    def test_collection_date_is_not_part_of_version_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1")
            payload = _templated_spec(["episode_1"])
            payload["collection_date"] = "2026-07-15"

            with self.assertRaises(PrismaxValidationError) as ctx:
                DataUpload.from_dict(payload, base_path=root)

        self.assertIn("collection_date", str(ctx.exception))

    def test_format_version_is_optional(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1")
            payload = _templated_spec(["episode_1"])
            payload.pop("format_version")

            data_upload = DataUpload.from_dict(payload, base_path=root)

        self.assertEqual(data_upload.episode_keys, ["episode_1"])

    def test_rejects_duplicate_additional_destination_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            (root / "b").mkdir()
            _write_episode(root, "episode_1")
            (root / "a" / "extra.mp4").write_bytes(b"a")
            (root / "b" / "extra.mp4").write_bytes(b"b")
            payload = _templated_spec(["episode_1"])
            payload["episode_set"]["mode"] = "explicit"
            payload["episode_set"].pop("episode_keys")
            payload["episode_set"].pop("file_layout")
            payload["episode_set"]["episodes"] = [{
                "episode_key": "episode_1",
                "assets": {
                    "mcap": {"source_path": "episode_1.mcap"},
                    "primary_videos": {
                        "env": {"source_path": "episode_1/high.mp4"},
                        "left": {"source_path": "episode_1/left.mp4"},
                        "right": {"source_path": "episode_1/right.mp4"},
                    },
                    "additional_videos": [
                        {"source_path": "a/extra.mp4"},
                        {"source_path": "b/extra.mp4"},
                    ],
                },
            }]

            with self.assertRaises(PrismaxValidationError) as ctx:
                DataUpload.from_dict(payload, base_path=root)

        self.assertIn("same upload path", str(ctx.exception))

    def test_explicit_spec_rejects_primary_repeated_as_additional(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1")
            payload = {
                "format_version": 1,
                "scenario": "Put away messy clothes",
                "robot": {"serial_number": "MD100101000019205Z00082"},
                "episode_set": {
                    "mode": "explicit",
                    "episodes": [{
                        "episode_key": "episode_1",
                        "assets": {
                            "mcap": {"source_path": "episode_1.mcap"},
                            "primary_videos": {
                                "env": {"source_path": "episode_1/high.mp4"},
                                "left": {"source_path": "episode_1/left.mp4"},
                                "right": {"source_path": "episode_1/right.mp4"},
                            },
                            "additional_videos": [
                                {"source_path": "episode_1/high.mp4"}
                            ],
                        },
                    }],
                },
            }

            with self.assertRaises(PrismaxValidationError) as ctx:
                DataUpload.from_dict(payload, base_path=root)

        self.assertIn("repeats a primary source", str(ctx.exception))

    def test_rejects_source_path_outside_spec_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1")
            payload = _templated_spec(["episode_1"])
            payload["episode_set"]["file_layout"]["mcap"]["source_path"] = "../outside.mcap"

            with self.assertRaises(PrismaxValidationError) as ctx:
                DataUpload.from_dict(payload, base_path=root)

        self.assertIn("relative path", str(ctx.exception))

    def test_rejects_episode_key_with_glob_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = _templated_spec(["episode_*"])

            with self.assertRaises(PrismaxValidationError) as ctx:
                DataUpload.from_dict(payload, base_path=root)

        self.assertIn("Invalid episode_key", str(ctx.exception))

    def test_session_api_uses_one_upload_id_and_uploads_manifests_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1", extra_names=("high2.mp4",))
            data_upload = DataUpload.from_dict(_templated_spec(["episode_1"]), base_path=root)
            signed_urls = [
                {
                    "relative_path": item.relative_path,
                    "signed_url": f"https://storage.test/{item.relative_path}",
                }
                for item in data_upload.files
            ]
            signed_urls.append({
                "relative_path": "episode_1/_MANIFEST.json",
                "signed_url": "https://storage.test/manifest",
            })
            client = Mock()
            client.list_tasks.return_value = [{
                "task_id": 12,
                "scenario": "Put away messy clothes",
            }]
            client.create_upload_session.return_value = {
                "upload_id": 456,
                "machine_id": "machine-1",
                "task_id": 12,
                "signed_urls": signed_urls,
            }

            def upload_files(items, on_file_complete=None):
                for item in items:
                    if on_file_complete:
                        on_file_complete(item)

            client.upload_files.side_effect = upload_files

            with patch.object(upload_module, "PrismaXClient", return_value=client):
                upload_id = create_upload_session(data_upload, api_key="pxu_test")
                result = upload_session(
                    upload_id,
                    data_upload,
                    api_key="pxu_test",
                    progress=False,
                )

            self.assertEqual(upload_id, 456)
            self.assertEqual(result["upload_id"], 456)
            self.assertEqual(len(client.upload_files.call_args.args[0]), 5)
            client.upload_json_to_signed_url.assert_called_once()

    def test_create_upload_session_accepts_task_id_without_task_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1")
            data_upload = DataUpload.from_dict(
                _templated_spec(["episode_1"]), base_path=root
            )
            client = Mock()
            client.create_upload_session.return_value = {
                "upload_id": 456,
                "task_id": 99,
                "signed_urls": [],
            }

            with patch.object(upload_module, "PrismaXClient", return_value=client):
                upload_id = create_upload_session(
                    data_upload,
                    task_id=99,
                    api_key="pxu_test",
                )

            self.assertEqual(upload_id, 456)
            client.list_tasks.assert_not_called()
            self.assertEqual(
                client.create_upload_session.call_args.kwargs["task_id"], 99
            )

    def test_upload_episode_only_transfers_requested_episode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1")
            _write_episode(root, "episode_2")
            data_upload = DataUpload.from_dict(
                _templated_spec(["episode_1", "episode_2"]), base_path=root
            )
            session = {
                "upload_id": 456,
                "machine_id": "machine-1",
                "task_id": 12,
                "signed_urls": [
                    {
                        "relative_path": item.relative_path,
                        "signed_url": f"https://storage.test/{item.relative_path}",
                    }
                    for item in data_upload.files
                ] + [{
                    "relative_path": "episode_1/_MANIFEST.json",
                    "signed_url": "https://storage.test/manifest-1",
                }],
            }
            data_upload._store_session(session)
            client = Mock()

            with patch.object(upload_module, "PrismaXClient", return_value=client):
                upload_episode(
                    456,
                    "episode_1",
                    data_upload,
                    api_key="pxu_test",
                    progress=False,
                )

            uploaded = client.upload_files.call_args.args[0]
            self.assertEqual(len(uploaded), 4)
            self.assertTrue(all(item["relative_path"].startswith("episode_1") for item in uploaded))
            client.upload_json_to_signed_url.assert_called_once()

    def test_upload_episode_refreshes_expired_session_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1")
            data_upload = DataUpload.from_dict(_templated_spec(["episode_1"]), base_path=root)
            data_upload._store_session({
                "upload_id": 456,
                "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                "signed_urls": [],
            })
            client = Mock()
            client.resume_upload_session.return_value = {
                "upload_id": 456,
                "machine_id": "machine-1",
                "task_id": 12,
                "signed_urls": [],
            }

            with patch.object(upload_module, "PrismaXClient", return_value=client):
                upload_episode(
                    456,
                    "episode_1",
                    data_upload,
                    api_key="pxu_test",
                    progress=False,
                )

            client.resume_upload_session.assert_called_once()

    def test_repeated_upload_episode_uses_resume_instead_of_cached_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1")
            data_upload = DataUpload.from_dict(
                _templated_spec(["episode_1"]), base_path=root
            )
            session = {
                "upload_id": 456,
                "machine_id": "machine-1",
                "task_id": 12,
                "signed_urls": [
                    {
                        "relative_path": item.relative_path,
                        "signed_url": f"https://storage.test/{item.relative_path}",
                    }
                    for item in data_upload.files
                ] + [{
                    "relative_path": "episode_1/_MANIFEST.json",
                    "signed_url": "https://storage.test/manifest",
                }],
            }
            data_upload._store_session(session)
            client = Mock()
            client.resume_upload_session.return_value = {
                "upload_id": 456,
                "machine_id": "machine-1",
                "task_id": 12,
                "signed_urls": [],
            }

            with patch.object(upload_module, "PrismaXClient", return_value=client):
                upload_episode(
                    456,
                    "episode_1",
                    data_upload,
                    api_key="pxu_test",
                    progress=False,
                )
                upload_episode(
                    456,
                    "episode_1",
                    data_upload,
                    api_key="pxu_test",
                    progress=False,
                )

            client.resume_upload_session.assert_called_once()
            client.upload_json_to_signed_url.assert_called_once()

    def test_same_episode_cannot_be_claimed_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1")
            data_upload = DataUpload.from_dict(
                _templated_spec(["episode_1"]), base_path=root
            )
            data_upload._claim_episode_uploads(["episode_1"])
            try:
                with self.assertRaises(PrismaxValidationError) as ctx:
                    data_upload._claim_episode_uploads(["episode_1"])
            finally:
                data_upload._release_episode_uploads(["episode_1"])

            self.assertIn("already being uploaded", str(ctx.exception))

    def test_resume_upload_refreshes_signed_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_episode(root, "episode_1")
            data_upload = DataUpload.from_dict(_templated_spec(["episode_1"]), base_path=root)
            client = Mock()
            client.resume_upload_session.return_value = {
                "upload_id": 456,
                "machine_id": "machine-1",
                "task_id": 12,
                "signed_urls": [],
            }

            with patch.object(upload_module, "PrismaXClient", return_value=client):
                resume_upload(
                    456,
                    data_upload,
                    api_key="pxu_test",
                    progress=False,
                )

            client.resume_upload_session.assert_called_once()

    def test_progress_is_enabled_by_default(self):
        self.assertTrue(inspect.signature(upload_session).parameters["progress"].default)
        self.assertTrue(inspect.signature(upload_episode).parameters["progress"].default)
        self.assertTrue(inspect.signature(resume_upload).parameters["progress"].default)

    def test_cli_prints_upload_id_before_session_upload(self):
        data_upload = Mock()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(cli.DataUpload, "from_json", return_value=data_upload), patch.object(
            cli, "create_upload_session", return_value=456
        ), patch.object(
            cli,
            "upload_session",
            return_value={"upload_id": 456, "status": "UPLOADING"},
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = cli.main(["upload-data", "prismax_upload.json"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Created upload session: 456", stderr.getvalue())
        self.assertIn("Upload ID: 456", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
