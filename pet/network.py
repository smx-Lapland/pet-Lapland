"""Destination checks shared by chat and speech HTTP clients."""
import ipaddress
import time
from urllib import request
from urllib.parse import urlsplit


def validate_endpoint(base):
    url = urlsplit(base)
    if (url.scheme not in ("https", "http") or not url.hostname or url.username is not None
            or url.password is not None or "?" in base or "#" in base or "\\" in base
            or any(ch.isspace() or ord(ch) < 32 for ch in base)):
        raise ValueError("接口地址须为 HTTP(S) 基础地址，请移除凭据、查询参数和片段。")
    # Accessing port validates its syntax and range.
    _ = url.port
    if url.scheme == "http" and url.hostname != "localhost":
        try:
            local = ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            local = False
        if not local:
            raise ValueError("远程接口须使用 HTTPS。本机服务可以使用 http://127.0.0.1。")


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_api(req, *, timeout):
    validate_endpoint(req.full_url)
    handlers = [_NoRedirect()]
    if urlsplit(req.full_url).scheme == "http":
        # A local HTTP endpoint should stay local even when a system proxy is active.
        handlers.append(request.ProxyHandler({}))
    return request.build_opener(*handlers).open(req, timeout=timeout)


CHAT_LIMIT = 2 * 1024 * 1024
AUDIO_LIMIT = 16 * 1024 * 1024


def read_body(response, limit):
    data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("服务响应超过大小限制。")
    return data


def event_lines(response, timeout):
    remaining = CHAT_LIMIT
    deadline = time.monotonic() + timeout
    while True:
        line = response.readline(min(remaining, 256 * 1024) + 1)
        if not line:
            return
        if time.monotonic() > deadline or len(line) > min(remaining, 256 * 1024):
            raise ValueError("流式响应超过时长或大小限制。")
        remaining -= len(line)
        yield line
