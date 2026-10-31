from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import numpy as np

from .contracts import DetectedLine, PagePreparationError
from .geometry import clamp, normalize_quad


class UmiOcrError(RuntimeError):
    """Transport or protocol failure while talking to the Umi-OCR service."""


@dataclass(frozen=True)
class UmiOcrConfig:
    endpoint: str = "http://127.0.0.1:1224"
    disabled: bool = False
    language: str = ""
    timeout_seconds: float = 120.0

    @classmethod
    def from_environment(cls) -> "UmiOcrConfig":
        return cls(
            endpoint=os.environ.get("GUIDED_READER_UMI_OCR_ENDPOINT", "http://127.0.0.1:1224").strip(),
            disabled=os.environ.get("GUIDED_READER_UMI_OCR_DISABLED", "").strip() == "1",
            language=os.environ.get("GUIDED_READER_UMI_OCR_LANGUAGE", "").strip(),
            timeout_seconds=_environment_float("GUIDED_READER_UMI_OCR_TIMEOUT_SECONDS", 120.0),
        )

    def api_url(self) -> str:
        split = urlsplit(self.endpoint)
        if split.scheme not in ("http", "https") or not split.hostname:
            raise UmiOcrError(f"Invalid Umi-OCR endpoint: {self.endpoint}")
        root = split.path.rstrip("/")
        return f"{split.scheme}://{split.netloc}{root}/api/ocr"


def _environment_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


class UmiOcrClient:
    """Serial HTTP client for a locally installed Umi-OCR service.

    Umi-OCR handles concurrent requests poorly, so all recognition goes
    through one lock; a page at a time, never parallel calls.
    """

    def __init__(
        self,
        config: UmiOcrConfig | None = None,
        *,
        connect_timeout: float = 1.0,
    ):
        self._config = config or UmiOcrConfig.from_environment()
        self._connect_timeout = connect_timeout
        self._lock = threading.Lock()

    @property
    def config(self) -> UmiOcrConfig:
        return self._config

    def is_available(self, timeout: float | None = None) -> bool:
        """The port answering is enough; GET / may legitimately return 404."""
        try:
            split = urlsplit(self._config.endpoint)
            host, port = split.hostname, split.port or (443 if split.scheme == "https" else 80)
        except ValueError:
            return False
        if not host:
            return False
        try:
            with socket.create_connection((host, port), timeout or self._connect_timeout):
                return True
        except OSError:
            return False

    def recognize_png(self, png_bytes: bytes, options: dict | None = None) -> list[dict]:
        """Recognize one PNG page; returns Umi-OCR text blocks as plain records.

        Each record is {"text": str, "box": [[x, y] x 4], "score": float} with
        box corners in the source image pixel coordinates.
        """
        payload_options = {"tbpu.parser": "multi_para"}
        if self._config.language:
            payload_options["ocr.language"] = self._config.language
        if options:
            payload_options.update(options)
        payload = json.dumps({
            "base64": base64.b64encode(png_bytes).decode("ascii"),
            "options": payload_options,
        }).encode("utf-8")
        result = self._post_with_retry(payload)
        data = result.get("data")
        if result.get("code") != 100 or not isinstance(data, list):
            raise UmiOcrError(f"Umi-OCR returned an unexpected response: {result!r}")
        blocks = []
        for item in data:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", ""))
            box = item.get("box")
            if not text.strip() or not isinstance(box, list) or len(box) != 4:
                continue
            blocks.append({
                "text": text,
                "box": [[float(point[0]), float(point[1])] for point in box],
                "score": clamp(item.get("score", 1.0)),
            })
        return blocks

    def _post_with_retry(self, payload: bytes) -> dict:
        attempts = 2
        with self._lock:
            for attempt in range(1, attempts + 1):
                try:
                    return self._post(payload)
                except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                    # Transient refusals happen when the service is starting up.
                    if attempt == attempts:
                        raise UmiOcrError(f"Umi-OCR request failed: {exc}") from exc

    def _post(self, payload: bytes) -> dict:
        request = urllib.request.Request(
            self._config.api_url(),
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._config.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def encode_png(image) -> bytes:
    """Encode a numpy page image (BGR/BGRA/gray) as a PNG without Pillow."""
    array = np.asarray(image)
    if array.ndim == 3 and array.shape[2] >= 3:
        array = array[:, :, :3, ...][:, :, ::-1]  # pypdfium2 renders BGR(A); PNG wants RGB
        color_type, channels = 2, 3
    elif array.ndim == 2:
        color_type, channels = 0, 1
    else:
        raise UmiOcrError(f"Unsupported page image shape: {array.shape}")
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    height, width = array.shape[:2]
    array = np.ascontiguousarray(array)
    stride = width * channels
    raw = array.tobytes()
    scanlines = b"".join(
        b"\x00" + raw[offset : offset + stride] for offset in range(0, len(raw), stride)
    )
    compressed = zlib.compress(scanlines, 6)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


class UmiOcrEngine:
    """Foundation OcrEngine backed by an external Umi-OCR HTTP service."""

    def __init__(self, client: UmiOcrClient | None = None):
        self._client = client or UmiOcrClient(UmiOcrConfig.from_environment())

    @property
    def profile(self) -> str:
        config = self._client.config
        language = config.language or "default"
        return f"umi-ocr:http:{language}:{config.endpoint}"

    def prepare_page(
        self, page_image: object, page_size: tuple[int, int]
    ) -> list[DetectedLine]:
        if self._client.config.disabled:
            raise PagePreparationError(
                "OCR_UNAVAILABLE", "Umi-OCR is disabled by configuration"
            )
        width, height = page_size
        png = encode_png(page_image)
        try:
            blocks = self._client.recognize_png(png)
        except UmiOcrError as exc:
            raise PagePreparationError("OCR_UNAVAILABLE", str(exc)) from exc
        return [_translate_block(block, width, height) for block in blocks]


def _translate_block(block: dict, width: int, height: int) -> DetectedLine:
    box = block["box"]
    text = block["text"]
    cells = _even_cells(box, text, width)
    return DetectedLine(
        quad=normalize_quad(box, width, height),
        text=text,
        confidence=block["score"],
        cells=cells,
    )


def _even_cells(box, text: str, width: int):
    """Umi-OCR gives whole-block geometry only; distribute cells evenly in x."""
    length = len(text)
    if not length:
        return ()
    xs = [float(point[0]) for point in box]
    x0, x1 = min(xs), max(xs)
    return tuple(
        (
            clamp((x0 + (x1 - x0) * index / length) / width),
            clamp((x0 + (x1 - x0) * (index + 1) / length) / width),
            index,
            index + 1,
        )
        for index in range(length)
    )


def umi_availability(config: UmiOcrConfig | None = None) -> dict:
    """Probe used by the preparation status endpoint."""
    resolved = config or UmiOcrConfig.from_environment()
    if resolved.disabled:
        return {"backend": "umi-ocr", "available": False}
    client = UmiOcrClient(resolved)
    return {"backend": "umi-ocr", "available": client.is_available()}
