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

    def test_falls_back_to_repo_todo_data_dir_when_env_not_set(self):
        if "TODO_HOME" in os.environ:
            del os.environ["TODO_HOME"]
        repo_root = os.path.dirname(
            os.path.dirname(os.path.abspath(storage.__file__))
        )
        expected = os.path.join(repo_root, ".todo-data")
        self.assertEqual(storage.get_storage_dir(), expected)

    def test_does_not_use_home_todo_when_env_not_set(self):
        if "TODO_HOME" in os.environ:
            del os.environ["TODO_HOME"]
        self.assertNotEqual(storage.get_storage_dir(), os.path.expanduser("~/.todo"))


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
        storage.done(1, self.tmpdir)
        items = storage.list_items(include_done=False, storage_dir=self.tmpdir)
        self.assertEqual([i["id"] for i in items], [2])

    def test_list_items_include_done_true_shows_all(self):
        storage.add("a", self.tmpdir)
        storage.add("b", self.tmpdir)
        storage.done(1, self.tmpdir)
        items = storage.list_items(include_done=True, storage_dir=self.tmpdir)
        self.assertEqual([i["id"] for i in items], [1, 2])


class CompleteRemoveTest(StorageTestCase):
    def test_complete_marks_done_and_sets_completed_at(self):
        storage.add("a", self.tmpdir)
        item = storage.done(1, self.tmpdir)
        self.assertTrue(item["done"])
        self.assertIsNotNone(item["completed_at"])

    def test_complete_missing_id_raises_item_not_found_error(self):
        with self.assertRaises(storage.ItemNotFoundError) as ctx:
            storage.done(99, self.tmpdir)
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


class LockStaleEnvVarTest(StorageTestCase):
    def test_stale_lock_uses_env_var_override(self):
        os.makedirs(self.tmpdir, exist_ok=True)
        os.environ["TODO_LOCK_STALE_SECONDS"] = "1"
        try:
            old_time = time.time() - 2
            with open(self.lock_path(), "w", encoding="utf-8") as f:
                f.write("{}\n{}\n".format(os.getpid(), old_time))
            item = storage.add("a", self.tmpdir)
            self.assertEqual(item["id"], 1)
            self.assertFalse(os.path.exists(self.lock_path()))
        finally:
            del os.environ["TODO_LOCK_STALE_SECONDS"]

    def test_lock_not_stale_within_env_var_threshold(self):
        os.makedirs(self.tmpdir, exist_ok=True)
        os.environ["TODO_LOCK_STALE_SECONDS"] = "60"
        try:
            with open(self.lock_path(), "w", encoding="utf-8") as f:
                f.write("{}\n{}\n".format(os.getpid(), time.time()))
            with self.assertRaises(storage.LockAcquisitionError):
                storage.add("a", self.tmpdir)
        finally:
            del os.environ["TODO_LOCK_STALE_SECONDS"]


class NowTodayTest(StorageTestCase):
    def test_add_uses_storage_now_for_created_at(self):
        original_now = storage.now
        storage.now = lambda: "2026-01-01T09:00:00+09:00"
        try:
            item = storage.add("a", storage_dir=self.tmpdir)
            self.assertEqual(item["created_at"], "2026-01-01T09:00:00+09:00")
        finally:
            storage.now = original_now

    def test_done_uses_storage_now_for_completed_at(self):
        storage.add("a", storage_dir=self.tmpdir)
        original_now = storage.now
        storage.now = lambda: "2026-02-02T10:00:00+09:00"
        try:
            item = storage.done(1, storage_dir=self.tmpdir)
            self.assertEqual(item["completed_at"], "2026-02-02T10:00:00+09:00")
        finally:
            storage.now = original_now

    def test_today_can_be_fixed_for_overdue_judgement(self):
        original_today = storage.today
        storage.today = lambda: "2026-09-15"
        try:
            self.assertEqual(storage.today(), "2026-09-15")
        finally:
            storage.today = original_today


