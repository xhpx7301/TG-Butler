from __future__ import annotations

import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def time_parts(value: str) -> tuple[int, int]:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("Time must be HH:MM (00:00 through 23:59)")
    hour, minute = value.split(":")
    return int(hour), int(minute)


class Group(StrictModel):
    id: int
    name: str = ""
    daily_summary: bool = True
    notify_all: bool = False
    watch_users: list[int] = Field(default_factory=list)


class Checkin(StrictModel):
    name: str = Field(min_length=1)
    chat_id: int
    at: str
    command: str = Field(min_length=1, max_length=4096)
    responder_id: int | None = None
    success_pattern: str | None = None
    failure_pattern: str | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    require_reply: bool = True

    @model_validator(mode="after")
    def validate_checkin(self) -> Checkin:
        time_parts(self.at)
        if bool(self.responder_id) != bool(self.success_pattern):
            raise ValueError("responder_id and success_pattern must be configured together")
        for pattern in (self.success_pattern, self.failure_pattern):
            if pattern:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(f"Invalid check-in response pattern: {exc}") from None
        return self


class Summary(StrictModel):
    enabled: bool = True
    at: str = "00:05"
    api_url: str | None = None
    model: str | None = None
    max_messages: int = Field(default=500, ge=1, le=10000)
    max_chars: int = Field(default=30000, ge=1000, le=200000)

    @model_validator(mode="after")
    def validate_summary(self) -> Summary:
        time_parts(self.at)
        if bool(self.api_url) != bool(self.model):
            raise ValueError("summary.api_url and summary.model must be configured together")
        if self.api_url and not self.api_url.startswith("https://"):
            raise ValueError("summary.api_url must use HTTPS")
        return self


class Notifications(StrictModel):
    queue_size: int = Field(default=500, ge=1, le=10000)


class Settings(StrictModel):
    timezone: str = "Asia/Shanghai"
    data_dir: Path = Path("data")
    retention_days: int = Field(default=30, ge=1, le=3650)
    notifications: Notifications = Field(default_factory=Notifications)
    summary: Summary = Field(default_factory=Summary)
    groups: list[Group] = Field(default_factory=list)
    checkins: list[Checkin] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_settings(self) -> Settings:
        ZoneInfo(self.timezone)
        if len({group.id for group in self.groups}) != len(self.groups):
            raise ValueError("Group IDs must be unique")
        if len({job.name for job in self.checkins}) != len(self.checkins):
            raise ValueError("Check-in names must be unique")
        return self


class ApplicationCredentials(BaseModel):
    api_id: int = Field(gt=0)
    api_hash: SecretStr = Field(min_length=1)

    @model_validator(mode="after")
    def reject_application_example(self) -> ApplicationCredentials:
        if self.api_hash.get_secret_value() == "replace_with_api_hash":
            raise ValueError("请在 my.telegram.org 申请并填写真实 API ID / API HASH")
        return self


class NotificationCredentials(BaseModel):
    bot_token: SecretStr = Field(min_length=1)
    notify_chat_id: int

    @model_validator(mode="after")
    def reject_notification_example(self) -> NotificationCredentials:
        if self.bot_token.get_secret_value() == "replace_with_bot_token":
            raise ValueError("请填写 BotFather 提供的真实 Bot Token")
        return self


class Credentials(ApplicationCredentials, NotificationCredentials):
    summary_api_key: SecretStr = SecretStr("")


def load_settings(path: Path) -> Settings:
    path = path.resolve()
    with path.open(encoding="utf-8") as handle:
        settings = Settings.model_validate(yaml.safe_load(handle) or {})
    if not settings.data_dir.is_absolute():
        settings.data_dir = path.parent / settings.data_dir
    return settings


def load_credentials(config_path: Path) -> Credentials:
    load_dotenv(config_path.resolve().parent / ".env", override=False)
    return Credentials(
        api_id=os.environ.get("TG_API_ID", ""),
        api_hash=os.environ.get("TG_API_HASH", ""),
        bot_token=os.environ.get("TG_BOT_TOKEN", ""),
        notify_chat_id=os.environ.get("TG_NOTIFY_CHAT_ID", ""),
        summary_api_key=os.environ.get("SUMMARY_API_KEY", ""),
    )


def load_application_credentials(config_path: Path) -> ApplicationCredentials:
    load_dotenv(config_path.resolve().parent / ".env", override=False)
    return ApplicationCredentials(
        api_id=os.environ.get("TG_API_ID", ""), api_hash=os.environ.get("TG_API_HASH", "")
    )


def load_notification_credentials(config_path: Path) -> NotificationCredentials:
    load_dotenv(config_path.resolve().parent / ".env", override=False)
    return NotificationCredentials(
        bot_token=os.environ.get("TG_BOT_TOKEN", ""),
        notify_chat_id=os.environ.get("TG_NOTIFY_CHAT_ID", ""),
    )
