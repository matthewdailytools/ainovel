"""Start the standalone synchronization server with an explicit transport boundary."""

import argparse
from ipaddress import ip_address
import os
from pathlib import Path
import ssl

import uvicorn

from .cloud_sync import sync_app_from_env
from .db import DomainError


def main(argv=None):
    parser = argparse.ArgumentParser(description="启动独立的作品云同步服务。")
    parser.add_argument("--host", default="127.0.0.1", help="监听 IP，默认绑定 IPv4 回环地址。")
    parser.add_argument("--port", type=int, default=8888, help="监听端口，默认 8888。")
    parser.add_argument("--tls-certfile", type=Path, help="PEM 格式的服务端证书与证书链。")
    parser.add_argument("--tls-keyfile", type=Path, help="与服务端证书匹配的私钥文件。")
    args = parser.parse_args(argv)

    try:
        host = ip_address(args.host)
    except ValueError:
        parser.error("监听地址须使用明确的 IPv4 或 IPv6 地址。")
    if not 1 <= args.port <= 65_535:
        parser.error("监听端口须在 1 到 65535 之间。")
    if bool(args.tls_certfile) != bool(args.tls_keyfile):
        parser.error("TLS 证书和私钥须同时提供。")
    if not host.is_loopback and args.tls_certfile is None:
        parser.error("非回环地址必须配置 TLS 证书和私钥。")

    config = uvicorn.Config(
        sync_app_from_env,
        factory=True,
        host=str(host),
        port=args.port,
        access_log=False,
        proxy_headers=False,
        ssl_certfile=args.tls_certfile,
        ssl_keyfile=args.tls_keyfile,
        ssl_keyfile_password=os.environ.get("AINOVEL_SYNC_TLS_KEY_PASSWORD"),
        ssl_version=ssl.PROTOCOL_TLS_SERVER,
    )
    try:
        config.load()
    except OSError, ValueError, DomainError:
        parser.error("无法加载同步服务配置。请核对独立数据库、开通凭据、浏览器来源和 TLS 文件。")
    if config.ssl is not None:
        config.ssl.minimum_version = ssl.TLSVersion.TLSv1_2
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
