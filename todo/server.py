"""TODO CLI のローカルWebサーバ(`todo serve`)。

Python標準ライブラリの http.server(ThreadingHTTPServer / BaseHTTPRequestHandler)
のみで実装する。DESIGN.md 4章・8章・9章に準拠する。

公開関数のシグネチャ(8.4節、契約):
    create_server(host="127.0.0.1", port=8765) -> http.server.HTTPServer
        bindのみ行い、serve_forever() は呼ばない。
    run_server(host="127.0.0.1", port=8765) -> None
        create_server() で生成し、ポート番号を標準出力に出力してから serve_forever() でブロックする。

状態変更系(add/edit/done/undone/remove)は todo/storage.py の公開関数を直接呼び出し、
CLI(todo/cli.py)には依存しない。バリデーション・エラーメッセージ・ID採番はCLIと共通になる。
"""

import http.server
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse

from todo import storage

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

LOCK_RETRY_ATTEMPTS = 5
LOCK_RETRY_INTERVAL_SECONDS = 0.1

_TODO_ID_RE = re.compile(r"^/api/todos/(\d+)$")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


def _item_to_json(item):
    # item は storage 層で読み込んだ dict をそのまま保持している
    # (ラウンドトリップ安全性、DESIGN.md 3章)ため、そのままシリアライズしてよい。
    # "overdue" はブラウザ画面(8.1節)の「(期限切れ)」表示のための計算済みフィールド
    # (storage.is_overdue()と同じ判定をクライアント側で再計算させない、5.9節参照)。
    result = dict(item)
    result["overdue"] = storage.is_overdue(item)
    return result


def _run_with_write_lock(server, func, *args, **kwargs):
    """threading.Lock でプロセス内直列化しつつ func を呼び出す。

    func はストレージ層の関数(add/edit/done/undone/remove)で、内部で
    ファイルロックの取得・読み込み・更新・書き込み・解放を行う。
    ファイルロック取得に失敗した場合は短い間隔でリトライし、それでも
    失敗すれば LockAcquisitionError をそのまま送出する(呼び出し元で503に変換する)。
    """
    with server.write_lock:
        last_error = None
        for attempt in range(LOCK_RETRY_ATTEMPTS):
            try:
                return func(*args, **kwargs)
            except storage.LockAcquisitionError as e:
                last_error = e
                if attempt < LOCK_RETRY_ATTEMPTS - 1:
                    time.sleep(LOCK_RETRY_INTERVAL_SECONDS)
        raise last_error


class TodoHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.csrf_token = secrets.token_hex(32)
        self.write_lock = threading.Lock()


class TodoRequestHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # --- 共通ヘルパー ---------------------------------------------------

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandlerのシグネチャに合わせる
        pass

    def _valid_host(self):
        host = self.headers.get("Host", "")
        port = self.server.server_port
        return host in ("127.0.0.1:{}".format(port), "localhost:{}".format(port))

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status, message):
        self._send_json(status, {"error": message})

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise _BadRequest("エラー: リクエストボディがJSON形式ではありません")
        if not isinstance(data, dict):
            raise _BadRequest("エラー: リクエストボディがJSON形式ではありません")
        return data

    def _check_csrf(self):
        token = self.headers.get("X-Todo-Token")
        if not token or not secrets.compare_digest(token, self.server.csrf_token):
            self._send_error_json(403, "エラー: CSRFトークンが不正です")
            return False
        return True

    def _handle_storage_error(self, exc):
        if isinstance(exc, storage.ItemNotFoundError):
            self._send_error_json(404, str(exc))
        elif isinstance(exc, storage.LockAcquisitionError):
            self._send_error_json(503, str(exc))
        elif isinstance(exc, storage.CorruptDataError):
            self._send_error_json(500, str(exc))
        elif isinstance(exc, storage.StorageError):
            self._send_error_json(400, str(exc))
        else:
            raise exc

    # --- ルーティング -----------------------------------------------------

    def do_GET(self):
        if not self._valid_host():
            self._send_error_json(403, "エラー: 不正なリクエストです")
            return

        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._serve_static_file("/index.html")
        elif path in ("/app.js", "/style.css"):
            self._serve_static_file(path)
        elif path == "/api/todos":
            self._handle_list(parsed.query)
        else:
            self._send_error_json(404, "エラー: 見つかりません")

    def do_POST(self):
        if not self._valid_host():
            self._send_error_json(403, "エラー: 不正なリクエストです")
            return
        if self.path != "/api/todos":
            self._send_error_json(404, "エラー: 見つかりません")
            return
        if not self._check_csrf():
            return

        try:
            body = self._read_json_body()
        except _BadRequest as e:
            self._send_error_json(400, str(e))
            return

        try:
            item = _run_with_write_lock(
                self.server,
                storage.add,
                body.get("text"),
                due=body.get("due"),
                priority=body.get("priority"),
                tags=body.get("tags"),
            )
        except storage.StorageError as e:
            self._handle_storage_error(e)
            return

        self._send_json(201, _item_to_json(item))

    def do_PATCH(self):
        if not self._valid_host():
            self._send_error_json(403, "エラー: 不正なリクエストです")
            return

        match = _TODO_ID_RE.match(urllib.parse.urlsplit(self.path).path)
        if not match:
            self._send_error_json(404, "エラー: 見つかりません")
            return
        if not self._check_csrf():
            return

        item_id = int(match.group(1))

        try:
            body = self._read_json_body()
        except _BadRequest as e:
            self._send_error_json(400, str(e))
            return

        edit_kwargs = {}
        if "text" in body:
            edit_kwargs["text"] = body["text"]
        if "due" in body:
            if body["due"] is None:
                edit_kwargs["clear_due"] = True
            else:
                edit_kwargs["due"] = body["due"]
        if "priority" in body:
            if body["priority"] is None:
                edit_kwargs["clear_priority"] = True
            else:
                edit_kwargs["priority"] = body["priority"]
        if "tags" in body:
            if body["tags"] in (None, []):
                edit_kwargs["clear_tags"] = True
            else:
                edit_kwargs["tags"] = body["tags"]

        has_done = "done" in body

        if not edit_kwargs and not has_done:
            self._send_error_json(400, "エラー: 変更する項目を指定してください")
            return

        try:
            item = None
            if edit_kwargs:
                item = _run_with_write_lock(self.server, storage.edit, item_id, **edit_kwargs)
            if has_done:
                target_func = storage.done if body["done"] else storage.undone
                item = _run_with_write_lock(self.server, target_func, item_id)
        except storage.StorageError as e:
            self._handle_storage_error(e)
            return

        self._send_json(200, _item_to_json(item))

    def do_DELETE(self):
        if not self._valid_host():
            self._send_error_json(403, "エラー: 不正なリクエストです")
            return

        match = _TODO_ID_RE.match(urllib.parse.urlsplit(self.path).path)
        if not match:
            self._send_error_json(404, "エラー: 見つかりません")
            return
        if not self._check_csrf():
            return

        item_id = int(match.group(1))

        try:
            item = _run_with_write_lock(self.server, storage.remove, item_id)
        except storage.StorageError as e:
            self._handle_storage_error(e)
            return

        self._send_json(200, _item_to_json(item))

    # --- 一覧取得・静的ファイル配信 ---------------------------------------

    def _handle_list(self, query_string):
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)

        def _single(name):
            values = params.get(name)
            return values[-1] if values else None

        tag = params.get("tag")

        try:
            items = storage.list_items(
                sort=_single("sort"),
                search=_single("search"),
                status=_single("status"),
                tag=tag,
                due_before=_single("due_before"),
                due_after=_single("due_after"),
            )
        except storage.StorageError as e:
            self._handle_storage_error(e)
            return

        self._send_json(200, [_item_to_json(item) for item in items])

    def _serve_static_file(self, rel_path):
        file_path = STATIC_DIR + rel_path
        try:
            with open(file_path, "rb") as f:
                content = f.read()
        except FileNotFoundError:
            self._send_error_json(404, "エラー: 見つかりません")
            return

        if rel_path == "/index.html":
            content = content.replace(b"__CSRF_TOKEN__", self.server.csrf_token.encode("ascii"))

        ext = "." + rel_path.rsplit(".", 1)[-1] if "." in rel_path else ""
        content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


class _BadRequest(Exception):
    pass


def create_server(host="127.0.0.1", port=8765):
    """ThreadingHTTPServerインスタンスを生成してbindする(serve_forever()は呼ばない)。

    port=0 の場合はOSが空きポートを割り当てる。実際に割り当てられたポートは
    戻り値の server_port から取得できる。
    """
    return TodoHTTPServer((host, port), TodoRequestHandler)


def run_server(host="127.0.0.1", port=8765):
    """create_server()でサーバを生成し、実際のポート番号を標準出力へ出力してから
    serve_forever()でフォアグラウンド起動し、ブロックする。
    """
    httpd = create_server(host, port)
    print("起動しました: http://{}:{}".format(host, httpd.server_port))
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
