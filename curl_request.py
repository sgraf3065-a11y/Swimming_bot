"""
Замена стандартного httpx-транспорта в python-telegram-bot.
Использует requests (urllib3) с новой сессией на каждый запрос —
чтобы избежать проблем с SSL keep-alive на macOS.
"""
from __future__ import annotations

import asyncio
import io
import logging
import ssl
import urllib3
from typing import Optional, Tuple

import requests
from requests.adapters import HTTPAdapter

from telegram.error import NetworkError, TimedOut
from telegram.request import BaseRequest
from telegram.request._requestdata import RequestData

logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class _TolerantSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def _make_session() -> requests.Session:
    """Новая requests.Session без keep-alive на каждый запрос."""
    s = requests.Session()
    s.verify = False
    s.headers["Connection"] = "close"
    adapter = _TolerantSSLAdapter(max_retries=1)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _do_request_sync(
    url: str,
    method: str,
    request_data: Optional[RequestData],
    timeout: float,
) -> Tuple[int, bytes]:
    """Синхронный HTTP-запрос; создаёт новую сессию без keep-alive."""
    session = _make_session()
    kwargs: dict = {"timeout": timeout}

    if request_data is None or (not request_data.contains_files and not request_data.parameters):
        kwargs["json"] = {} if method.upper() == "POST" else None

    elif request_data.contains_files:
        files: list = []
        data: dict = {}
        for param in request_data._parameters:  # noqa: SLF001
            if param.input_files:
                for ifile in param.input_files:
                    filename, content, mimetype = ifile.field_tuple
                    field = ifile.attach_name or param.name
                    files.append((field, (filename or field, io.BytesIO(content), mimetype)))
            elif param.value is not None:
                jv = param.json_value
                if jv is not None:
                    data[param.name] = jv
        kwargs["files"] = files
        kwargs["data"] = data

    else:
        kwargs["json"] = request_data.parameters

    try:
        resp = session.request(method.upper(), url, **kwargs)
        return resp.status_code, resp.content
    except requests.exceptions.Timeout as exc:
        raise TimedOut from exc
    except requests.exceptions.ConnectionError as exc:
        raise NetworkError(str(exc)) from exc
    except requests.exceptions.RequestException as exc:
        raise NetworkError(str(exc)) from exc
    finally:
        session.close()


class CurlRequest(BaseRequest):
    """BaseRequest через requests (новая сессия на каждый запрос)."""

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: Optional[RequestData] = None,
        read_timeout=BaseRequest.DEFAULT_NONE,
        write_timeout=BaseRequest.DEFAULT_NONE,
        connect_timeout=BaseRequest.DEFAULT_NONE,
        pool_timeout=BaseRequest.DEFAULT_NONE,
    ) -> Tuple[int, bytes]:
        timeout = 65.0
        if read_timeout is not BaseRequest.DEFAULT_NONE and read_timeout is not None:
            try:
                timeout = float(read_timeout) + 10.0
            except (TypeError, ValueError):
                pass

        try:
            return await asyncio.to_thread(
                _do_request_sync, url, method, request_data, timeout
            )
        except (TimedOut, NetworkError):
            raise
        except Exception as exc:
            raise NetworkError(str(exc)) from exc