class AddNewFieldsTest(StorageTestCase):
    def test_add_with_due_priority_tags(self):
        item = storage.add(
            "資料を作る", due="2026-09-20", priority="high",
            tags=["仕事", "至急"], storage_dir=self.tmpdir,
        )
        self.assertEqual(item["due"], "2026-09-20")
        self.assertEqual(item["priority"], "high")
        self.assertEqual(item["tags"], ["仕事", "至急"])

    def test_add_without_new_fields_defaults(self):
        item = storage.add("牛乳を買う", storage_dir=self.tmpdir)
        self.assertIsNone(item["due"])
        self.assertIsNone(item["priority"])
        self.assertEqual(item["tags"], [])

    def test_add_invalid_due_format_raises(self):
        with self.assertRaises(storage.InvalidDueError) as ctx:
            storage.add("a", due="2026-13-01", storage_dir=self.tmpdir)
        self.assertEqual(
            str(ctx.exception),
            "エラー: 期限の形式が不正です(YYYY-MM-DD または YYYY-MM-DD HH:MM形式で指定してください)",
        )

    def test_add_invalid_priority_raises(self):
        with self.assertRaises(storage.InvalidPriorityError) as ctx:
            storage.add("a", priority="urgent", storage_dir=self.tmpdir)
        self.assertEqual(
            str(ctx.exception),
            "エラー: 優先度はhigh/medium/lowのいずれかで指定してください",
        )

    def test_add_priority_case_insensitive(self):
        item = storage.add("a", priority="HIGH", storage_dir=self.tmpdir)
        self.assertEqual(item["priority"], "high")

    def test_add_tags_are_trimmed_and_deduplicated(self):
        item = storage.add(
            "a", tags=[" 仕事 ", "仕事", "", "  ", "至急"], storage_dir=self.tmpdir,
        )
        self.assertEqual(item["tags"], ["仕事", "至急"])


class RoundtripSafetyTest(StorageTestCase):
    def test_unknown_keys_are_preserved_on_edit(self):
        data = {
            "next_id": 2,
            "items": [
                {
                    "id": 1, "text": "a", "done": False,
                    "created_at": "2026-01-01T00:00:00+09:00", "completed_at": None,
                    "future_field": "keep-me",
                }
            ],
        }
        storage.save(data, self.tmpdir)
        storage.edit(1, text="b", storage_dir=self.tmpdir)
        loaded = storage.load(self.tmpdir)
        self.assertEqual(loaded["items"][0]["future_field"], "keep-me")
        self.assertEqual(loaded["items"][0]["text"], "b")

    def test_old_format_item_without_new_fields_untouched_by_plain_list(self):
        data = {
            "next_id": 2,
            "items": [
                {
                    "id": 1, "text": "a", "done": False,
                    "created_at": "2026-01-01T00:00:00+09:00", "completed_at": None,
                }
            ],
        }
        storage.save(data, self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir)
        self.assertNotIn("due", items[0])
        self.assertNotIn("priority", items[0])
        self.assertNotIn("tags", items[0])
        loaded = storage.load(self.tmpdir)
        self.assertEqual(loaded, data)


