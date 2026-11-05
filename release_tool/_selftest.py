import json
import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import release_tool.release_publisher as p
import release_tool.token_store as token_store


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


def test_validation_without_tag():
    app = object.__new__(p.ReleasePublisherApp)
    app.token_var = Mock(get=Mock(return_value="test-token"))
    app.repo_var = Mock(get=Mock(return_value=p.DEFAULT_REPO))
    app.tag_var = Mock(get=Mock(return_value=""))
    app._current_proxy = lambda: ("direct", "")
    assert app._validate_common() == ("test-token", p.DEFAULT_REPO, "direct", "")
    try:
        app._validate_publish()
    except ValueError as e:
        assert "Tag" in str(e)
    else:
        raise AssertionError("publishing without tag should be rejected")
    print("self-check accepts empty tag; publish rejects it")


def test_dpapi_storage():
    with tempfile.TemporaryDirectory() as tmp:
        fp = Path(tmp) / "config.json"
        assert token_store.load_token(fp) is None
        token_store.store_token(fp, "test-not-a-real-token")
        encrypted = token_store.token_file(fp).read_bytes()
        assert b"test-not-a-real-token" not in encrypted
        assert token_store.load_token(fp) == "test-not-a-real-token"
        token_store.forget_token(fp)
        assert token_store.load_token(fp) is None
    print("DPAPI save/load/delete ok")


def test_create_repository_payload():
    calls = []

    def fake_request(url, token, opener, **kw):
        calls.append((url, kw.get("method"), kw.get("body")))
        return {"full_name": "puppy-bai/NewRepo"}, 201, {}

    original = p.request_json
    p.request_json = fake_request
    try:
        obj = p.create_repository("t", None, name="NewRepo", description="demo",
                                  private=True, auto_init=False)
    finally:
        p.request_json = original
    url, method, body = calls[0]
    assert url.endswith("/user/repos") and method == "POST"
    assert body["name"] == "NewRepo" and body["private"] is True
    assert body["auto_init"] is False and body["description"] == "demo"
    assert obj["full_name"] == "puppy-bai/NewRepo"
    print("create repository request ok")


def test_repo_upload_existing_branch():
    """An existing branch retains its base tree and updates only its ref."""
    calls = []
    source_files = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sample.txt"
        path.write_bytes(b"hello repository")
        source_files.append((str(path), "docs/sample.txt"))
        replies = {
            "GET /git/ref/heads/main": {"object": {"sha": "old-commit"}},
            "GET /git/commits/old-commit": {"tree": {"sha": "old-tree"}},
            "POST /git/blobs": {"sha": "new-blob"},
            "POST /git/trees": {"sha": "new-tree"},
            "POST /git/commits": {"sha": "new-commit"},
            "PATCH /git/refs/heads/main": {"object": {"sha": "new-commit"}},
        }

        def fake_request(url, token, opener, **kw):
            assert url.startswith(p.GITHUB_API + "/repos/puppy-bai/MediaTranscriberSuite/")
            method = kw.get("method", "GET")
            route = url.split("/repos/puppy-bai/MediaTranscriberSuite", 1)[1]
            calls.append((method, route, kw.get("body")))
            return replies[f"{method} {route}"], 200, {}

        with patch.object(p, "request_json", side_effect=fake_request):
            result = p.upload_files_to_repo("puppy-bai", "MediaTranscriberSuite", "t", None,
                                            source_files, target_dir="uploads")
        assert result["commit"] == "new-commit" and result["count"] == 1
        assert source_files == [(str(path), "docs/sample.txt")], "input list was mutated"
        assert [route for _, route, _ in calls] == [
            "/git/ref/heads/main", "/git/commits/old-commit", "/git/blobs", "/git/trees",
            "/git/commits", "/git/refs/heads/main"]
        assert calls[3][2]["base_tree"] == "old-tree"
        assert calls[3][2]["tree"][0]["path"] == "uploads/docs/sample.txt"
        assert calls[4][2]["parents"] == ["old-commit"]
        assert calls[5][0] == "PATCH" and calls[5][2]["force"] is False
    print("repository upload preserves existing branch")


