import json
import re
import threading
from pathlib import Path, PurePosixPath

from .errors import PrismaxValidationError
from .scanner import LocalFile, _content_type, _is_hidden_path


FORMAT_VERSION = 1
MAX_FILES_PER_UPLOAD = 2000
PRIMARY_DESTINATIONS = {
    "env": "high.mp4",
    "left": "left.mp4",
    "right": "right.mp4",
}
EPISODE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _require_mapping(value, label):
    if not isinstance(value, dict):
        raise PrismaxValidationError(f"{label} must be an object.")
    return value


def _require_only_keys(value, allowed, label):
    unexpected = sorted(set(value) - set(allowed))
    if unexpected:
        raise PrismaxValidationError(
            f"{label} contains unsupported fields: {', '.join(unexpected)}."
        )


def _require_string(value, label):
    normalized = str(value or "").strip()
    if not normalized:
        raise PrismaxValidationError(f"{label} is required.")
    return normalized


def _validate_episode_key(value):
    episode_key = _require_string(value, "episode_key")
    if (
        not EPISODE_KEY_PATTERN.fullmatch(episode_key)
        or episode_key.endswith(".mcap")
    ):
        raise PrismaxValidationError(
            f"Invalid episode_key {episode_key!r}. Use letters, numbers, dots, "
            "underscores, or hyphens, starting with a letter or number."
        )
    return episode_key


def _source_path_from_asset(asset, label):
    asset = _require_mapping(asset, label)
    _require_only_keys(asset, {"source_path"}, label)
    return _require_string(asset.get("source_path"), f"{label}.source_path")