class EditTest(StorageTestCase):
    def test_edit_text_only(self):
        storage.add("a", storage_dir=self.tmpdir)
        item = storage.edit(1, text="b", storage_dir=self.tmpdir)
        self.assertEqual(item["text"], "b")

    def test_edit_option_only(self):
        storage.add("a", storage_dir=self.tmpdir)
        item = storage.edit(1, priority="low", storage_dir=self.tmpdir)
        self.assertEqual(item["text"], "a")
        self.assertEqual(item["priority"], "low")

    def test_edit_no_fields_raises(self):
        storage.add("a", storage_dir=self.tmpdir)
        with self.assertRaises(storage.NoFieldsSpecifiedError) as ctx:
            storage.edit(1, storage_dir=self.tmpdir)
        self.assertEqual(str(ctx.exception), "エラー: 変更する項目を指定してください")

    def test_edit_missing_id_raises(self):
        with self.assertRaises(storage.ItemNotFoundError) as ctx:
            storage.edit(99, text="内容", storage_dir=self.tmpdir)
        self.assertEqual(str(ctx.exception), "エラー: ID 99 は存在しません")

    def test_edit_due_and_clear_due_conflict(self):
        storage.add("a", storage_dir=self.tmpdir)
        with self.assertRaises(storage.ConflictingOptionsError) as ctx:
            storage.edit(1, due="2026-09-25", clear_due=True, storage_dir=self.tmpdir)
        self.assertEqual(
            str(ctx.exception), "エラー: --due と --clear-due は同時に指定できません"
        )

    def test_edit_priority_and_clear_priority_conflict(self):
        storage.add("a", storage_dir=self.tmpdir)
        with self.assertRaises(storage.ConflictingOptionsError) as ctx:
            storage.edit(1, priority="high", clear_priority=True, storage_dir=self.tmpdir)
        self.assertEqual(
            str(ctx.exception),
            "エラー: --priority と --clear-priority は同時に指定できません",
        )

    def test_edit_tag_and_clear_tags_conflict(self):
        storage.add("a", storage_dir=self.tmpdir)
        with self.assertRaises(storage.ConflictingOptionsError) as ctx:
            storage.edit(1, tags=["仕事"], clear_tags=True, storage_dir=self.tmpdir)
        self.assertEqual(
            str(ctx.exception), "エラー: --tag と --clear-tags は同時に指定できません"
        )

    def test_edit_tags_replace_entirely(self):
        storage.add("a", tags=["仕事", "至急"], storage_dir=self.tmpdir)
        item = storage.edit(1, tags=["仕事"], storage_dir=self.tmpdir)
        self.assertEqual(item["tags"], ["仕事"])

    def test_edit_clear_due_priority_tags(self):
        storage.add(
            "a", due="2026-09-20", priority="high", tags=["仕事"],
            storage_dir=self.tmpdir,
        )
        item = storage.edit(
            1, clear_due=True, clear_priority=True, clear_tags=True,
            storage_dir=self.tmpdir,
        )
        self.assertIsNone(item["due"])
        self.assertIsNone(item["priority"])
        self.assertEqual(item["tags"], [])

    def test_edit_empty_text_raises_invalid_text_error(self):
        storage.add("a", storage_dir=self.tmpdir)
        with self.assertRaises(storage.InvalidTextError):
            storage.edit(1, text="   ", storage_dir=self.tmpdir)

    def test_edit_invalid_due_raises(self):
        storage.add("a", storage_dir=self.tmpdir)
        with self.assertRaises(storage.InvalidDueError):
            storage.edit(1, due="2026-13-01", storage_dir=self.tmpdir)

    def test_edit_invalid_priority_raises(self):
        storage.add("a", storage_dir=self.tmpdir)
        with self.assertRaises(storage.InvalidPriorityError):
            storage.edit(1, priority="urgent", storage_dir=self.tmpdir)


class UndoneTest(StorageTestCase):
    def test_undone_resets_done_and_completed_at(self):
        storage.add("a", storage_dir=self.tmpdir)
        storage.done(1, storage_dir=self.tmpdir)
        item = storage.undone(1, storage_dir=self.tmpdir)
        self.assertFalse(item["done"])
        self.assertIsNone(item["completed_at"])

    def test_undone_missing_id_raises(self):
        with self.assertRaises(storage.ItemNotFoundError) as ctx:
            storage.undone(99, storage_dir=self.tmpdir)
        self.assertEqual(str(ctx.exception), "エラー: ID 99 は存在しません")


