"""Reuse HTTPX's packaged CA bundle for short-lived HTTP/1.1 clients."""

import os
import ssl
from functools import cache

import httpx


@cache
def _bundled_context() -> ssl.SSLContext:
    return httpx.create_ssl_context(trust_env=False)


def default_verification() -> ssl.SSLContext | bool:
    # Let HTTPX load deployment-specific trust roots normally, including
    # changes to the contents of a configured certificate file.
    if os.environ.get('SSL_CERT_FILE') or os.environ.get('SSL_CERT_DIR'):
        return True
    return _bundled_context()