class DataUpload:
    """Validated description of one fixed PrismaX upload batch."""

    def __init__(self, spec, *, base_path="."):
        self._spec = _require_mapping(spec, "upload spec")
        self._base_path = Path(base_path).expanduser().resolve()
        if not self._base_path.exists() or not self._base_path.is_dir():
            raise PrismaxValidationError(
                f"Upload spec base_path must be an existing folder: {self._base_path}"
            )
        self._sessions = {}
        self._active_episode_keys = set()
        self._state_lock = threading.Lock()
        self._parse()

    @classmethod
    def from_json(cls, path):
        json_path = Path(path).expanduser().resolve()
        if not json_path.exists() or not json_path.is_file():
            raise PrismaxValidationError(f"Upload spec does not exist: {json_path}")
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PrismaxValidationError(
                f"Unable to read upload spec {json_path}: {exc}"
            ) from exc
        return cls(payload, base_path=json_path.parent)

    @classmethod
    def from_dict(cls, payload, *, base_path="."):
        return cls(payload, base_path=base_path)

    @property
    def scenario(self):
        return self._scenario

    @property
    def serial_number(self):
        return self._serial_number

    @property
    def episode_keys(self):
        return list(self._episode_keys)

    @property
    def files(self):
        return list(self._files)

    def files_for_episode(self, episode_key):
        episode_key = _validate_episode_key(episode_key)
        if episode_key not in self._episode_keys:
            raise PrismaxValidationError(
                f"Episode {episode_key!r} is not declared in this DataUpload."
            )
        return [
            item
            for item in self._files
            if item.relative_path == f"{episode_key}.mcap"
            or item.relative_path.startswith(f"{episode_key}/")
        ]

    def _store_session(self, session):
        upload_id = session.get("upload_id")
        if upload_id is not None:
            with self._state_lock:
                self._sessions[int(upload_id)] = session

    def _get_session(self, upload_id):
        with self._state_lock:
            return self._sessions.get(int(upload_id))

    def _discard_session(self, upload_id):
        with self._state_lock:
            self._sessions.pop(int(upload_id), None)

    def _claim_episode_uploads(self, episode_keys):
        episode_keys = set(episode_keys)
        with self._state_lock:
            conflicts = sorted(episode_keys & self._active_episode_keys)
            if conflicts:
                raise PrismaxValidationError(
                    "These episodes are already being uploaded by this process: "
                    f"{', '.join(conflicts)}."
                )
            self._active_episode_keys.update(episode_keys)

    def _release_episode_uploads(self, episode_keys):
        with self._state_lock:
            self._active_episode_keys.difference_update(episode_keys)

    def _parse(self):
        _require_only_keys(
            self._spec,
            {"format_version", "scenario", "robot", "episode_set"},
            "upload spec",
        )
        format_version = self._spec.get("format_version", FORMAT_VERSION)
        if format_version != FORMAT_VERSION:
            raise PrismaxValidationError(
                f"format_version must be {FORMAT_VERSION}."
            )
        self._scenario = _require_string(self._spec.get("scenario"), "scenario")

        robot = _require_mapping(self._spec.get("robot"), "robot")
        _require_only_keys(robot, {"serial_number"}, "robot")
        self._serial_number = _require_string(
            robot.get("serial_number"), "robot.serial_number"
        )

        episode_set = _require_mapping(self._spec.get("episode_set"), "episode_set")
        mode = _require_string(episode_set.get("mode"), "episode_set.mode").lower()
        if mode == "templated":
            files_by_episode = self._parse_templated(episode_set)
        elif mode == "explicit":
            files_by_episode = self._parse_explicit(episode_set)
        else:
            raise PrismaxValidationError(
                "episode_set.mode must be either 'templated' or 'explicit'."
            )

        source_owners = {}
        relative_paths = {}
        files = []
        for episode_key, episode_files in files_by_episode:
            for source_path, relative_path in episode_files:
                resolved_source = str(source_path.resolve())
                previous_owner = source_owners.get(resolved_source)
                if previous_owner is not None:
                    raise PrismaxValidationError(
                        f"Source file is assigned more than once: {source_path} "
                        f"({previous_owner} and {relative_path})."
                    )
                source_owners[resolved_source] = relative_path
                normalized_relative_path = relative_path.casefold()
                previous_path = relative_paths.get(normalized_relative_path)
                if previous_path is not None:
                    if previous_path == relative_path:
                        raise PrismaxValidationError(
                            f"Multiple files map to the same upload path: {relative_path}."
                        )
                    raise PrismaxValidationError(
                        "Upload paths must be unique regardless of letter case: "
                        f"{previous_path} and {relative_path}."
                    )
                relative_paths[normalized_relative_path] = relative_path
                files.append(LocalFile(
                    relative_path=relative_path,
                    path=source_path,
                    size_bytes=source_path.stat().st_size,
                    content_type=_content_type(source_path),
                ))

        self._episode_keys = tuple(episode_key for episode_key, _ in files_by_episode)
        self._files = tuple(sorted(files, key=lambda item: item.relative_path))
        total_entries = len(self._files) + len(self._episode_keys)
        if total_entries > MAX_FILES_PER_UPLOAD:
            raise PrismaxValidationError(
                f"Upload contains {total_entries} files including generated manifests; "
                f"the maximum is {MAX_FILES_PER_UPLOAD}."
            )

    def _parse_templated(self, episode_set):
        _require_only_keys(
            episode_set,
            {"mode", "episode_keys", "file_layout"},
            "episode_set",
        )
        raw_keys = episode_set.get("episode_keys")
        if not isinstance(raw_keys, list) or not raw_keys:
            raise PrismaxValidationError(
                "episode_set.episode_keys must be a non-empty list."
            )
        keys = [_validate_episode_key(value) for value in raw_keys]
        self._validate_unique_episode_keys(keys)

        layout = _require_mapping(episode_set.get("file_layout"), "file_layout")
        _require_only_keys(
            layout,
            {"mcap", "primary_videos", "additional_videos"},
            "file_layout",
        )
        mcap_template = _source_path_from_asset(layout.get("mcap"), "file_layout.mcap")
        primary = self._parse_primary_assets(
            layout.get("primary_videos"), "file_layout.primary_videos"
        )
        additional = layout.get("additional_videos")
        additional_pattern = None
        if additional is not None:
            additional = _require_mapping(additional, "file_layout.additional_videos")
            _require_only_keys(
                additional, {"source_glob"}, "file_layout.additional_videos"
            )
            additional_pattern = _require_string(
                additional.get("source_glob"),
                "file_layout.additional_videos.source_glob",
            )

        result = []
        for episode_key in keys:
            mcap_path = self._resolve_source(
                self._render(mcap_template, episode_key),
                f"episode {episode_key} mcap",
                ".mcap",
            )
            primary_paths = {
                slot: self._resolve_source(
                    self._render(template, episode_key),
                    f"episode {episode_key} primary {slot}",
                    ".mp4",
                )
                for slot, template in primary.items()
            }
            additional_paths = []
            if additional_pattern:
                additional_paths = self._expand_glob(
                    self._render(additional_pattern, episode_key), episode_key
                )
            result.append((
                episode_key,
                self._build_episode_files(
                    episode_key, mcap_path, primary_paths, additional_paths
                ),
            ))
        return result

    def _parse_explicit(self, episode_set):
        _require_only_keys(episode_set, {"mode", "episodes"}, "episode_set")
        raw_episodes = episode_set.get("episodes")
        if not isinstance(raw_episodes, list) or not raw_episodes:
            raise PrismaxValidationError(
                "episode_set.episodes must be a non-empty list."
            )

        parsed = []
        keys = []
        for index, episode in enumerate(raw_episodes):
            label = f"episode_set.episodes[{index}]"
            episode = _require_mapping(episode, label)
            _require_only_keys(episode, {"episode_key", "assets"}, label)
            episode_key = _validate_episode_key(episode.get("episode_key"))
            keys.append(episode_key)
            assets = _require_mapping(episode.get("assets"), f"{label}.assets")
            _require_only_keys(
                assets,
                {"mcap", "primary_videos", "additional_videos"},
                f"{label}.assets",
            )
            mcap_path = self._resolve_source(
                _source_path_from_asset(assets.get("mcap"), f"{label}.assets.mcap"),
                f"episode {episode_key} mcap",
                ".mcap",
            )
            primary_templates = self._parse_primary_assets(
                assets.get("primary_videos"), f"{label}.assets.primary_videos"
            )
            primary_paths = {
                slot: self._resolve_source(
                    source_path,
                    f"episode {episode_key} primary {slot}",
                    ".mp4",
                )
                for slot, source_path in primary_templates.items()
            }
            raw_additional = assets.get("additional_videos", [])
            if not isinstance(raw_additional, list):
                raise PrismaxValidationError(
                    f"{label}.assets.additional_videos must be a list."
                )
            additional_paths = [
                self._resolve_source(
                    _source_path_from_asset(item, f"{label}.assets.additional_videos[{item_index}]"),
                    f"episode {episode_key} additional video",
                    ".mp4",
                )
                for item_index, item in enumerate(raw_additional)
            ]
            assigned_primary = {path.resolve() for path in primary_paths.values()}
            duplicate_primary_paths = [
                path for path in additional_paths if path.resolve() in assigned_primary
            ]
            if duplicate_primary_paths:
                raise PrismaxValidationError(
                    f"Episode {episode_key} additional_videos repeats a primary source: "
                    f"{duplicate_primary_paths[0]}"
                )
            parsed.append((
                episode_key,
                self._build_episode_files(
                    episode_key, mcap_path, primary_paths, additional_paths
                ),
            ))
        self._validate_unique_episode_keys(keys)
        return parsed

    def _parse_primary_assets(self, value, label):
        value = _require_mapping(value, label)
        _require_only_keys(value, set(PRIMARY_DESTINATIONS), label)
        missing = [slot for slot in PRIMARY_DESTINATIONS if slot not in value]
        if missing:
            raise PrismaxValidationError(
                f"{label} is missing required videos: {', '.join(missing)}."
            )
        return {
            slot: _source_path_from_asset(value[slot], f"{label}.{slot}")
            for slot in PRIMARY_DESTINATIONS
        }

    def _build_episode_files(
        self, episode_key, mcap_path, primary_paths, additional_paths
    ):
        assigned_primary = {path.resolve() for path in primary_paths.values()}
        if len(assigned_primary) != len(PRIMARY_DESTINATIONS):
            raise PrismaxValidationError(
                f"Episode {episode_key} must use a different source file for each primary video."
            )
        files = [(mcap_path, f"{episode_key}.mcap")]
        files.extend(
            (primary_paths[slot], f"{episode_key}/{destination}")
            for slot, destination in PRIMARY_DESTINATIONS.items()
        )
        for path in sorted(additional_paths, key=lambda item: item.as_posix()):
            if path.resolve() in assigned_primary:
                continue
            files.append((path, f"{episode_key}/{path.name}"))
        return files

    def _resolve_source(self, value, label, expected_suffix):
        pure_path = PurePosixPath(value.replace("\\", "/"))
        if pure_path.is_absolute() or ".." in pure_path.parts:
            raise PrismaxValidationError(
                f"{label} must be a relative path inside {self._base_path}: {value!r}."
            )
        if any(character in value for character in "*?["):
            raise PrismaxValidationError(f"{label} cannot contain glob characters: {value!r}.")
        unresolved_source_path = self._base_path / Path(*pure_path.parts)
        symlink_path = self._find_symlink(unresolved_source_path)
        if symlink_path is not None:
            raise PrismaxValidationError(
                f"{label} cannot reference a symbolic link: {symlink_path}"
            )
        source_path = unresolved_source_path.resolve()
        try:
            relative_source = source_path.relative_to(self._base_path)
        except ValueError:
            raise PrismaxValidationError(
                f"{label} resolves outside {self._base_path}: {value!r}."
            ) from None
        if _is_hidden_path(relative_source):
            raise PrismaxValidationError(f"{label} cannot reference a hidden file: {value!r}.")
        if not source_path.exists() or not source_path.is_file():
            raise PrismaxValidationError(f"{label} does not exist: {source_path}")
        if source_path.suffix != expected_suffix:
            raise PrismaxValidationError(
                f"{label} must use the lowercase {expected_suffix} extension: {source_path.name}"
            )
        return source_path

    def _expand_glob(self, pattern, episode_key):
        pure_pattern = PurePosixPath(pattern.replace("\\", "/"))
        if pure_pattern.is_absolute() or ".." in pure_pattern.parts:
            raise PrismaxValidationError(
                f"Episode {episode_key} additional_videos.source_glob must stay inside "
                f"{self._base_path}: {pattern!r}."
            )
        matches = []
        for path in self._base_path.glob(pattern):
            try:
                relative_source = path.relative_to(self._base_path)
            except ValueError:
                raise PrismaxValidationError(
                    f"Episode {episode_key} additional video resolves outside "
                    f"{self._base_path}: {path}"
                ) from None
            if _is_hidden_path(relative_source):
                continue
            symlink_path = self._find_symlink(path)
            if symlink_path is not None:
                raise PrismaxValidationError(
                    f"Episode {episode_key} additional video cannot use a symbolic link: "
                    f"{symlink_path}"
                )
            resolved = path.resolve()
            if not resolved.is_file():
                continue
            try:
                relative_source = resolved.relative_to(self._base_path)
            except ValueError:
                raise PrismaxValidationError(
                    f"Episode {episode_key} additional video resolves outside "
                    f"{self._base_path}: {path}"
                ) from None
            if resolved.suffix != ".mp4":
                continue
            matches.append(resolved)
        return matches

    def _find_symlink(self, path):
        try:
            relative_path = path.relative_to(self._base_path)
        except ValueError:
            return path if path.is_symlink() else None
        current = self._base_path
        for part in relative_path.parts:
            current = current / part
            if current.is_symlink():
                return current
        return None

    @staticmethod
    def _render(template, episode_key):
        try:
            return template.format(episode_key=episode_key)
        except (IndexError, KeyError, ValueError) as exc:
            raise PrismaxValidationError(
                f"Invalid source path template {template!r}: {exc}"
            ) from exc

    @staticmethod
    def _validate_unique_episode_keys(keys):
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise PrismaxValidationError(
                f"Duplicate episode_keys are not allowed: {', '.join(duplicates)}."
            )