class SortSearchFilterTest(StorageTestCase):
    def test_sort_due_nulls_last_and_stable(self):
        storage.add("no-due-first", storage_dir=self.tmpdir)
        storage.add("late", due="2026-09-25", storage_dir=self.tmpdir)
        storage.add("early", due="2026-09-20", storage_dir=self.tmpdir)
        storage.add("no-due-second", storage_dir=self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir, sort="due")
        self.assertEqual(
            [i["text"] for i in items],
            ["early", "late", "no-due-first", "no-due-second"],
        )

    def test_sort_priority_high_medium_low_then_null(self):
        storage.add("low-item", priority="low", storage_dir=self.tmpdir)
        storage.add("no-priority", storage_dir=self.tmpdir)
        storage.add("high-item", priority="high", storage_dir=self.tmpdir)
        storage.add("medium-item", priority="medium", storage_dir=self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir, sort="priority")
        self.assertEqual(
            [i["text"] for i in items],
            ["high-item", "medium-item", "low-item", "no-priority"],
        )

    def test_search_matches_text_case_and_width_insensitively(self):
        storage.add("資料を作る ABC", storage_dir=self.tmpdir)
        storage.add("その他", storage_dir=self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir, search="ａｂｃ")
        self.assertEqual([i["text"] for i in items], ["資料を作る ABC"])

    def test_search_does_not_match_tags(self):
        storage.add("本文", tags=["仕事"], storage_dir=self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir, search="仕事")
        self.assertEqual(items, [])

    def test_tag_filter_or_condition(self):
        storage.add("a", tags=["仕事"], storage_dir=self.tmpdir)
        storage.add("b", tags=["個人"], storage_dir=self.tmpdir)
        storage.add("c", tags=["仕事", "至急"], storage_dir=self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir, tag=["仕事", "個人"])
        self.assertEqual([i["text"] for i in items], ["a", "b", "c"])

    def test_status_pending_and_done(self):
        storage.add("a", storage_dir=self.tmpdir)
        storage.add("b", storage_dir=self.tmpdir)
        storage.done(1, storage_dir=self.tmpdir)
        pending = storage.list_items(storage_dir=self.tmpdir, status="pending")
        done_items = storage.list_items(storage_dir=self.tmpdir, status="done")
        self.assertEqual([i["id"] for i in pending], [2])
        self.assertEqual([i["id"] for i in done_items], [1])

    def test_due_before_and_due_after_inclusive_boundaries(self):
        storage.add("a", due="2026-09-18", storage_dir=self.tmpdir)
        storage.add("b", due="2026-09-20", storage_dir=self.tmpdir)
        storage.add("c", due="2026-09-22", storage_dir=self.tmpdir)
        before = storage.list_items(storage_dir=self.tmpdir, due_before="2026-09-20")
        after = storage.list_items(storage_dir=self.tmpdir, due_after="2026-09-20")
        both = storage.list_items(
            storage_dir=self.tmpdir, due_before="2026-09-20", due_after="2026-09-20",
        )
        self.assertEqual([i["text"] for i in before], ["a", "b"])
        self.assertEqual([i["text"] for i in after], ["b", "c"])
        self.assertEqual([i["text"] for i in both], ["b"])


class DueWithTimeValidationTest(StorageTestCase):
    def test_add_accepts_date_only(self):
        item = storage.add("a", due="2026-09-20", storage_dir=self.tmpdir)
        self.assertEqual(item["due"], "2026-09-20")

    def test_add_accepts_date_and_time(self):
        item = storage.add("a", due="2026-09-20 18:00", storage_dir=self.tmpdir)
        self.assertEqual(item["due"], "2026-09-20 18:00")

    def test_add_rejects_hour_24(self):
        with self.assertRaises(storage.InvalidDueError):
            storage.add("a", due="2026-09-20 24:00", storage_dir=self.tmpdir)

    def test_add_rejects_seconds(self):
        with self.assertRaises(storage.InvalidDueError):
            storage.add("a", due="2026-09-20 18:00:00", storage_dir=self.tmpdir)

    def test_add_rejects_timezone_suffix(self):
        with self.assertRaises(storage.InvalidDueError):
            storage.add("a", due="2026-09-20 18:00+09:00", storage_dir=self.tmpdir)

    def test_add_rejects_single_digit_hour_minute(self):
        with self.assertRaises(storage.InvalidDueError):
            storage.add("a", due="2026-09-20 8:00", storage_dir=self.tmpdir)

    def test_add_rejects_invalid_minute(self):
        with self.assertRaises(storage.InvalidDueError):
            storage.add("a", due="2026-09-20 12:60", storage_dir=self.tmpdir)

    def test_error_message_mentions_both_formats(self):
        with self.assertRaises(storage.InvalidDueError) as ctx:
            storage.add("a", due="not-a-date", storage_dir=self.tmpdir)
        self.assertEqual(
            str(ctx.exception),
            "エラー: 期限の形式が不正です(YYYY-MM-DD または YYYY-MM-DD HH:MM形式で指定してください)",
        )


