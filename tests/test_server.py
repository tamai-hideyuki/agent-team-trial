"""todo/server.py のテスト。DESIGN.md 8章(ブラウザ画面)・9章に対応する。

create_server(port=0) を使い、実プロセスやポート8765固定には依存しない。
TODO_HOME を一時ディレクトリに設定して実データに触れないようにする。
"""

import http.client
import json
import os
import re
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from todo import server, storage


CSRF_META_RE = re.compile(r'<meta name="todo-csrf-token" content="([^"]*)">')


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="todo-server-test-")
        self._env_backup = os.environ.get("TODO_HOME")
        os.environ["TODO_HOME"] = self.tmpdir

        self.httpd = server.create_server(host="127.0.0.1", port=0)
        self.port = self.httpd.server_port
        self.token = self.httpd.csrf_token
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

        if self._env_backup is not None:
            os.environ["TODO_HOME"] = self._env_backup
        elif "TODO_HOME" in os.environ:
            del os.environ["TODO_HOME"]
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def connect(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def request(self, method, path, body=None, headers=None, host=None):
        conn = self.connect()
        try:
            hdrs = dict(headers or {})
            if host is not None:
                hdrs["Host"] = host
            data = None
            if body is not None:
                data = json.dumps(body).encode("utf-8")
                hdrs.setdefault("Content-Type", "application/json")
            conn.request(method, path, body=data, headers=hdrs)
            resp = conn.getresponse()
            raw = resp.read()
            return resp, raw
        finally:
            conn.close()

    def request_with_token(self, method, path, body=None, headers=None):
        hdrs = dict(headers or {})
        hdrs["X-Todo-Token"] = self.token
        return self.request(method, path, body=body, headers=hdrs)

    def json_body(self, raw):
        return json.loads(raw.decode("utf-8"))


class IndexAndCsrfTest(ServerTestCase):
    def test_index_serves_html_with_real_token_substituted(self):
        resp, raw = self.request("GET", "/")
        self.assertEqual(resp.status, 200)
        html = raw.decode("utf-8")
        match = CSRF_META_RE.search(html)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), self.token)
        self.assertNotIn("__CSRF_TOKEN__", html)

    def test_static_files_served(self):
        resp, _raw = self.request("GET", "/app.js")
        self.assertEqual(resp.status, 200)
        resp, _raw = self.request("GET", "/style.css")
        self.assertEqual(resp.status, 200)


class HostHeaderTest(ServerTestCase):
    def test_valid_host_127_accepted(self):
        resp, _raw = self.request(
            "GET", "/api/todos", host="127.0.0.1:{}".format(self.port)
        )
        self.assertEqual(resp.status, 200)

    def test_valid_host_localhost_accepted(self):
        resp, _raw = self.request(
            "GET", "/api/todos", host="localhost:{}".format(self.port)
        )
        self.assertEqual(resp.status, 200)

    def test_invalid_host_rejected_with_403(self):
        resp, raw = self.request("GET", "/api/todos", host="evil.example.com")
        self.assertEqual(resp.status, 403)
        body = self.json_body(raw)
        self.assertIn("error", body)

    def test_no_cors_headers_present(self):
        resp, _raw = self.request("GET", "/api/todos")
        self.assertIsNone(resp.getheader("Access-Control-Allow-Origin"))


class CsrfTokenTest(ServerTestCase):
    def test_post_without_token_rejected(self):
        resp, raw = self.request("POST", "/api/todos", body={"text": "牛乳"})
        self.assertEqual(resp.status, 403)
        body = self.json_body(raw)
        self.assertIn("error", body)

    def test_post_with_wrong_token_rejected(self):
        resp, raw = self.request(
            "POST",
            "/api/todos",
            body={"text": "牛乳"},
            headers={"X-Todo-Token": "wrong-token"},
        )
        self.assertEqual(resp.status, 403)

    def test_post_with_correct_token_accepted(self):
        resp, raw = self.request_with_token(
            "POST", "/api/todos", body={"text": "牛乳を買う"}
        )
        self.assertEqual(resp.status, 201)
        item = self.json_body(raw)
        self.assertEqual(item["text"], "牛乳を買う")

    def test_get_does_not_require_token(self):
        resp, _raw = self.request("GET", "/api/todos")
        self.assertEqual(resp.status, 200)


