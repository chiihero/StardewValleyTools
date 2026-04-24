from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    from packaging.version import InvalidVersion, Version
except Exception:  # pragma: no cover - 只在环境缺少 packaging 时走 fallback。
    InvalidVersion = Exception
    Version = None

try:
    import py7zr
except Exception:  # pragma: no cover - 7z 仅在装了额外依赖时启用。
    py7zr = None

from .models import ManagedMod, NexusDownloadResult, NexusUpdateInfo, UpdateStatus
from .scanner import scan_mod

NEXUS_API_BASE = "https://api.nexusmods.com/v1/"
NEXUS_GAME_DOMAIN = "stardewvalley"


@dataclass(slots=True)
class NexusUpdateSource:
    """保存从 manifest 更新键里提取出来的 Nexus 定位信息。"""

    mod_id: int
    subkey: str | None = None
    file_id: int | None = None
    raw_key: str = ""


@dataclass(slots=True)
class NexusFileInfo:
    """保存 Nexus 文件列表中的关键字段。"""

    file_id: int
    version: str | None
    file_name: str | None
    description: str | None
    category_name: str | None
    is_primary: bool
    uploaded_timestamp: str | None


class NexusError(RuntimeError):
    """表示 Nexus API、下载或安装过程中的可预期错误。"""


def _extract_int(value: Any) -> int | None:
    """把任意值转换成整数，失败时返回 None。"""
    try:
        text = str(value).strip()
        if not text:
            return None
        return int(text)
    except Exception:
        return None


def _normalise_version_key(version: str) -> tuple[Any, ...]:
    """把版本字符串转换成可比较的回退键。"""
    pieces: list[Any] = []
    for token in re.findall(r"\d+|[A-Za-z]+", version.replace("-", ".")):
        pieces.append(int(token) if token.isdigit() else token.lower())
    return tuple(pieces)


def _is_remote_newer(local_version: str | None, remote_version: str | None) -> bool:
    """比较两个版本号，判断远端版本是否更新。"""
    if not local_version or not remote_version:
        return False

    local_text = local_version.lstrip("vV").strip()
    remote_text = remote_version.lstrip("vV").strip()

    if Version is not None:
        try:
            return Version(remote_text) > Version(local_text)
        except InvalidVersion:
            pass

    return _normalise_version_key(remote_text) > _normalise_version_key(local_text)


def _parse_update_key(value: str) -> NexusUpdateSource | None:
    """解析 manifest.json 里的 Nexus 更新键。"""
    match = re.match(r"(?i)^nexus:\s*(?P<mod_id>\d+)(?:@(?P<subkey>.+))?$", value.strip())
    if match is None:
        return None

    mod_id = _extract_int(match.group("mod_id"))
    if mod_id is None:
        return None

    return NexusUpdateSource(
        mod_id=mod_id,
        subkey=str(match.group("subkey")).strip() if match.group("subkey") else None,
        file_id=_extract_int(match.group("subkey")),
        raw_key=value.strip(),
    )


def extract_nexus_source(update_keys: Iterable[str]) -> NexusUpdateSource | None:
    """从 manifest 更新键中提取第一个可用的 Nexus 目标。"""
    for key in update_keys:
        parsed = _parse_update_key(str(key))
        if parsed is not None:
            return parsed
    return None


def _select_download_link(links: Any) -> str | None:
    """从 download_link.json 返回值里挑出可用的 CDN 地址。"""
    entries: list[dict[str, Any]] = []
    if isinstance(links, list):
        entries = [entry for entry in links if isinstance(entry, dict)]
    elif isinstance(links, dict):
        entries = [links]

    for entry in entries:
        short_name = str(entry.get("short_name") or entry.get("shortName") or "").strip().lower()
        url = str(entry.get("URI") or entry.get("uri") or entry.get("url") or "").strip()
        if short_name == "nexus cdn" and url:
            return url

    for entry in entries:
        url = str(entry.get("URI") or entry.get("uri") or entry.get("url") or "").strip()
        if url:
            return url

    return None