class IsOverdueTest(StorageTestCase):
    def setUp(self):
        super().setUp()
        self._orig_today = storage.today
        self._orig_now = storage.now

    def tearDown(self):
        storage.today = self._orig_today
        storage.now = self._orig_now
        super().tearDown()

    def test_date_only_not_overdue_on_due_day(self):
        storage.today = lambda: "2026-09-20"
        item = {"due": "2026-09-20"}
        self.assertFalse(storage.is_overdue(item))

    def test_date_only_overdue_on_day_after(self):
        storage.today = lambda: "2026-09-21"
        item = {"due": "2026-09-20"}
        self.assertTrue(storage.is_overdue(item))

    def test_date_only_with_no_due_is_not_overdue(self):
        item = {"due": None}
        self.assertFalse(storage.is_overdue(item))

    def test_item_without_due_key_is_not_overdue(self):
        self.assertFalse(storage.is_overdue({}))

    def test_datetime_not_overdue_before_due_minute(self):
        storage.now = lambda: "2026-09-20T17:59:00+09:00"
        item = {"due": "2026-09-20 18:00"}
        self.assertFalse(storage.is_overdue(item))

    def test_datetime_not_overdue_at_exact_due_minute(self):
        storage.now = lambda: "2026-09-20T18:00:00+09:00"
        item = {"due": "2026-09-20 18:00"}
        self.assertFalse(storage.is_overdue(item))

    def test_datetime_overdue_one_minute_after(self):
        storage.now = lambda: "2026-09-20T18:01:00+09:00"
        item = {"due": "2026-09-20 18:00"}
        self.assertTrue(storage.is_overdue(item))

    def test_datetime_overdue_same_day_later_hour(self):
        # 日付+時刻の場合は当日でも指定時刻を過ぎていれば期限切れになる
        storage.now = lambda: "2026-09-20T20:00:00+09:00"
        item = {"due": "2026-09-20 18:00"}
        self.assertTrue(storage.is_overdue(item))

    def test_is_overdue_ignores_done_status(self):
        # done かどうかの考慮は呼び出し側(グルーピング)の責務
        storage.today = lambda: "2026-09-21"
        item = {"due": "2026-09-20", "done": True}
        self.assertTrue(storage.is_overdue(item))


class DueBeforeAfterWithTimeTest(StorageTestCase):
    def test_due_before_matches_datetime_due_by_date_part_only(self):
        storage.add("a", due="2026-09-01 18:00", storage_dir=self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir, due_before="2026-09-01")
        self.assertEqual(len(items), 1)

    def test_due_after_matches_datetime_due_by_date_part_only(self):
        storage.add("a", due="2026-09-01 00:30", storage_dir=self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir, due_after="2026-09-01")
        self.assertEqual(len(items), 1)


class DefaultOverdueGroupingTest(StorageTestCase):
    def setUp(self):
        super().setUp()
        self._orig_today = storage.today
        self._orig_now = storage.now
        storage.today = lambda: "2026-09-12"
        storage.now = lambda: "2026-09-12T09:00:00+09:00"

    def tearDown(self):
        storage.today = self._orig_today
        storage.now = self._orig_now
        super().tearDown()

    def test_overdue_pending_items_come_first_sorted_by_due(self):
        storage.add("no-due", storage_dir=self.tmpdir)
        storage.add("late-overdue", due="2026-09-05", storage_dir=self.tmpdir)
        storage.add("future", due="2026-09-20", storage_dir=self.tmpdir)
        storage.add("early-overdue", due="2026-09-01", storage_dir=self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir)
        self.assertEqual(
            [i["text"] for i in items],
            ["early-overdue", "late-overdue", "no-due", "future"],
        )

    def test_done_items_are_excluded_from_overdue_group_even_if_shown(self):
        storage.add("a", due="2026-09-01", storage_dir=self.tmpdir)
        storage.add("b", storage_dir=self.tmpdir)
        storage.done(1, storage_dir=self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir, status="all")
        self.assertEqual([i["text"] for i in items], ["a", "b"])

    def test_explicit_sort_due_disables_grouping(self):
        storage.add("no-due", storage_dir=self.tmpdir)
        storage.add("overdue", due="2026-09-01", storage_dir=self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir, sort="due")
        self.assertEqual([i["text"] for i in items], ["overdue", "no-due"])

    def test_explicit_sort_priority_disables_grouping(self):
        storage.add("overdue-low", due="2026-09-01", priority="low", storage_dir=self.tmpdir)
        storage.add("no-due-high", priority="high", storage_dir=self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir, sort="priority")
        self.assertEqual([i["text"] for i in items], ["no-due-high", "overdue-low"])

    def test_sort_overdue_explicit_value_behaves_like_default(self):
        storage.add("no-due", storage_dir=self.tmpdir)
        storage.add("overdue", due="2026-09-01", storage_dir=self.tmpdir)
        default_items = storage.list_items(storage_dir=self.tmpdir)
        explicit_items = storage.list_items(storage_dir=self.tmpdir, sort="overdue")
        self.assertEqual(
            [i["text"] for i in default_items], [i["text"] for i in explicit_items]
        )

    def test_no_overdue_items_keeps_id_order_unchanged(self):
        storage.add("a", storage_dir=self.tmpdir)
        storage.add("b", due="2026-09-20", storage_dir=self.tmpdir)
        storage.add("c", storage_dir=self.tmpdir)
        items = storage.list_items(storage_dir=self.tmpdir)
        self.assertEqual([i["id"] for i in items], [1, 2, 3])