def test_repo_upload_missing_branch_and_missing_repo():
    """Only a verified missing branch may be created; inaccessible repo fails early."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sample.txt"
        path.write_text("test")
        files = [(str(path), "sample.txt")]
        calls = []

        def fake_request(url, token, opener, **kw):
            method = kw.get("method", "GET")
            calls.append((method, url, kw.get("body")))
            if url.endswith("/git/ref/heads/new-branch"):
                raise RuntimeError('HTTP 404: {"message":"Not Found"}')
            if url.endswith("/git/blobs"):
                return {"sha": "blob"}, 201, {}
            if url.endswith("/git/trees"):
                return {"sha": "tree"}, 201, {}
            if url.endswith("/git/commits"):
                return {"sha": "commit"}, 201, {}
            if url.endswith("/git/refs"):
                return {"ref": "refs/heads/new-branch"}, 201, {}
            if url.endswith("/repos/puppy-bai/MediaTranscriberSuite"):
                return {"full_name": "puppy-bai/MediaTranscriberSuite"}, 200, {}
            raise AssertionError(url)

        with patch.object(p, "request_json", side_effect=fake_request):
            p.upload_files_to_repo("puppy-bai", "MediaTranscriberSuite", "t", None, files,
                                   branch="new-branch")
        assert calls[1][1].endswith("/repos/puppy-bai/MediaTranscriberSuite")
        assert calls[-1][0] == "POST" and calls[-1][1].endswith("/git/refs")
        assert calls[-1][2]["ref"] == "refs/heads/new-branch"

        def missing_repo(url, token, opener, **kw):
            calls.append((kw.get("method", "GET"), url, kw.get("body")))
            raise RuntimeError('HTTP 404: {"message":"Not Found"}')

        calls.clear()
        with patch.object(p, "request_json", side_effect=missing_repo):
            try:
                p.upload_files_to_repo("puppy-bai", "missing", "t", None, files)
            except RuntimeError as e:
                assert "puppy-bai/missing" in str(e)
            else:
                raise AssertionError("inaccessible repository was treated as a new branch")
        assert len(calls) == 2 and all(method == "GET" for method, _, _ in calls)
    print("missing branch and inaccessible repository distinguished")


def test_gui_repo_upload_routes():
    """Exercise the button callback to catch owner/name duplication before release."""
    app = object.__new__(p.ReleasePublisherApp)
    app._repo_files = [("local-file.txt", "docs/local-file.txt")]
    app._busy = False
    app._validate_common = lambda: ("test-token", "puppy-bai/MediaTranscriberSuite", "direct", "")
    app.branch_var = Mock(get=Mock(return_value="main"))
    app.repo_dir_var = Mock(get=Mock(return_value="docs"))
    app.commit_msg_var = Mock(get=Mock(return_value="test"))
    app._set_busy = Mock()
    app._cancel_evt = threading.Event()
    app._reset_progress = Mock()
    app._set_progress = Mock()
    app._persist_token_if_selected = Mock()
    app._log = Mock()
    app._run_async = lambda worker, success, error, status: success(worker())
    with patch.object(p, "build_opener", return_value=object()), \
         patch.object(p, "upload_files_to_repo", return_value={
             "count": 1, "branch": "main", "url": "https://github.com/example/commit/abc",
         }) as upload, patch.object(p.messagebox, "showinfo"):
        app._push_files_to_repo()
    args, kwargs = upload.call_args
    assert args[:3] == ("puppy-bai", "MediaTranscriberSuite", "test-token"), args
    assert kwargs["branch"] == "main" and kwargs["target_dir"] == "docs"
    print("GUI repository upload passes separate owner and repo")


if __name__ == "__main__":
    test_repo_upload_existing_branch()
    test_repo_upload_missing_branch_and_missing_repo()
    test_gui_repo_upload_routes()
    test_upload_streams()
    test_upload_cancel()
    test_permission_diagnosis()
    test_validation_without_tag()
    test_dpapi_storage()
    test_create_repository_payload()
