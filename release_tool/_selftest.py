import json
import os
import tempfile
import threading

import release_tool.release_publisher as p


class FakeResp:
    def __init__(self, payload, status=201):
        self.status = status
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, payload, status=201):
        self.payload = payload
        self.status = status
        self.seen = None

    def open(self, req, timeout=None):
        body = req.data
        chunks = 0
        while True:
            piece = body.read(8192)
            if not piece:
                break
            chunks += len(piece)
        self.seen = {"chunks": chunks, "length": req.get_header("Content-length"),
                     "ctype": req.get_header("Content-type")}
        return FakeResp(self.payload, self.status)


def test_upload_streams():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "asset-v1.zip")
        with open(path, "wb") as fh:
            fh.write(b"x" * (3 * 1024 * 1024 + 17))
        events = []
        opener = FakeOpener({"name": "asset-v1.zip", "size": os.path.getsize(path)})
        asset = p.upload_asset("o", "r", 7, "tok", path, opener,
                               progress_cb=lambda f, s: events.append((f, s)))
        assert asset["name"] == "asset-v1.zip"
        assert opener.seen["chunks"] == os.path.getsize(path), opener.seen
        assert opener.seen["length"] == str(os.path.getsize(path))
        assert opener.seen["ctype"] == "application/zip"
        assert events and events[-1][0] == 1.0
        print("upload stream ok", opener.seen)


def test_upload_cancel():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "big.bin")
        with open(path, "wb") as fh:
            fh.write(b"y" * (5 * 1024 * 1024))
        cancel = threading.Event()
        cancel.set()
        try:
            p.upload_asset("o", "r", 1, "t", path, FakeOpener({}), cancel=cancel)
        except p.UploadCancelled:
            print("cancel ok")
            return
        raise AssertionError("cancel did not raise")


def test_permission_diagnosis():
    calls = []

    def fake_request(url, token, opener, **kw):
        calls.append(url)
        if url.endswith("/user"):
            return {"login": "puppy-bai"}, 200, {"x-oauth-scopes": "repo"}
        if url.endswith("/git/blobs"):
            raise RuntimeError('HTTP 403: {"message":"Resource not accessible by personal access token"}')
        return {"default_branch": "main", "permissions": {"push": True}}, 200, {}

    original = p.request_json
    p.request_json = fake_request
    try:
        info = p.check_permissions("puppy-bai", "MediaTranscriberSuite", "t", None)
    finally:
        p.request_json = original
    assert info["login"] == "puppy-bai"
    assert info["repo_ok"] is True
    assert info["can_write"] is False
    assert "workflow" in " ".join(info["notes"])
    print("permission diagnosis ok")


if __name__ == "__main__":
    test_upload_streams()
    test_upload_cancel()
    test_permission_diagnosis()
