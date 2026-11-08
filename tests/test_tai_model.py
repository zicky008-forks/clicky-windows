"""The model download used by the build retries server errors and stops at once on client errors."""
import importlib.util
import io
import urllib.error
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "tai_model", Path(__file__).resolve().parents[1] / "tools" / "giong_noi" / "tai_model.py")
tai_model = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tai_model)


def _opener(outcomes):
    calls = []

    def open_(url, timeout):
        calls.append(url)
        o = outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return io.BytesIO(o)
    return open_, calls


def _http(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, None)


def test_gateway_timeout_is_retried_then_succeeds():
    open_, calls = _opener([_http(504), urllib.error.URLError("reset"), b"ok"])
    slept = []
    assert tai_model.download("https://x", waits=(1, 2, 3), opener=open_, sleep=slept.append) == b"ok"
    assert len(calls) == 3 and slept == [1, 2]


def test_not_found_is_not_retried():
    open_, calls = _opener([_http(404)])
    with pytest.raises(urllib.error.HTTPError):
        tai_model.download("https://x", waits=(1, 2), opener=open_, sleep=lambda s: None)
    assert len(calls) == 1


def test_gives_up_after_the_last_wait():
    open_, calls = _opener([_http(503), _http(503), _http(503)])
    with pytest.raises(urllib.error.HTTPError):
        tai_model.download("https://x", waits=(1, 2), opener=open_, sleep=lambda s: None)
    assert len(calls) == 3


def test_cached_archive_is_used_without_the_network(tmp_path):
    import hashlib
    data = b"archive"
    sha = hashlib.sha256(data).hexdigest()
    (tmp_path / "m.tar.bz2").write_bytes(data)

    def offline(url):
        raise AssertionError("the network was used")
    assert tai_model.archive("https://x/m.tar.bz2", sha, tmp_path, get=offline) == data


def test_bad_cache_is_replaced_by_a_checked_download(tmp_path):
    import hashlib
    data = b"good"
    sha = hashlib.sha256(data).hexdigest()
    (tmp_path / "m.tar.bz2").write_bytes(b"corrupt")
    assert tai_model.archive("https://x/m.tar.bz2", sha, tmp_path, get=lambda u: data) == data
    assert (tmp_path / "m.tar.bz2").read_bytes() == data


def test_download_with_the_wrong_sha_is_refused_and_not_cached(tmp_path):
    with pytest.raises(SystemExit):
        tai_model.archive("https://x/m.tar.bz2", "0" * 64, tmp_path, get=lambda u: b"other")
    assert not (tmp_path / "m.tar.bz2").exists()
