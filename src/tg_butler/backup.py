"""Private, versioned server backups for configuration and runtime data."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from datetime import datetime
from io import StringIO
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

import yaml
from dotenv import dotenv_values

from tg_butler.config import Settings, load_settings

BACKUP_LIMIT = 20 * 1024 * 1024
BACKUP_RETENTION = 30
ALLOWED_FILES = {
    "config.yaml",
    ".env",
    "runtime/butler.sqlite3",
    "runtime/user.session",
    "runtime/backup-settings.json",
}


def backup_dir(root: Path) -> Path:
    path = root / "data" / "backups"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


def create_backup(root: Path, config: Path, *, now: datetime | None = None) -> Path:
    root, config = root.resolve(), config.resolve()
    data_dir = load_settings(config).data_dir
    when = now or datetime.now().astimezone()
    folder = backup_dir(root)
    timestamp = when.strftime("%Y%m%d-%H%M%S")
    target = folder / f"tg-butler-{timestamp}.tar.gz"
    suffix = 1
    while target.exists():
        target = folder / f"tg-butler-{timestamp}-{suffix}.tar.gz"
        suffix += 1
    with tempfile.TemporaryDirectory(dir=folder) as temp_name:
        temp = Path(temp_name)
        files: list[tuple[Path, str]] = []
        sources = [
            (config, "config.yaml"),
            (root / ".env", ".env"),
            (data_dir / "butler.sqlite3", "runtime/butler.sqlite3"),
            (data_dir / "user.session", "runtime/user.session"),
            (root / "data" / "backup-settings.json", "runtime/backup-settings.json"),
        ]
        account_config = Settings.model_validate(
            yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        )
        sources.extend(
            (
                data_dir / "accounts" / f"{account.id}.session",
                f"runtime/accounts/{account.id}.session",
            )
            for account in account_config.accounts
            if account.id != "default"
        )
        for source, archive_name in sources:
            if not source.is_file():
                if archive_name == ".env":
                    empty_env = temp / ".env"
                    empty_env.write_text("", encoding="utf-8")
                    files.append((empty_env, archive_name))
                continue
            if archive_name.endswith((".sqlite3", ".session")):
                snapshot = temp / Path(archive_name).name
                _sqlite_snapshot(source, snapshot)
                files.append((snapshot, archive_name))
            else:
                files.append((source, archive_name))
        metadata = temp / "manifest.json"
        metadata.write_text(
            json.dumps(
                {
                    "format": 1,
                    "created_at": when.isoformat(),
                    "application": "TG-Butler",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary = folder / f".{target.name}.tmp"
        try:
            with tarfile.open(temporary, "w:gz") as archive:
                archive.add(metadata, arcname="manifest.json")
                for source, name in files:
                    archive.add(source, arcname=name)
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    prune_backups(root)
    return target


def list_backups(root: Path) -> list[dict]:
    folder = backup_dir(root)
    return [
        {
            "name": path.name,
            "size": path.stat().st_size,
            "created_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(),
        }
        for path in sorted(
            folder.glob("tg-butler-*.tar.gz"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if path.is_file() and not path.is_symlink()
    ]


def prune_backups(root: Path) -> None:
    for item in list_backups(root)[BACKUP_RETENTION:]:
        (backup_dir(root) / item["name"]).unlink(missing_ok=True)


def _read_archive(upload: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(upload, "r:gz") as archive:
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                if not member.isfile() or path.is_absolute() or ".." in path.parts:
                    raise ValueError("备份包包含不支持的路径或文件类型")
                if member.name == "manifest.json":
                    if member.size > 4096:
                        raise ValueError("备份清单无效")
                elif member.name not in ALLOWED_FILES and not (
                    len(path.parts) == 3
                    and path.parts[0] == "runtime"
                    and path.parts[1] == "accounts"
                    and re.fullmatch(r"[a-z][a-z0-9_-]{0,39}\.session", path.parts[2])
                ):
                    raise ValueError("备份包包含未知文件，已拒绝导入")
                if member.name in result:
                    raise ValueError("备份包内存在重复文件")
                total += member.size
                if total > BACKUP_LIMIT:
                    raise ValueError("备份内容超过 20 MB 限制")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("备份包读取失败")
                result[member.name] = stream.read(BACKUP_LIMIT + 1)
    except (tarfile.TarError, OSError) as exc:
        raise ValueError("文件不是有效的 TG-Butler .tar.gz 备份") from exc
    if "config.yaml" not in result or ".env" not in result or "manifest.json" not in result:
        raise ValueError("备份缺少配置文件或清单")
    try:
        manifest = json.loads(result["manifest.json"])
        Settings.model_validate(yaml.safe_load(result["config.yaml"]) or {})
        dotenv_values(stream=StringIO(result[".env"].decode("utf-8")))
    except (ValueError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError("备份中的配置文件无法读取") from exc
    if not isinstance(manifest, dict):
        raise ValueError("备份清单无效")
    if manifest.get("application") != "TG-Butler" or manifest.get("format") != 1:
        raise ValueError("备份格式或版本不受支持")
    return result


def validate_backup(upload: Path) -> None:
    _read_archive(upload)


def restore_backup(root: Path, config: Path, upload: Path) -> None:
    files = _read_archive(upload)
    root, config = root.resolve(), config.resolve()
    current_data_dir = load_settings(config).data_dir
    current_data_value = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    imported_config = yaml.safe_load(files["config.yaml"]) or {}
    imported_settings = Settings.model_validate(imported_config)
    imported_data_dir = imported_settings.data_dir
    if not imported_data_dir.is_absolute():
        imported_data_dir = config.parent / imported_data_dir
    if imported_data_dir.resolve() != current_data_dir.resolve():
        imported_config["data_dir"] = current_data_value.get("data_dir", "data")
        files["config.yaml"] = yaml.safe_dump(
            imported_config, allow_unicode=True, sort_keys=False
        ).encode("utf-8")
    staging = root / "data" / ".restore-staging"
    staging.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        staged: dict[str, Path] = {}
        for name, content in files.items():
            if name == "manifest.json":
                continue
            destination = staging / Path(name).name
            destination.write_bytes(content)
            os.chmod(destination, 0o600)
            if name.endswith((".sqlite3", ".session")):
                with closing(sqlite3.connect(f"file:{destination}?mode=ro", uri=True)) as db:
                    db.execute("PRAGMA schema_version").fetchone()
            staged[name] = destination
        for name in sorted(staged, key=lambda item: item == "config.yaml"):
            destination = staged[name]
            target = (
                config
                if name == "config.yaml"
                else (
                    root / ".env"
                    if name == ".env"
                    else root / "data" / "backup-settings.json"
                    if name == "runtime/backup-settings.json"
                    else current_data_dir
                    / Path(name.removeprefix("runtime/")).parent
                    / Path(name).name
                )
            )
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.replace(destination, target)
            os.chmod(target, 0o600)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def read_schedule(root: Path) -> dict:
    path = root / "data" / "backup-settings.json"
    if not path.exists():
        return {"enabled": True, "at": "03:00"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("enabled"), bool) or not isinstance(data.get("at"), str):
            raise ValueError
        if len(data["at"]) != 5 or data["at"][2] != ":":
            raise ValueError
        hour, minute = map(int, data["at"].split(":"))
        if hour not in range(24) or minute not in range(60):
            raise ValueError
        return {"enabled": data["enabled"], "at": data["at"]}
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {"enabled": True, "at": "03:00"}


def write_schedule(root: Path, schedule: dict) -> dict:
    if not isinstance(schedule, dict) or not isinstance(schedule.get("enabled"), bool):
        raise ValueError("请设置自动备份开关")
    at = schedule.get("at", "")
    try:
        hour, minute = map(int, at.split(":"))
        if len(at) != 5 or at[2] != ":" or hour not in range(24) or minute not in range(60):
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("备份时间格式应为 HH:MM") from None
    folder = root / "data"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / "backup-settings.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"enabled": schedule["enabled"], "at": at}), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return {"enabled": schedule["enabled"], "at": at}


def next_backup(root: Path, timezone: str) -> str | None:
    schedule = read_schedule(root)
    if not schedule["enabled"]:
        return None
    from datetime import timedelta

    zone = ZoneInfo(timezone)
    now = datetime.now(zone)
    hour, minute = map(int, schedule["at"].split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.isoformat()
