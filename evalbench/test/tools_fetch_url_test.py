import io
import os
import socket
import sys
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.models.tools.fetch_url import fetch_url  # noqa: E402


def _public_addrinfo():
    """getaddrinfo response for a routable public IP. Avoids documentation
    ranges (198.51.100/24, 203.0.113/24) which Python's ipaddress module
    classifies as private."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


def _private_addrinfo():
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0))]


def _loopback_addrinfo():
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]


def _mock_response(body: bytes, content_type: str = "text/plain"):
    resp = MagicMock()
    resp.read.return_value = body
    resp.headers = {"Content-Type": content_type}
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda *a: False
    return resp


def test_missing_url_returns_error():
    result = fetch_url({})
    assert result.startswith("Error: missing required arg 'url'")


def test_non_https_scheme_rejected():
    result = fetch_url({"url": "http://example.com/foo"})
    assert "only https URLs are allowed" in result


def test_file_scheme_rejected():
    result = fetch_url({"url": "file:///etc/passwd"})
    assert "only https URLs are allowed" in result


@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_private_ip_rejected(mock_getaddrinfo):
    mock_getaddrinfo.return_value = _private_addrinfo()
    result = fetch_url({"url": "https://internal.corp/secret"})
    assert "private, loopback" in result


@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_loopback_rejected(mock_getaddrinfo):
    mock_getaddrinfo.return_value = _loopback_addrinfo()
    result = fetch_url({"url": "https://localhost/admin"})
    assert "private, loopback" in result


@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_unresolvable_host_rejected(mock_getaddrinfo):
    mock_getaddrinfo.side_effect = socket.gaierror("nope")
    result = fetch_url({"url": "https://does-not-exist.invalid/"})
    assert "private, loopback, link-local, or unresolvable" in result


@patch("generators.models.tools.fetch_url.urllib.request.urlopen")
@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_plain_text_response_returned(mock_getaddrinfo, mock_urlopen):
    mock_getaddrinfo.return_value = _public_addrinfo()
    mock_urlopen.return_value = _mock_response(
        b"Apache Beam 2.99.0 released",
        content_type="text/plain",
    )
    result = fetch_url({"url": "https://beam.apache.org/version"})
    assert result == "Apache Beam 2.99.0 released"


@patch("generators.models.tools.fetch_url.urllib.request.urlopen")
@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_html_response_is_stripped(mock_getaddrinfo, mock_urlopen):
    mock_getaddrinfo.return_value = _public_addrinfo()
    html = (
        b"<html><head><style>body{}</style></head><body>"
        b"<h1>Downloads</h1><p>Latest: <b>2.99.0</b></p>"
        b"<script>alert('hi')</script></body></html>"
    )
    mock_urlopen.return_value = _mock_response(html, content_type="text/html")
    result = fetch_url({"url": "https://beam.apache.org/get-started/downloads/"})
    assert "Downloads" in result
    assert "2.99.0" in result
    assert "alert" not in result
    assert "<h1>" not in result


@patch("generators.models.tools.fetch_url.urllib.request.urlopen")
@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_oversized_body_is_truncated(mock_getaddrinfo, mock_urlopen):
    mock_getaddrinfo.return_value = _public_addrinfo()
    big_body = b"x" * 60_000
    mock_urlopen.return_value = _mock_response(big_body, content_type="text/plain")
    result = fetch_url({"url": "https://example.com/big"})
    assert "[truncated at 50000 bytes]" in result
    assert len(result) <= 50_000 + len("\n\n[truncated at 50000 bytes]")


@patch("generators.models.tools.fetch_url.urllib.request.urlopen")
@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_http_error_returned_as_string(mock_getaddrinfo, mock_urlopen):
    mock_getaddrinfo.return_value = _public_addrinfo()
    mock_urlopen.side_effect = urllib.error.HTTPError(
        "https://example.com/missing", 404, "Not Found", {}, io.BytesIO()
    )
    result = fetch_url({"url": "https://example.com/missing"})
    assert "HTTP 404" in result


@patch("generators.models.tools.fetch_url.urllib.request.urlopen")
@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_timeout_returned_as_string(mock_getaddrinfo, mock_urlopen):
    mock_getaddrinfo.return_value = _public_addrinfo()
    mock_urlopen.side_effect = socket.timeout()
    result = fetch_url({"url": "https://example.com/slow"})
    assert "timeout after" in result


@patch("generators.models.tools.fetch_url.urllib.request.urlopen")
@patch("generators.models.tools.fetch_url.socket.getaddrinfo")
def test_url_error_returned_as_string(mock_getaddrinfo, mock_urlopen):
    mock_getaddrinfo.return_value = _public_addrinfo()
    mock_urlopen.side_effect = urllib.error.URLError("connection refused")
    result = fetch_url({"url": "https://example.com/down"})
    assert "URL error" in result
    assert "connection refused" in result
