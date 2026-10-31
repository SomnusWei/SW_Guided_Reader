from __future__ import annotations

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from reader_service.foundation.contracts import PagePreparationError
from reader_service.foundation.umi_ocr import (
    UmiOcrClient,
    UmiOcrConfig,
    UmiOcrEngine,
    UmiOcrError,
    encode_png,
    umi_availability,
)


BLOCKS = [
    {
        "text": "第五章 保险市场引论",
        "box": [[10, 20], [300, 20], [300, 40], [10, 40]],
        "score": 0.97,
    },
    {
        "text": "5.1 风险与保险",
        "box": [[10, 60], [200, 60], [200, 80], [10, 80]],
        "score": 0.88,
    },
]


class FakeUmiServer:
    """A minimal stand-in for the Umi-OCR HTTP service."""

    def __init__(self, *, fail_first_post: bool = False, delay: float = 0.0):
        outer = self
        outer.fail_first_post = fail_first_post
        outer.delay = delay
        outer.post_count = 0
        outer.last_body = None
        outer.active = 0
        outer.max_active = 0

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                outer.last_body = json.loads(self.rfile.read(length))
                outer.post_count += 1
                if outer.fail_first_post and outer.post_count == 1:
                    self.close_connection = True
                    try:
                        self.connection.shutdown(2)
                    except OSError:
                        pass
                    return
                if outer.delay:
                    time.sleep(outer.delay)
                outer.active += 1
                outer.max_active = max(outer.max_active, outer.active)
                time.sleep(0.05)
                outer.active -= 1
                payload = json.dumps({"code": 100, "data": BLOCKS}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):  # noqa: N802
                self.send_response(404)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_umi():
    server = FakeUmiServer()
    yield server
    server.stop()


def make_config(endpoint: str, **overrides) -> UmiOcrConfig:
    return UmiOcrConfig(endpoint=endpoint, **overrides)


def test_recognize_png_sends_base64_and_parses_blocks(fake_umi):
    client = UmiOcrClient(make_config(fake_umi.endpoint))
    blocks = client.recognize_png(b"fake-png-bytes")

    assert client.config.api_url().endswith("/api/ocr")
    decoded = base64.b64decode(fake_umi.last_body["base64"])
    assert decoded == b"fake-png-bytes"
    assert fake_umi.last_body["options"]["tbpu.parser"] == "multi_para"
    assert "ocr.language" not in fake_umi.last_body["options"]
    assert blocks == BLOCKS


def test_language_option_is_forwarded(fake_umi):
    client = UmiOcrClient(make_config(fake_umi.endpoint, language="en"))
    client.recognize_png(b"png")
    assert fake_umi.last_body["options"]["ocr.language"] == "en"


def test_caller_options_override_defaults(fake_umi):
    client = UmiOcrClient(make_config(fake_umi.endpoint))
    client.recognize_png(b"png", options={"tbpu.parser": "multi_line"})
    assert fake_umi.last_body["options"]["tbpu.parser"] == "multi_line"


def test_transient_connection_failure_is_retried_once():
    client = UmiOcrClient(make_config("http://127.0.0.1:1224"))
    attempts = []
    payloads = [ConnectionRefusedError("ECONNREFUSED"), {"code": 100, "data": []}]

    def flaky_post(payload):
        attempts.append(payload)
        outcome = payloads.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client._post = flaky_post
    assert client.recognize_png(b"png") == []
    assert len(attempts) == 2


def test_persistent_failure_stops_after_two_attempts():
    client = UmiOcrClient(make_config("http://127.0.0.1:1224"))
    attempts = []

    def refused_post(payload):
        attempts.append(payload)
        raise ConnectionRefusedError("ECONNREFUSED")

    client._post = refused_post
    with pytest.raises(UmiOcrError):
        client.recognize_png(b"png")
    assert len(attempts) == 2


def test_protocol_failure_is_not_retried():
    client = UmiOcrClient(make_config("http://127.0.0.1:1224"))
    calls = []

    def bad_code(payload):
        calls.append(payload)
        return {"code": 101, "data": None}

    client._post = bad_code
    with pytest.raises(UmiOcrError):
        client.recognize_png(b"png")
    assert len(calls) == 1