class TodoApiTest(ServerTestCase):
    def add(self, **kwargs):
        resp, raw = self.request_with_token("POST", "/api/todos", body=kwargs)
        self.assertEqual(resp.status, 201, raw)
        return self.json_body(raw)

    def test_add_and_list(self):
        self.add(text="牛乳を買う")
        self.add(text="資料を作る", due="2026-09-20", priority="high", tags=["仕事", "至急"])

        resp, raw = self.request("GET", "/api/todos")
        self.assertEqual(resp.status, 200)
        items = self.json_body(raw)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1]["due"], "2026-09-20")
        self.assertEqual(items[1]["priority"], "high")
        self.assertEqual(items[1]["tags"], ["仕事", "至急"])

    def test_add_empty_text_returns_400(self):
        resp, raw = self.request_with_token("POST", "/api/todos", body={"text": "  "})
        self.assertEqual(resp.status, 400)
        body = self.json_body(raw)
        self.assertEqual(body["error"], "エラー: TODOの内容を入力してください")

    def test_add_invalid_due_returns_400(self):
        resp, raw = self.request_with_token(
            "POST", "/api/todos", body={"text": "x", "due": "2026-13-01"}
        )
        self.assertEqual(resp.status, 400)
        body = self.json_body(raw)
        self.assertEqual(
            body["error"],
            "エラー: 期限の形式が不正です"
            "(YYYY-MM-DD または YYYY-MM-DD HH:MM形式で指定してください)",
        )

    def test_add_invalid_priority_returns_400(self):
        resp, raw = self.request_with_token(
            "POST", "/api/todos", body={"text": "x", "priority": "urgent"}
        )
        self.assertEqual(resp.status, 400)
        body = self.json_body(raw)
        self.assertEqual(
            body["error"], "エラー: 優先度はhigh/medium/lowのいずれかで指定してください"
        )

    def test_list_filters_by_status_search_tag(self):
        self.add(text="牛乳を買う")
        self.add(text="資料を作る", tags=["仕事"])

        resp, raw = self.request("GET", "/api/todos?search=%E8%B3%87%E6%96%99")
        items = self.json_body(raw)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "資料を作る")

        resp, raw = self.request("GET", "/api/todos?tag=%E4%BB%95%E4%BA%8B")
        items = self.json_body(raw)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["tags"], ["仕事"])

    def test_patch_edit_fields(self):
        item = self.add(text="資料を作る", priority="high")
        resp, raw = self.request_with_token(
            "PATCH",
            "/api/todos/{}".format(item["id"]),
            body={"priority": "medium"},
        )
        self.assertEqual(resp.status, 200)
        updated = self.json_body(raw)
        self.assertEqual(updated["priority"], "medium")

    def test_patch_clear_fields_with_null_and_empty_array(self):
        item = self.add(
            text="資料を作る", due="2026-09-20", priority="high", tags=["仕事"]
        )
        resp, raw = self.request_with_token(
            "PATCH",
            "/api/todos/{}".format(item["id"]),
            body={"due": None, "priority": None, "tags": []},
        )
        self.assertEqual(resp.status, 200)
        updated = self.json_body(raw)
        self.assertIsNone(updated["due"])
        self.assertIsNone(updated["priority"])
        self.assertEqual(updated["tags"], [])

    def test_patch_done_and_undone_via_done_field(self):
        item = self.add(text="牛乳を買う")
        resp, raw = self.request_with_token(
            "PATCH", "/api/todos/{}".format(item["id"]), body={"done": True}
        )
        self.assertEqual(resp.status, 200)
        updated = self.json_body(raw)
        self.assertTrue(updated["done"])
        self.assertIsNotNone(updated["completed_at"])

        resp, raw = self.request_with_token(
            "PATCH", "/api/todos/{}".format(item["id"]), body={"done": False}
        )
        self.assertEqual(resp.status, 200)
        updated = self.json_body(raw)
        self.assertFalse(updated["done"])
        self.assertIsNone(updated["completed_at"])

    def test_patch_no_fields_returns_400(self):
        item = self.add(text="牛乳を買う")
        resp, raw = self.request_with_token(
            "PATCH", "/api/todos/{}".format(item["id"]), body={}
        )
        self.assertEqual(resp.status, 400)
        body = self.json_body(raw)
        self.assertEqual(body["error"], "エラー: 変更する項目を指定してください")

    def test_patch_nonexistent_id_returns_404(self):
        resp, raw = self.request_with_token(
            "PATCH", "/api/todos/999", body={"text": "x"}
        )
        self.assertEqual(resp.status, 404)
        body = self.json_body(raw)
        self.assertEqual(body["error"], "エラー: ID 999 は存在しません")

    def test_delete_removes_item(self):
        item = self.add(text="牛乳を買う")
        resp, raw = self.request_with_token(
            "DELETE", "/api/todos/{}".format(item["id"])
        )
        self.assertEqual(resp.status, 200)

        resp, raw = self.request("GET", "/api/todos")
        items = self.json_body(raw)
        self.assertEqual(items, [])

    def test_delete_nonexistent_id_returns_404(self):
        resp, raw = self.request_with_token("DELETE", "/api/todos/999")
        self.assertEqual(resp.status, 404)
        body = self.json_body(raw)
        self.assertEqual(body["error"], "エラー: ID 999 は存在しません")

    def test_list_with_corrupt_data_file_returns_500_json(self):
        data_path = os.path.join(self.tmpdir, "todo.json")
        with open(data_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")

        resp, raw = self.request("GET", "/api/todos")
        self.assertEqual(resp.status, 500)
        body = self.json_body(raw)
        self.assertIn("エラー: データファイルが壊れています", body["error"])


class OverdueGroupingTest(ServerTestCase):
    """DESIGN.md 5.13節(期限切れ優先グルーピング)・8.2節(sort=overdue)対応。"""

    def setUp(self):
        super().setUp()
        self._today_patcher = mock.patch.object(
            storage, "today", lambda: "2026-09-12"
        )
        self._now_patcher = mock.patch.object(
            storage, "now", lambda: "2026-09-12T10:00:00+09:00"
        )
        self._today_patcher.start()
        self._now_patcher.start()

    def tearDown(self):
        self._now_patcher.stop()
        self._today_patcher.stop()
        super().tearDown()

    def add(self, **kwargs):
        resp, raw = self.request_with_token("POST", "/api/todos", body=kwargs)
        self.assertEqual(resp.status, 201, raw)
        return self.json_body(raw)

    def build_items(self):
        future = self.add(text="future", due="2026-12-01")
        overdue_date = self.add(text="overdue-date", due="2026-09-01")
        overdue_time_early = self.add(
            text="overdue-time-early", due="2026-09-12 09:00"
        )
        overdue_time_late = self.add(
            text="overdue-time-late", due="2026-09-12 11:00"
        )
        done_overdue = self.add(text="done-overdue", due="2026-08-01")
        resp, raw = self.request_with_token(
            "PATCH",
            "/api/todos/{}".format(done_overdue["id"]),
            body={"done": True},
        )
        self.assertEqual(resp.status, 200)
        return future, overdue_date, overdue_time_early, overdue_time_late, done_overdue

    def test_default_sort_groups_undone_overdue_first(self):
        future, overdue_date, overdue_time_early, overdue_time_late, done_overdue = (
            self.build_items()
        )

        resp, raw = self.request("GET", "/api/todos")
        self.assertEqual(resp.status, 200)
        items = self.json_body(raw)
        ids = [item["id"] for item in items]
        self.assertEqual(
            ids,
            [
                overdue_date["id"],
                overdue_time_early["id"],
                future["id"],
                overdue_time_late["id"],
                done_overdue["id"],
            ],
        )

    def test_sort_overdue_matches_default(self):
        self.build_items()

        resp_default, raw_default = self.request("GET", "/api/todos")
        resp_overdue, raw_overdue = self.request("GET", "/api/todos?sort=overdue")
        self.assertEqual(resp_default.status, 200)
        self.assertEqual(resp_overdue.status, 200)
        self.assertEqual(
            [i["id"] for i in self.json_body(raw_default)],
            [i["id"] for i in self.json_body(raw_overdue)],
        )

    def test_sort_due_bypasses_overdue_grouping(self):
        future, overdue_date, overdue_time_early, overdue_time_late, done_overdue = (
            self.build_items()
        )

        resp, raw = self.request("GET", "/api/todos?sort=due")
        self.assertEqual(resp.status, 200)
        ids = [item["id"] for item in self.json_body(raw)]
        self.assertEqual(
            ids,
            [
                done_overdue["id"],
                overdue_date["id"],
                overdue_time_early["id"],
                overdue_time_late["id"],
                future["id"],
            ],
        )

    def test_overdue_field_reflects_is_overdue_regardless_of_done(self):
        future, overdue_date, overdue_time_early, overdue_time_late, done_overdue = (
            self.build_items()
        )

        resp, raw = self.request("GET", "/api/todos")
        by_id = {item["id"]: item for item in self.json_body(raw)}
        self.assertFalse(by_id[future["id"]]["overdue"])
        self.assertTrue(by_id[overdue_date["id"]]["overdue"])
        self.assertTrue(by_id[overdue_time_early["id"]]["overdue"])
        self.assertFalse(by_id[overdue_time_late["id"]]["overdue"])
        self.assertTrue(by_id[done_overdue["id"]]["overdue"])


class ConcurrencyTest(ServerTestCase):
    def test_concurrent_add_requests_do_not_corrupt_data(self):
        errors = []

        def add_one(n):
            try:
                resp, raw = self.request_with_token(
                    "POST", "/api/todos", body={"text": "todo-{}".format(n)}
                )
                if resp.status != 201:
                    errors.append((n, resp.status, raw))
            except Exception as exc:  # noqa: BLE001
                errors.append((n, "exception", str(exc)))

        threads = [threading.Thread(target=add_one, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])

        resp, raw = self.request("GET", "/api/todos")
        items = self.json_body(raw)
        self.assertEqual(len(items), 20)
        ids = [item["id"] for item in items]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