class TrashTest(StorageTestCase):
    def setUp(self):
        super().setUp()
        self._orig_now = storage.now
        storage.now = lambda: "2026-09-12T09:00:00+09:00"

    def tearDown(self):
        storage.now = self._orig_now
        super().tearDown()

    def test_remove_is_soft_delete_item_remains_in_json_with_deleted_at(self):
        storage.add("a", storage_dir=self.tmpdir)
        storage.remove(1, storage_dir=self.tmpdir)
        data = storage.load(self.tmpdir)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["id"], 1)
        self.assertEqual(data["items"][0]["deleted_at"], "2026-09-12T09:00:00+09:00")

    def test_restore_clears_deleted_at(self):
        storage.add("a", storage_dir=self.tmpdir)
        storage.remove(1, storage_dir=self.tmpdir)
        item = storage.restore(1, storage_dir=self.tmpdir)
        self.assertIsNone(item["deleted_at"])
        data = storage.load(self.tmpdir)
        self.assertIsNone(data["items"][0]["deleted_at"])

    def test_restore_missing_id_raises_item_not_found_error(self):
        with self.assertRaises(storage.ItemNotFoundError) as ctx:
            storage.restore(99, storage_dir=self.tmpdir)
        self.assertEqual(str(ctx.exception), "エラー: ID 99 は存在しません")

    def test_restore_id_not_in_trash_raises_trash_item_not_found_error(self):
        storage.add("a", storage_dir=self.tmpdir)
        with self.assertRaises(storage.TrashItemNotFoundError) as ctx:
            storage.restore(1, storage_dir=self.tmpdir)
        self.assertEqual(str(ctx.exception), "エラー: ID 1 はゴミ箱にありません")

    def test_purge_removes_item_physically_and_keeps_next_id(self):
        storage.add("a", storage_dir=self.tmpdir)
        storage.add("b", storage_dir=self.tmpdir)
        storage.remove(1, storage_dir=self.tmpdir)
        purged = storage.purge(1, storage_dir=self.tmpdir)
        self.assertEqual(purged["id"], 1)
        data = storage.load(self.tmpdir)
        self.assertEqual([i["id"] for i in data["items"]], [2])
        self.assertEqual(data["next_id"], 3)
        new_item = storage.add("c", storage_dir=self.tmpdir)
        self.assertEqual(new_item["id"], 3)

    def test_purge_missing_id_raises_item_not_found_error(self):
        with self.assertRaises(storage.ItemNotFoundError) as ctx:
            storage.purge(99, storage_dir=self.tmpdir)
        self.assertEqual(str(ctx.exception), "エラー: ID 99 は存在しません")

    def test_purge_id_not_in_trash_raises_trash_item_not_found_error(self):
        storage.add("a", storage_dir=self.tmpdir)
        with self.assertRaises(storage.TrashItemNotFoundError) as ctx:
            storage.purge(1, storage_dir=self.tmpdir)
        self.assertEqual(str(ctx.exception), "エラー: ID 1 はゴミ箱にありません")

    def test_purge_all_removes_all_trashed_items_and_returns_count(self):
        storage.add("a", storage_dir=self.tmpdir)
        storage.add("b", storage_dir=self.tmpdir)
        storage.add("c", storage_dir=self.tmpdir)
        storage.remove(1, storage_dir=self.tmpdir)
        storage.remove(2, storage_dir=self.tmpdir)
        count = storage.purge_all(storage_dir=self.tmpdir)
        self.assertEqual(count, 2)
        data = storage.load(self.tmpdir)
        self.assertEqual([i["id"] for i in data["items"]], [3])

    def test_purge_all_returns_zero_when_trash_is_empty(self):
        storage.add("a", storage_dir=self.tmpdir)
        count = storage.purge_all(storage_dir=self.tmpdir)
        self.assertEqual(count, 0)
        data = storage.load(self.tmpdir)
        self.assertEqual(len(data["items"]), 1)

    def test_list_items_excludes_trashed_items_regardless_of_filters(self):
        storage.add("a", storage_dir=self.tmpdir, tags=["work"])
        storage.add("secret-b", storage_dir=self.tmpdir, tags=["work"], due="2026-09-01")
        storage.remove(2, storage_dir=self.tmpdir)

        self.assertEqual(
            [i["id"] for i in storage.list_items(storage_dir=self.tmpdir, status="all")],
            [1],
        )
        self.assertEqual(
            [i["id"] for i in storage.list_items(storage_dir=self.tmpdir, tag="work")],
            [1],
        )
        self.assertEqual(
            [
                i["id"]
                for i in storage.list_items(storage_dir=self.tmpdir, search="secret")
            ],
            [],
        )
        self.assertEqual(
            [i["id"] for i in storage.list_items(storage_dir=self.tmpdir, sort="due")],
            [1],
        )

    def test_done_undone_edit_on_trashed_item_raise_item_not_found_error(self):
        storage.add("a", storage_dir=self.tmpdir)
        storage.remove(1, storage_dir=self.tmpdir)

        with self.assertRaises(storage.ItemNotFoundError):
            storage.done(1, storage_dir=self.tmpdir)
        with self.assertRaises(storage.ItemNotFoundError):
            storage.undone(1, storage_dir=self.tmpdir)
        with self.assertRaises(storage.ItemNotFoundError):
            storage.edit(1, text="b", storage_dir=self.tmpdir)

    def test_deleted_at_survives_save_load_roundtrip(self):
        data = {
            "next_id": 2,
            "items": [
                {
                    "id": 1,
                    "text": "a",
                    "done": False,
                    "created_at": "2026-01-01T00:00:00+09:00",
                    "completed_at": None,
                    "due": None,
                    "priority": None,
                    "tags": [],
                    "deleted_at": "2026-01-05T00:00:00+09:00",
                }
            ],
        }
        storage.save(data, self.tmpdir)
        loaded = storage.load(self.tmpdir)
        self.assertEqual(loaded, data)


