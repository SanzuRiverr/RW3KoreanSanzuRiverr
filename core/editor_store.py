"""Mutable translation editor state with validated, atomic persistence."""

from __future__ import annotations

import json
import os
import shutil
import string
import tempfile
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path
from typing import MutableMapping


def _syntax_error(message: str) -> ValueError:
    return ValueError(f"번역 형식 오류: {message}")


def _scan_delimiters(value: str, opening: str, closing: str):
    """Return balanced token contents and unmatched delimiter characters."""
    tokens = []
    unmatched = []
    start = None
    for index, char in enumerate(value):
        if char == opening:
            if start is not None:
                unmatched.append(opening)
            start = index
        elif char == closing:
            if start is None:
                unmatched.append(closing)
            else:
                tokens.append(value[start + 1:index])
                start = None
    if start is not None:
        unmatched.append(opening)
    return tokens, unmatched


def _markup_id(token: str) -> str:
    if ":" in token:
        return token.rsplit(":", 1)[1].strip().casefold()
    return token.strip().casefold()


def _markup_signature(value: str):
    tokens, unmatched = _scan_delimiters(value, "[", "]")
    return [_markup_id(token) for token in tokens], unmatched


def _format_signature(value: str):
    fields = []
    try:
        parsed = string.Formatter().parse(value)
        for literal, field, spec, conversion in parsed:
            if field is not None:
                fields.append((field, spec, conversion))
    except (ValueError, IndexError) as error:
        raise _syntax_error(f"중괄호 서식이 잘못되었습니다: {error}") from error
    _, unmatched = _scan_delimiters(value, "{", "}")
    return fields, unmatched


def validate_translation(en: str, ko: str) -> None:
    """Validate a translated string's placeholders and semantic markup."""
    if not isinstance(en, str) or not en.strip():
        raise _syntax_error("원문은 비어 있지 않은 문자열이어야 합니다.")
    if not isinstance(ko, str) or not ko.strip():
        raise _syntax_error("번역문은 비어 있지 않은 문자열이어야 합니다.")

    source_fields, source_brace_unmatched = _format_signature(en)
    target_fields, target_brace_unmatched = _format_signature(ko)
    if Counter(source_fields) != Counter(target_fields):
        raise _syntax_error(
            f"중괄호 필드·서식의 개수나 내용이 원문과 다릅니다 (원문={source_fields!r}, 번역={target_fields!r})."
        )
    if source_brace_unmatched != target_brace_unmatched:
        raise _syntax_error("중괄호의 짝이 맞지 않습니다.")

    source_markup, source_square_unmatched = _markup_signature(en)
    target_markup, target_square_unmatched = _markup_signature(ko)
    if Counter(source_markup) != Counter(target_markup):
        raise _syntax_error(
            f"대괄호 마크업 ID의 개수가 원문과 다릅니다 (원문={source_markup!r}, 번역={target_markup!r})."
        )
    if source_square_unmatched != target_square_unmatched:
        raise _syntax_error("대괄호의 짝이 맞지 않습니다.")