def build_manual_download_url(mod_id: int, file_id: int | None = None) -> str:
    """生成 Nexus Mods 的网页手动下载地址。"""
    base_url = f"https://www.nexusmods.com/{NEXUS_GAME_DOMAIN}/mods/{mod_id}"
    if file_id is None:
        return base_url
    return f"{base_url}?tab=files&file_id={file_id}"


class NexusService:
    """封装 Nexus Mods 的更新查询、下载与安装流程。"""

    def __init__(self, api_key: str, timeout: int = 30) -> None:
        """初始化 Nexus 服务。"""
        self.api_key = api_key.strip()
        self.timeout = timeout

    def _build_request(self, url: str, method: str = "GET") -> Request:
        """创建带 Nexus 约定头部的请求对象。"""
        headers = {
            "Application-Name": "StardewValleyTools",
            "Application-Version": "1.0",
            "User-Agent": "StardewValleyTools/1.0",
        }
        if self.api_key:
            headers["apikey"] = self.api_key
        return Request(url, method=method, headers=headers)

    def _request_json(self, path: str) -> Any:
        """请求 Nexus API 并把 JSON 响应解析成 Python 对象。"""
        url = f"{NEXUS_API_BASE.rstrip('/')}/{path.lstrip('/')}"
        request = self._build_request(url)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload)
        except HTTPError as exc:
            raise NexusError(f"Nexus API 请求失败：HTTP {exc.code} {exc.reason}") from exc
        except URLError as exc:
            raise NexusError(f"Nexus API 请求失败：{exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise NexusError(f"Nexus API 返回了无法解析的 JSON：{exc}") from exc

    def _request_bytes(self, url: str) -> bytes:
        """下载二进制内容。"""
        request = self._build_request(url)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except HTTPError as exc:
            raise NexusError(f"Nexus 下载失败：HTTP {exc.code} {exc.reason}") from exc
        except URLError as exc:
            raise NexusError(f"Nexus 下载失败：{exc.reason}") from exc

    def extract_source(self, record: ManagedMod) -> NexusUpdateSource | None:
        """从当前 Mod 记录里提取 Nexus 更新源。"""
        analysis = record.analysis or scan_mod(record.source_path)
        manifest = analysis.manifest
        if manifest is None or not manifest.update_keys:
            return None
        return extract_nexus_source(manifest.update_keys)

    def _extract_file_info(self, payload: dict[str, Any]) -> NexusFileInfo | None:
        """把 Nexus 文件详情整理成内部结构。"""
        file_id = _extract_int(payload.get("file_id") or payload.get("fileId") or payload.get("id"))
        if file_id is None:
            return None
        category = payload.get("category_name") or payload.get("categoryName")
        if isinstance(category, dict):
            category = category.get("name") or category.get("title")
        return NexusFileInfo(
            file_id=file_id,
            version=str(payload.get("version") or "") or None,
            file_name=str(payload.get("file_name") or payload.get("name") or "") or None,
            description=str(payload.get("description") or payload.get("Description") or "") or None,
            category_name=str(category) if category is not None else None,
            is_primary=bool(payload.get("is_primary") or payload.get("isPrimary")),
            uploaded_timestamp=str(payload.get("uploaded_timestamp") or payload.get("uploadedTime") or "") or None,
        )

    def _select_latest_file(self, files_payload: Any, source: NexusUpdateSource) -> NexusFileInfo | None:
        """从文件列表中选出最适合作为更新目标的文件。"""
        candidates: list[NexusFileInfo] = []

        if isinstance(files_payload, dict):
            items = files_payload.get("files") or files_payload.get("Files") or []
        else:
            items = files_payload

        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                parsed = self._extract_file_info(item)
                if parsed is not None:
                    candidates.append(parsed)

        if not candidates:
            return None

        if source.file_id is not None:
            matched = next((item for item in candidates if item.file_id == source.file_id), None)
            if matched is not None:
                return matched

        if source.subkey:
            lowered = source.subkey.lower()
            flagged = [
                item
                for item in candidates
                if lowered in (item.file_name or "").lower()
                or lowered in (item.description or "").lower()
                or lowered in (item.category_name or "").lower()
            ]
            if flagged:
                candidates = flagged

        # primary = [item for item in candidates if item.is_primary]
        # if primary:
        #     candidates = primary

        main_files = [item for item in candidates if (item.category_name or "").strip().upper() == "MAIN"]
        if main_files:
            candidates = main_files

        # Nexus 文件通常按上传时间递增；如果时间缺失，就退回到列表顺序。
        def sort_key(item: NexusFileInfo) -> tuple[str, int]:
            return (item.uploaded_timestamp or "", item.file_id)

        return sorted(candidates, key=sort_key, reverse=True)[0]

    def check_mod(self, record: ManagedMod) -> NexusUpdateInfo:
        """检查单个 Mod 的 Nexus 更新状态。"""
        analysis = record.analysis or scan_mod(record.source_path)
        manifest = analysis.manifest
        local_version = manifest.version if manifest is not None else record.version
        source = self.extract_source(record)
        checked_at = datetime.now().isoformat(timespec="seconds")

        if source is None:
            return NexusUpdateInfo(
                status="no_source",
                current_version=local_version,
                checked_at=checked_at,
                message="缺少可用的 Nexus 更新键。",
            )

        details = self._request_json(f"games/{NEXUS_GAME_DOMAIN}/mods/{source.mod_id}.json")
        files_payload = self._request_json(f"games/{NEXUS_GAME_DOMAIN}/mods/{source.mod_id}/files.json")
        selected_file = self._select_latest_file(files_payload, source)

        if source.file_id is not None:
            # 如果 manifest 明确指向某个文件，就优先使用该文件的详情。
            try:
                detailed = self._request_json(f"games/{NEXUS_GAME_DOMAIN}/mods/{source.mod_id}/files/{source.file_id}.json")
                if isinstance(detailed, dict):
                    parsed = self._extract_file_info(detailed)
                    if parsed is not None:
                        selected_file = parsed
            except NexusError:
                # 如果详细文件接口失败，不中断整体检查，继续用文件列表里的候选项。
                pass

        remote_version = None
        file_name = None
        if selected_file is not None:
            remote_version = selected_file.version
            file_name = selected_file.file_name

        if remote_version is None and isinstance(details, dict):
            remote_version = str(details.get("version") or details.get("Version") or "") or None

        download_url = None
        if selected_file is not None:
            try:
                download_payload = self._request_json(
                    f"games/{NEXUS_GAME_DOMAIN}/mods/{source.mod_id}/files/{selected_file.file_id}/download_link.json"
                )
                download_url = _select_download_link(download_payload)
            except NexusError:
                download_url = None

        manual_download_url = build_manual_download_url(
            source.mod_id,
            selected_file.file_id if selected_file is not None else source.file_id,
        )

        status: UpdateStatus = "unknown"
        message = ""
        if local_version is None:
            message = "本地版本缺失，无法比较。"
        elif remote_version is None:
            message = "无法从 Nexus 读取远端版本。"
        elif _is_remote_newer(local_version, remote_version):
            status = "outdated"
            message = f"发现更新：{local_version} → {remote_version}"
        else:
            status = "up_to_date"
            message = f"已是最新版本：{remote_version}"

        if status == "outdated" and download_url is None and manual_download_url:
            message = f"{message}；需要通过网页手动下载：{manual_download_url}" if message else f"需要通过网页手动下载：{manual_download_url}"

        return NexusUpdateInfo(
            status=status,
            mod_id=source.mod_id,
            file_id=selected_file.file_id if selected_file is not None else source.file_id,
            current_version=local_version,
            latest_version=remote_version,
            file_name=file_name,
            download_url=download_url,
            manual_download_url=manual_download_url,
            update_url=f"https://www.nexusmods.com/stardewvalley/mods/{source.mod_id}",
            checked_at=checked_at,
            message=message,
        )

    def download_update(self, info: NexusUpdateInfo) -> Path:
        """把 Nexus 更新文件下载到临时目录。"""
        if not info.download_url:
            if info.manual_download_url:
                raise NexusError(f"缺少可直接下载的 Nexus 链接，请通过网页手动下载：{info.manual_download_url}")
            raise NexusError("缺少可下载的 Nexus 链接。")

        url_name = Path(urlparse(info.download_url).path).name
        suggested_name = info.file_name or url_name or f"nexus-{info.mod_id or 'update'}.zip"
        suffix = Path(suggested_name).suffix or Path(url_name).suffix or ".zip"
        filename = suggested_name if Path(suggested_name).suffix else f"{suggested_name}{suffix}"
        temp_dir = Path(tempfile.mkdtemp(prefix="stvtools-nexus-"))
        archive_path = temp_dir / filename
        archive_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            request = self._build_request(info.download_url)
            with urlopen(request, timeout=self.timeout) as response, archive_path.open("wb") as handle:
                shutil.copyfileobj(response, handle, length=1024 * 1024)
            return archive_path
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def _find_mod_root(self, extracted_root: Path) -> Path | None:
        """在解压结果里定位真正的 Mod 根目录。"""
        if (extracted_root / "manifest.json").is_file():
            return extracted_root

        manifests = [path for path in extracted_root.rglob("manifest.json") if path.is_file()]
        if not manifests:
            return None

        parents = {path.parent.resolve() for path in manifests}
        if len(parents) == 1:
            return next(iter(parents))

        return min(parents, key=lambda item: len(item.parts))

    def _extract_archive(self, archive_path: Path, extract_dir: Path) -> Path:
        """把下载的压缩包解压到临时目录，并返回真正的 Mod 根目录。"""
        extract_dir.mkdir(parents=True, exist_ok=True)
        suffix = archive_path.suffix.lower()

        if suffix == ".7z":
            if py7zr is None:
                raise NexusError("当前环境未安装 py7zr，无法解压 7z 文件。")
            with py7zr.SevenZipFile(archive_path, mode="r") as archive:
                archive.extractall(path=extract_dir)
        else:
            try:
                shutil.unpack_archive(str(archive_path), str(extract_dir))
            except shutil.ReadError as exc:
                raise NexusError(f"不支持的压缩包格式：{archive_path.suffix or 'unknown'}") from exc

        mod_root = self._find_mod_root(extract_dir)
        if mod_root is None:
            raise NexusError("压缩包里没有找到 manifest.json，无法识别 Mod 根目录。")
        return mod_root

    def install_download(self, record: ManagedMod, archive_path: Path) -> NexusDownloadResult:
        """把已经下载好的更新包安装回 Mod 库目录。"""
        install_temp = Path(tempfile.mkdtemp(prefix="stvtools-nexus-install-"))
        extracted_root = self._extract_archive(archive_path, install_temp)
        target_path = record.source_path
        backup_path = target_path.parent / f".{target_path.name}.backup-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        if target_path.exists():
            shutil.move(str(target_path), str(backup_path))

        try:
            shutil.move(str(extracted_root), str(target_path))
        except Exception as exc:
            if target_path.exists():
                if target_path.is_dir():
                    shutil.rmtree(target_path, ignore_errors=True)
                else:
                    target_path.unlink(missing_ok=True)
            if backup_path.exists():
                shutil.move(str(backup_path), str(target_path))
            raise NexusError(f"安装 Nexus 更新失败：{exc}") from exc
        finally:
            shutil.rmtree(install_temp, ignore_errors=True)

        if backup_path.exists():
            shutil.rmtree(backup_path, ignore_errors=True)

        return NexusDownloadResult(
            status="installed",
            downloaded_path=archive_path,
            extracted_path=extracted_root,
            installed_path=target_path,
            message=f"已安装到 {target_path}",
        )

    def download_and_install(self, record: ManagedMod, info: NexusUpdateInfo | None = None) -> NexusDownloadResult:
        """把检查、下载和安装串成完整流程。"""
        update_info = info or self.check_mod(record)
        if update_info.status not in {"outdated", "up_to_date"}:
            raise NexusError(update_info.message or "当前 Mod 没有可安装的 Nexus 更新。")

        archive_path = self.download_update(update_info)
        try:
            return self.install_download(record, archive_path)
        finally:
            shutil.rmtree(archive_path.parent, ignore_errors=True)