class DelayedPurgeTest(StorageTestCase):
    def setUp(self):
        super().setUp()
        self._orig_now = storage.now

    def tearDown(self):
        storage.now = self._orig_now
        super().tearDown()

    def test_item_remains_29_days_after_deletion(self):
        storage.now = lambda: "2026-01-01T00:00:00+09:00"
        storage.add("a", storage_dir=self.tmpdir)
        storage.remove(1, storage_dir=self.tmpdir)

        storage.now = lambda: "2026-01-30T00:00:00+09:00"
        storage.add("b", storage_dir=self.tmpdir)

        data = storage.load(self.tmpdir)
        self.assertEqual([i["id"] for i in data["items"]], [1, 2])

    def test_item_is_physically_purged_after_30_days_on_next_write(self):
        storage.now = lambda: "2026-01-01T00:00:00+09:00"
        storage.add("a", storage_dir=self.tmpdir)
        storage.remove(1, storage_dir=self.tmpdir)

        storage.now = lambda: "2026-02-01T00:00:01+09:00"
        storage.add("b", storage_dir=self.tmpdir)

        data = storage.load(self.tmpdir)
        self.assertEqual([i["id"] for i in data["items"]], [2])

    def test_delayed_purge_does_not_happen_on_list_items(self):
        storage.now = lambda: "2026-01-01T00:00:00+09:00"
        storage.add("a", storage_dir=self.tmpdir)
        storage.remove(1, storage_dir=self.tmpdir)

        storage.now = lambda: "2026-03-01T00:00:00+09:00"
        storage.list_items(storage_dir=self.tmpdir, status="all")

        data = storage.load(self.tmpdir)
        self.assertEqual([i["id"] for i in data["items"]], [1])


if __name__ == "__main__":
    unittest.main()
