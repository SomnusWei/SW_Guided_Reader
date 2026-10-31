from __future__ import annotations

import os
from io import BytesIO

import pytest
from pypdf import PdfWriter

from reader_service.library import LibraryService
from reader_service.storage import ManagedPaths

# The test suite talks to 127.0.0.1 only. A machine-wide proxy (HTTP_PROXY in
# CI or a local proxy client) would otherwise relay those loopback requests and
# reject headers it cannot parse, which has nothing to do with Reader behaviour.
for _proxy_bypass_name in ("NO_PROXY", "no_proxy"):
    _existing_bypass = os.environ.get(_proxy_bypass_name, "")
    if "127.0.0.1" not in _existing_bypass:
        os.environ[_proxy_bypass_name] = ",".join(
            filter(None, (_existing_bypass, "127.0.0.1,localhost"))
        )


def make_pdf(page_sizes=((612, 792),), *, encrypted=False) -> bytes:
    writer = PdfWriter()
    for width, height in page_sizes:
        writer.add_blank_page(width=width, height=height)
    if encrypted:
        writer.encrypt("secret")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def service(tmp_path):
    return LibraryService(ManagedPaths(tmp_path / "data"))
