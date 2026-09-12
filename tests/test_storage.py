"""todo/storage.py のテスト。DESIGN.md 4章(ストレージ形式と保存場所)に対応する。

一時ディレクトリのみを storage_dir として使用し、実際の ~/.todo には触れない。
"""

import json
import os
import shutil
import tempfile
import time
import unittest

from todo import storage


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="todo-test-")
        self._env_backup = os.environ.get("TODO_HOME")
        if "TODO_HOME" in os.environ:
            del os.environ["TODO_HOME"]

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        if self._env_backup is not None:
            os.environ["TODO_HOME"] = self._env_backup
        elif "TODO_HOME" in os.environ:
            del os.environ["TODO_HOME"]

    def data_path(self):
        return os.path.join(self.tmpdir, "todo.json")

    def tmp_path(self):
        return os.path.join(self.tmpdir, "todo.json.tmp")

    def lock_path(self):
        return os.path.join(self.tmpdir, "todo.lock")


class GetStorageDirTest(StorageTestCase):
    def test_uses_todo_home_env_var_when_set(self):
        os.environ["TODO_HOME"] = self.tmpdir
        self.assertEqual(storage.get_storage_dir(), os.path.abspath(self.tmpdir))

    def test_falls_back_to_home_todo_when_env_not_set(self):
        if "TODO_HOME" in os.environ:
            del os.environ["TODO_HOME"]
        self.assertEqual(storage.get_storage_dir(), os.path.expanduser("~/.todo"))