class TranslationEditorStore:
    """Keep editor overrides while mutating the caller's catalog in place."""

    def __init__(self, path, catalog: MutableMapping[str, str]):
        self.path = Path(path)
        self.catalog = catalog
        self.original_base = dict(catalog)
        self.original = self.original_base
        self.enabled = False
        self.detection_mode = 'off'
        self.overrides: dict[str, str] = {}
        self.ignored: set[str] = set()
        self.missing: dict[str, str] = {}
        self.revision = 0
        if self.path.exists():
            self._load()
            for key, value in self.overrides.items():
                if key not in self.ignored:
                    self.catalog[key] = value
            self.missing = {key: value for key, value in self.missing.items()
                            if not self.catalog.get(key) and key not in self.ignored}

    def _load(self) -> None:
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"{self.path}: 편집 저장소를 읽을 수 없습니다: {error}") from error
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise ValueError(f"{self.path}: 지원하지 않는 편집 저장소 schema입니다 (schema=1 필요).")
        enabled = data.get("enabled", False)
        overrides = data.get("overrides", {})
        ignored = data.get("ignored", [])
        missing = data.get("missing", {})
        if not isinstance(enabled, bool) or not isinstance(overrides, dict) or not isinstance(ignored, list):
            raise ValueError(f"{self.path}: enabled/overrides/ignored 형식이 잘못되었습니다.")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in overrides.items()):
            raise ValueError(f"{self.path}: overrides에는 문자열 키와 값만 허용됩니다.")
        if any(not isinstance(key, str) for key in ignored):
            raise ValueError(f"{self.path}: ignored에는 문자열 키만 허용됩니다.")
        if not isinstance(missing, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                                for key, value in missing.items()):
            raise ValueError(f"{self.path}: missing에는 원문과 표시 예시 문자열만 허용됩니다.")
        for key, value in overrides.items():
            try:
                validate_translation(key, value)
            except ValueError as error:
                raise ValueError(f"{self.path}: overrides[{key!r}] 검증 실패: {error}") from error
        mode = data.get('detection_mode', 'off')
        if mode not in ('off', 'instant', 'collect'):
            raise ValueError('잘못된 번역 누락 감지 모드: ' + repr(mode))
        self.detection_mode = mode
        self.enabled = mode == 'instant'
        self.overrides = dict(overrides)
        self.ignored = set(ignored)
        self.missing = dict(missing)

    def _snapshot(self):
        return dict(self.catalog), dict(self.overrides), set(self.ignored), self.enabled, dict(self.missing), self.detection_mode

    def _restore(self, snapshot) -> None:
        old_catalog, old_overrides, old_ignored, old_enabled, old_missing, old_mode = snapshot
        self.catalog.clear()
        self.catalog.update(old_catalog)
        self.overrides = old_overrides
        self.ignored = old_ignored
        self.enabled = old_enabled
        self.missing = old_missing
        self.detection_mode = old_mode

    def _payload(self) -> dict:
        return {
            "schema": 1,
            "enabled": self.enabled,
            "detection_mode": self.detection_mode,
            "overrides": dict(sorted(self.overrides.items())),
            "ignored": sorted(self.ignored),
            "missing": dict(self.missing),
        }

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent, prefix=f".{self.path.name}.",
                suffix=".tmp", delete=False
            ) as stream:
                temp_name = stream.name
                json.dump(self._payload(), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if self.path.exists():
                shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
            os.replace(temp_name, self.path)
        except OSError:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
            raise

    def set_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("enabled는 bool이어야 합니다.")
        self.set_mode('instant' if enabled else 'collect')

    def set_mode(self, mode):
        if mode not in ('off', 'instant', 'collect'):
            raise ValueError('잘못된 번역 누락 감지 모드')
        snapshot = self._snapshot()
        self.detection_mode = mode
        self.enabled = mode == 'instant'
        try:
            self._persist()
        except OSError:
            self._restore(snapshot)
            raise

    def save_missing(self, records: dict[str, str], force=False) -> None:
        """Persist one batch of newly observed, unresolved display requests."""
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in records.items()):
            raise ValueError("누락 문구와 표시 예시는 문자열이어야 합니다.")
        additions = {key: value for key, value in records.items()
                     if key not in self.missing and key not in self.ignored and not self.catalog.get(key)}
        if not additions and not force:
            return
        snapshot = self._snapshot()
        self.missing.update(additions)
        try:
            self._persist()
        except OSError:
            self._restore(snapshot)
            raise

    def save_translation(self, en: str, ko: str) -> None:
        validate_translation(en, ko)
        snapshot = self._snapshot()
        self.overrides[en] = ko
        self.ignored.discard(en)
        self.catalog[en] = ko
        self.missing.pop(en, None)
        try:
            self._persist()
        except OSError:
            self._restore(snapshot)
            raise
        self.revision += 1

    def merge_translations(self, entries: dict[str, str]) -> None:
        """Validate and commit an import once, retaining a dated pre-import copy."""
        if not isinstance(entries, dict):
            raise ValueError("가져올 번역은 원문과 번역문의 사전이어야 합니다.")
        entries = dict(entries)
        for en, ko in entries.items():
            validate_translation(en, ko)
        if not entries:
            return
        snapshot = self._snapshot()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        backup = self.path.with_name(self.path.name + f".before-import-{stamp}.bak")
        # Exclusive creation preserves older import backups even on a collision.
        try:
            with backup.open("xb") as stream:
                if self.path.exists():
                    with self.path.open("rb") as current:
                        shutil.copyfileobj(current, stream)
                else:
                    stream.write((json.dumps(self._payload(), ensure_ascii=False, indent=2)
                                  + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            self.overrides.update(entries)
            self.catalog.update(entries)
            self.ignored.difference_update(entries)
            for en in entries:
                self.missing.pop(en, None)
            self._persist()
        except Exception:
            self._restore(snapshot)
            raise
        self.revision += 1

    def remove_translation(self, en: str) -> None:
        if not isinstance(en, str) or not en:
            raise ValueError("en은 비어 있지 않은 문자열이어야 합니다.")
        snapshot = self._snapshot()
        self.overrides.pop(en, None)
        self.ignored.discard(en)
        if en in self.original:
            self.catalog[en] = self.original[en]
        else:
            self.catalog.pop(en, None)
        try:
            self._persist()
        except OSError:
            self._restore(snapshot)
            raise
        self.revision += 1

    def ignore(self, en: str) -> None:
        if not isinstance(en, str) or not en:
            raise ValueError("en은 비어 있지 않은 문자열이어야 합니다.")
        snapshot = self._snapshot()
        self.ignored.add(en)
        self.missing.pop(en, None)
        self.overrides.pop(en, None)
        if en in self.original:
            self.catalog[en] = self.original[en]
        else:
            self.catalog.pop(en, None)
        try:
            self._persist()
        except OSError:
            self._restore(snapshot)
            raise
        self.revision += 1