def test_requests_are_serialized():
    client = UmiOcrClient(make_config("http://127.0.0.1:1224"))
    state = {"active": 0, "max_active": 0}

    def slow_post(payload):
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.05)
        state["active"] -= 1
        return {"code": 100, "data": []}

    client._post = slow_post
    threads = [
        threading.Thread(target=lambda: client.recognize_png(b"png")) for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert state["max_active"] == 1


def test_recognize_png_times_out():
    server = FakeUmiServer(delay=2.0)
    try:
        client = UmiOcrClient(make_config(server.endpoint, timeout_seconds=0.2))
        with pytest.raises(UmiOcrError):
            client.recognize_png(b"png")
    finally:
        server.stop()


def test_is_available_reflects_port_state(fake_umi):
    assert UmiOcrClient(make_config(fake_umi.endpoint)).is_available() is True
    closed = make_config("http://127.0.0.1:1")
    assert UmiOcrClient(closed).is_available() is False


def test_config_from_environment(monkeypatch):
    monkeypatch.setenv("GUIDED_READER_UMI_OCR_ENDPOINT", "http://127.0.0.1:9999/")
    monkeypatch.setenv("GUIDED_READER_UMI_OCR_DISABLED", "1")
    monkeypatch.setenv("GUIDED_READER_UMI_OCR_LANGUAGE", "en")
    monkeypatch.setenv("GUIDED_READER_UMI_OCR_TIMEOUT_SECONDS", "30")
    config = UmiOcrConfig.from_environment()
    assert config.endpoint == "http://127.0.0.1:9999/"
    assert config.disabled is True
    assert config.language == "en"
    assert config.timeout_seconds == 30.0
    assert config.api_url() == "http://127.0.0.1:9999/api/ocr"


def test_config_defaults_keep_umi_enabled(monkeypatch):
    for name in (
        "GUIDED_READER_UMI_OCR_ENDPOINT",
        "GUIDED_READER_UMI_OCR_DISABLED",
        "GUIDED_READER_UMI_OCR_LANGUAGE",
        "GUIDED_READER_UMI_OCR_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    config = UmiOcrConfig.from_environment()
    assert config.endpoint == "http://127.0.0.1:1224"
    assert config.disabled is False
    assert config.language == ""
    assert config.timeout_seconds == 120.0


def test_encode_png_round_trips_through_umi_color_channels():
    image = np.zeros((4, 6, 4), dtype=np.uint8)
    image[:, :, 0] = 200  # pypdfium2 renders BGR(A); the blue channel is first
    png = encode_png(image)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")

    import zlib

    offset = 8
    idat = b""
    while offset < len(png):
        length = int.from_bytes(png[offset : offset + 4], "big")
        tag = png[offset + 4 : offset + 8]
        chunk_data = png[offset + 8 : offset + 8 + length]
        if tag == b"IDAT":
            idat += chunk_data
        offset += 12 + length
    raw = zlib.decompress(idat, wbits=15)
    assert len(raw) == 4 * (1 + 6 * 3)
    assert raw[1:4] == b"\x00\x00\xc8"


def test_engine_translates_blocks_into_detected_lines(fake_umi):
    engine = UmiOcrEngine(UmiOcrClient(make_config(fake_umi.endpoint)))
    image = np.full((100, 400, 3), 255, dtype=np.uint8)
    lines = engine.prepare_page(image, (400, 100))

    assert [line.text for line in lines] == [block["text"] for block in BLOCKS]
    assert [line.confidence for line in lines] == [0.97, 0.88]
    first = lines[0]
    assert first.quad == (
        (10 / 400, 20 / 100),
        (300 / 400, 20 / 100),
        (300 / 400, 40 / 100),
        (10 / 400, 40 / 100),
    )
    assert len(first.cells) == len(first.text)
    assert all(
        0.0 <= cell[0] <= cell[1] <= 1.0 and cell[2] < cell[3]
        for cell in first.cells
    )
    assert [cell[2:] for cell in first.cells] == [
        (index, index + 1) for index in range(len(first.text))
    ]


def test_engine_profile_mentions_service_and_language(fake_umi):
    engine = UmiOcrEngine(UmiOcrClient(make_config(fake_umi.endpoint, language="en")))
    assert engine.profile == f"umi-ocr:http:en:{fake_umi.endpoint}"


def test_disabled_engine_fails_pages_without_any_request(fake_umi):
    engine = UmiOcrEngine(UmiOcrClient(make_config(fake_umi.endpoint, disabled=True)))
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    with pytest.raises(PagePreparationError) as excinfo:
        engine.prepare_page(image, (10, 10))
    assert excinfo.value.code == "OCR_UNAVAILABLE"
    assert fake_umi.post_count == 0


def test_unreachable_service_marks_page_as_ocr_unavailable():
    engine = UmiOcrEngine(UmiOcrClient(make_config("http://127.0.0.1:1")))
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    with pytest.raises(PagePreparationError) as excinfo:
        engine.prepare_page(image, (10, 10))
    assert excinfo.value.code == "OCR_UNAVAILABLE"


def test_availability_report(fake_umi):
    report = umi_availability(make_config(fake_umi.endpoint))
    assert report == {"backend": "umi-ocr", "available": True}
    disabled = umi_availability(make_config(fake_umi.endpoint, disabled=True))
    assert disabled == {"backend": "umi-ocr", "available": False}
    closed = umi_availability(make_config("http://127.0.0.1:1"))
    assert closed == {"backend": "umi-ocr", "available": False}