class LoadSaveTest(StorageTestCase):
    def test_load_returns_default_when_file_missing(self):
        data = storage.load(self.tmpdir)
        self.assertEqual(data, {"next_id": 1, "items": []})

    def test_load_does_not_create_file(self):
        storage.load(self.tmpdir)
        self.assertFalse(os.path.exists(self.data_path()))

    def test_save_then_load_roundtrip(self):
        data = {"next_id": 2, "items": [{"id": 1, "text": "a", "done": False,
                                          "created_at": "2026-01-01T00:00:00+09:00",
                                          "completed_at": None}]}
        storage.save(data, self.tmpdir)
        loaded = storage.load(self.tmpdir)
        self.assertEqual(loaded, data)

    def test_save_is_atomic_no_tmp_file_left(self):
        data = {"next_id": 1, "items": []}
        storage.save(data, self.tmpdir)
        self.assertTrue(os.path.exists(self.data_path()))
        self.assertFalse(os.path.exists(self.tmp_path()))

    def test_load_raises_corrupt_data_error_on_invalid_json(self):
        os.makedirs(self.tmpdir, exist_ok=True)
        with open(self.data_path(), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        with self.assertRaises(storage.CorruptDataError) as ctx:
            storage.load(self.tmpdir)
        self.assertIn(self.data_path(), str(ctx.exception))
        self.assertIn("データファイルが壊れています", str(ctx.exception))

    def test_load_does_not_overwrite_corrupt_file(self):
        broken_content = "{not valid json"
        with open(self.data_path(), "w", encoding="utf-8") as f:
            f.write(broken_content)
        with self.assertRaises(storage.CorruptDataError):
            storage.load(self.tmpdir)
        with open(self.data_path(), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), broken_content)

    def test_load_raises_corrupt_data_error_on_unexpected_structure(self):
        with open(self.data_path(), "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        with self.assertRaises(storage.CorruptDataError):
            storage.load(self.tmpdir)


class AddTest(StorageTestCase):
    def test_add_creates_item_with_id_1(self):
        item = storage.add("牛乳を買う", self.tmpdir)
        self.assertEqual(item["id"], 1)
        self.assertEqual(item["text"], "牛乳を買う")
        self.assertFalse(item["done"])
        self.assertIsNone(item["completed_at"])
        self.assertIsNotNone(item.get("created_at"))

    def test_add_increments_id(self):
        storage.add("first", self.tmpdir)
        second = storage.add("second", self.tmpdir)
        self.assertEqual(second["id"], 2)

    def test_add_empty_text_raises_invalid_text_error(self):
        with self.assertRaises(storage.InvalidTextError) as ctx:
            storage.add("", self.tmpdir)
        self.assertEqual(str(ctx.exception), "エラー: TODOの内容を入力してください")

    def test_add_whitespace_only_text_raises_invalid_text_error(self):
        with self.assertRaises(storage.InvalidTextError):
            storage.add("   ", self.tmpdir)
        with self.assertRaises(storage.InvalidTextError):
            storage.add("\t\n", self.tmpdir)

    def test_add_next_id_reconciles_with_max_existing_id(self):
        # next_id と実データの不整合を手動で作り、ID衝突が起きないことを確認する。
        data = {
            "next_id": 1,
            "items": [
                {"id": 1, "text": "a", "done": False,
                 "created_at": "2026-01-01T00:00:00+09:00", "completed_at": None},
                {"id": 5, "text": "b", "done": False,
                 "created_at": "2026-01-01T00:00:00+09:00", "completed_at": None},
            ],
        }
        storage.save(data, self.tmpdir)
        item = storage.add("c", self.tmpdir)
        self.assertEqual(item["id"], 6)


class ListItemsTest(StorageTestCase):
    def test_list_items_sorted_by_id_ascending(self):
        storage.add("a", self.tmpdir)
        storage.add("b", self.tmpdir)
        storage.add("c", self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir)
        self.assertEqual([i["id"] for i in items], [1, 2, 3])

    def test_list_items_include_done_false_filters_done(self):
        storage.add("a", self.tmpdir)
        storage.add("b", self.tmpdir)
        storage.complete(1, self.tmpdir)
        items = storage.list_items(include_done=False, storage_dir=self.tmpdir)
        self.assertEqual([i["id"] for i in items], [2])

    def test_list_items_include_done_true_shows_all(self):
        storage.add("a", self.tmpdir)
        storage.add("b", self.tmpdir)
        storage.complete(1, self.tmpdir)
        items = storage.list_items(include_done=True, storage_dir=self.tmpdir)
        self.assertEqual([i["id"] for i in items], [1, 2])


class CompleteRemoveTest(StorageTestCase):
    def test_complete_marks_done_and_sets_completed_at(self):
        storage.add("a", self.tmpdir)
        item = storage.complete(1, self.tmpdir)
        self.assertTrue(item["done"])
        self.assertIsNotNone(item["completed_at"])

    def test_complete_missing_id_raises_item_not_found_error(self):
        with self.assertRaises(storage.ItemNotFoundError) as ctx:
            storage.complete(99, self.tmpdir)
        self.assertEqual(str(ctx.exception), "エラー: ID 99 は存在しません")

    def test_remove_deletes_item(self):
        storage.add("a", self.tmpdir)
        storage.add("b", self.tmpdir)
        removed = storage.remove(1, self.tmpdir)
        self.assertEqual(removed["id"], 1)
        remaining = storage.list_items(storage_dir=self.tmpdir)
        self.assertEqual([i["id"] for i in remaining], [2])

    def test_remove_missing_id_raises_item_not_found_error(self):
        with self.assertRaises(storage.ItemNotFoundError) as ctx:
            storage.remove(42, self.tmpdir)
        self.assertEqual(str(ctx.exception), "エラー: ID 42 は存在しません")

    def test_remove_does_not_reuse_ids(self):
        storage.add("a", self.tmpdir)
        storage.remove(1, self.tmpdir)
        item = storage.add("b", self.tmpdir)
        self.assertEqual(item["id"], 2)


class LockTest(StorageTestCase):
    def test_double_lock_raises_lock_acquisition_error(self):
        os.makedirs(self.tmpdir, exist_ok=True)
        # 現在生存しているプロセス(自分自身)が保持している体で、直近取得の
        # ロックファイルを直接書き込み、二重ロックをシミュレートする。
        with open(self.lock_path(), "w", encoding="utf-8") as f:
            f.write("{}\n{}\n".format(os.getpid(), time.time()))

        with self.assertRaises(storage.LockAcquisitionError) as ctx:
            storage.add("a", self.tmpdir)
        self.assertEqual(
            str(ctx.exception),
            "エラー: 他のtodoプロセスが実行中です。しばらくして再試行してください",
        )

    def test_stale_lock_with_dead_pid_is_discarded(self):
        os.makedirs(self.tmpdir, exist_ok=True)
        dead_pid = 999999  # 存在しないであろうPID
        with open(self.lock_path(), "w", encoding="utf-8") as f:
            f.write("{}\n{}\n".format(dead_pid, time.time()))

        item = storage.add("a", self.tmpdir)
        self.assertEqual(item["id"], 1)
        self.assertFalse(os.path.exists(self.lock_path()))

    def test_stale_lock_with_old_timestamp_is_discarded(self):
        os.makedirs(self.tmpdir, exist_ok=True)
        old_time = time.time() - (storage.STALE_LOCK_SECONDS + 5)
        with open(self.lock_path(), "w", encoding="utf-8") as f:
            f.write("{}\n{}\n".format(os.getpid(), old_time))

        item = storage.add("a", self.tmpdir)
        self.assertEqual(item["id"], 1)
        self.assertFalse(os.path.exists(self.lock_path()))

    def test_lock_released_after_successful_add(self):
        storage.add("a", self.tmpdir)
        self.assertFalse(os.path.exists(self.lock_path()))


if __name__ == "__main__":
    unittest.main()
