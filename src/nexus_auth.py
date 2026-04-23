from __future__ import annotations

import json
import time
import uuid
import webbrowser
from dataclasses import dataclass


NEXUS_SSO_WS_URL = "wss://sso.nexusmods.com"
NEXUS_SSO_WEB_URL = "https://www.nexusmods.com/sso"


@dataclass(slots=True)
class NexusAuthResult:
    """保存一次 Nexus SSO 获取 API Key 的结果。"""

    api_key: str | None = None
    sso_url: str = ""
    message: str = ""
    error: str | None = None


class NexusAuthError(RuntimeError):
    """表示 Nexus SSO 获取流程中的可预期错误。"""


class NexusAuthSession:
    """封装 Stardrop 风格的 Nexus SSO 获取流程。"""

    def __init__(self, application_slug: str = "stardewvalleytools") -> None:
        """初始化一次 SSO 会话。"""
        self.connection_uuid = str(uuid.uuid4())
        self.application_slug = application_slug
        self.sso_url = f"{NEXUS_SSO_WEB_URL}?id={self.connection_uuid}&application={self.application_slug}"

    def open_browser(self) -> None:
        """打开 Nexus SSO 页面。"""
        webbrowser.open_new_tab(self.sso_url)

    def _create_socket(self, timeout: int):
        """延迟导入 websocket 客户端，避免主程序在未安装依赖时直接启动失败。"""
        try:
            import websocket  # type: ignore
        except Exception as exc:  # pragma: no cover - 仅在缺少依赖时触发。
            raise NexusAuthError("缺少 websocket-client 依赖，请先安装 requirements.txt 中的依赖。") from exc

        socket = websocket.create_connection(NEXUS_SSO_WS_URL, timeout=timeout)
        socket.settimeout(timeout)
        return socket

    def acquire_api_key(self, timeout: int = 120) -> NexusAuthResult:
        """连接 Nexus SSO websocket 并等待 API Key 返回。"""
        result = NexusAuthResult(sso_url=self.sso_url)
        socket = None

        try:
            self.open_browser()
            socket = self._create_socket(timeout)
            payload = json.dumps({"id": self.connection_uuid, "token": None, "protocol": 2})
            socket.send(payload)

            start = time.monotonic()
            while time.monotonic() - start < timeout:
                try:
                    raw_message = socket.recv()
                except Exception as exc:
                    raise NexusAuthError(f"等待 Nexus 返回 API Key 超时或连接失败：{exc}") from exc

                if not raw_message:
                    continue

                try:
                    response = json.loads(raw_message)
                except json.JSONDecodeError:
                    continue

                if not isinstance(response, dict):
                    continue

                if not response.get("success"):
                    continue

                data = response.get("data")
                if not isinstance(data, dict):
                    continue

                api_key = data.get("ApiKey") or data.get("apiKey")
                connection_token = data.get("ConnectionToken") or data.get("connectionToken")

                if connection_token is not None and api_key is None:
                    continue

                if isinstance(api_key, str) and api_key.strip():
                    result.api_key = api_key.strip()
                    result.message = "已成功获取 Nexus API Key。"
                    return result

            raise NexusAuthError("等待 Nexus 返回 API Key 超时。")
        except NexusAuthError as exc:
            result.error = str(exc)
            result.message = str(exc)
            return result
        except Exception as exc:
            result.error = str(exc)
            result.message = str(exc)
            return result
        finally:
            if socket is not None:
                try:
                    socket.close()
                except Exception:
                    pass
