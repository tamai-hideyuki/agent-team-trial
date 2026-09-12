"""todo CLI (python -m todo ...) のテスト。DESIGN.md 2章・5章に対応する。

実プロセスを subprocess で起動し、TODO_HOME を一時ディレクトリに向けて実行する。
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="todo-cli-test-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_cli(self, *args):
        env = dict(os.environ)
        env["TODO_HOME"] = self.tmpdir
        result = subprocess.run(
            [sys.executable, "-m", "todo"] + list(args),
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result


class AddCommandTest(CliTestCase):
    def test_add_prints_confirmation_and_exit_0(self):
        result = self.run_cli("add", "牛乳を買う")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "追加しました: #1 牛乳を買う")

    def test_add_increments_id_across_calls(self):
        self.run_cli("add", "牛乳を買う")
        result = self.run_cli("add", "資料を作る")
        self.assertEqual(result.stdout.strip(), "追加しました: #2 資料を作る")

    def test_add_empty_text_errors_with_exit_1(self):
        result = self.run_cli("add", "")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "エラー: TODOの内容を入力してください")

    def test_add_whitespace_only_errors_with_exit_1(self):
        result = self.run_cli("add", "   ")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "エラー: TODOの内容を入力してください")

    def test_add_no_argument_errors_with_exit_1(self):
        result = self.run_cli("add")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "エラー: TODOの内容を入力してください")


class ListCommandTest(CliTestCase):
    def test_list_empty_shows_no_todos_message(self):
        result = self.run_cli("list")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "TODOはありません")

    def test_no_args_behaves_like_list(self):
        self.run_cli("add", "牛乳を買う")
        result_noargs = self.run_cli()
        result_list = self.run_cli("list")
        self.assertEqual(result_noargs.returncode, 0)
        self.assertEqual(result_noargs.stdout, result_list.stdout)

    def test_no_args_shows_no_todos_message_when_empty(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "TODOはありません")

    def test_ls_alias_matches_list(self):
        self.run_cli("add", "牛乳を買う")
        result_ls = self.run_cli("ls")
        result_list = self.run_cli("list")
        self.assertEqual(result_ls.stdout, result_list.stdout)

    def test_list_shows_added_items_in_order(self):
        self.run_cli("add", "牛乳を買う")
        self.run_cli("add", "資料を作る")
        result = self.run_cli("list")
        lines = result.stdout.strip().splitlines()
        self.assertEqual(lines, ["#1 [ ] 牛乳を買う", "#2 [ ] 資料を作る"])

    def test_list_hides_completed_items_by_default(self):
        self.run_cli("add", "牛乳を買う")
        self.run_cli("add", "資料を作る")
        self.run_cli("done", "1")
        result = self.run_cli("list")
        self.assertEqual(result.stdout.strip(), "#2 [ ] 資料を作る")

    def test_list_all_shows_completed_with_mark_and_date(self):
        self.run_cli("add", "牛乳を買う")
        self.run_cli("add", "資料を作る")
        self.run_cli("done", "1")
        result = self.run_cli("list", "--all")
        lines = result.stdout.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(
            lines[0], r"^#1 \[x\] 牛乳を買う \(完了: \d{2}/\d{2}\)$"
        )
        self.assertEqual(lines[1], "#2 [ ] 資料を作る")

    def test_list_without_all_does_not_show_completion_date(self):
        self.run_cli("add", "牛乳を買う")
        self.run_cli("done", "1")
        result = self.run_cli("list", "--all")
        self.assertNotIn("完了", self.run_cli("list").stdout)
        self.assertIn("完了", result.stdout)


class DoneCommandTest(CliTestCase):
    def test_done_prints_confirmation_and_exit_0(self):
        self.run_cli("add", "牛乳を買う")
        result = self.run_cli("done", "1")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "完了にしました: #1 牛乳を買う")

    def test_done_missing_id_errors_with_exit_1(self):
        result = self.run_cli("done", "99")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "エラー: ID 99 は存在しません")


class RmCommandTest(CliTestCase):
    def test_rm_prints_confirmation_and_exit_0(self):
        self.run_cli("add", "牛乳を買う")
        self.run_cli("add", "資料を作る")
        result = self.run_cli("rm", "2")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "削除しました: #2 資料を作る")
        remaining = self.run_cli("list")
        self.assertEqual(remaining.stdout.strip(), "#1 [ ] 牛乳を買う")

    def test_rm_missing_id_errors_with_exit_1(self):
        result = self.run_cli("rm", "42")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "エラー: ID 42 は存在しません")

    def test_rm_and_done_error_message_format_is_identical(self):
        rm_result = self.run_cli("rm", "42")
        done_result = self.run_cli("done", "42")
        rm_msg = re.sub(r"\d+", "N", rm_result.stdout.strip())
        done_msg = re.sub(r"\d+", "N", done_result.stdout.strip())
        self.assertEqual(rm_msg, done_msg)


class DesignExampleScenarioTest(CliTestCase):
    """DESIGN.md 2章の使用例をそのままなぞるシナリオテスト。"""

    def test_full_scenario_matches_design_doc_example(self):
        r1 = self.run_cli("add", "牛乳を買う")
        self.assertEqual(r1.stdout.strip(), "追加しました: #1 牛乳を買う")

        r2 = self.run_cli("add", "資料を作る")
        self.assertEqual(r2.stdout.strip(), "追加しました: #2 資料を作る")

        r3 = self.run_cli("list")
        self.assertEqual(
            r3.stdout.strip().splitlines(),
            ["#1 [ ] 牛乳を買う", "#2 [ ] 資料を作る"],
        )

        r4 = self.run_cli("done", "1")
        self.assertEqual(r4.stdout.strip(), "完了にしました: #1 牛乳を買う")

        r5 = self.run_cli("list")
        self.assertEqual(r5.stdout.strip(), "#2 [ ] 資料を作る")

        r6 = self.run_cli("list", "--all")
        lines = r6.stdout.strip().splitlines()
        self.assertRegex(lines[0], r"^#1 \[x\] 牛乳を買う \(完了: \d{2}/\d{2}\)$")
        self.assertEqual(lines[1], "#2 [ ] 資料を作る")

        r7 = self.run_cli("rm", "2")
        self.assertEqual(r7.stdout.strip(), "削除しました: #2 資料を作る")

        r8 = self.run_cli()
        self.assertEqual(r8.stdout.strip(), "TODOはありません")


class AddWithOptionsTest(CliTestCase):
    def test_add_with_due_priority_tag_shows_annotation(self):
        result = self.run_cli(
            "add", "資料を作る",
            "--due", "2026-09-20", "--priority", "high",
            "--tag", "仕事", "--tag", "至急",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout.strip(),
            "追加しました: #1 資料を作る (期限: 2026-09-20, 優先度: high, タグ: 仕事, 至急)",
        )

    def test_add_without_options_has_no_annotation(self):
        result = self.run_cli("add", "牛乳を買う")
        self.assertEqual(result.stdout.strip(), "追加しました: #1 牛乳を買う")

    def test_add_invalid_due_format_errors(self):
        result = self.run_cli("add", "期限テスト", "--due", "2026-13-01")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stdout.strip(),
            "エラー: 期限の形式が不正です(YYYY-MM-DD形式で指定してください)",
        )

    def test_add_invalid_priority_errors(self):
        result = self.run_cli("add", "優先度テスト", "--priority", "urgent")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stdout.strip(),
            "エラー: 優先度はhigh/medium/lowのいずれかで指定してください",
        )

    def test_add_priority_is_case_insensitive(self):
        result = self.run_cli("add", "資料を作る", "--priority", "HIGH")
        self.assertIn("優先度: high", result.stdout)


class EditCommandTest(CliTestCase):
    def test_edit_text_only(self):
        self.run_cli("add", "牛乳を買う")
        result = self.run_cli("edit", "1", "パンを買う")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "編集しました: #1 パンを買う")
        listing = self.run_cli("list")
        self.assertEqual(listing.stdout.strip(), "#1 [ ] パンを買う")

    def test_edit_option_only(self):
        self.run_cli(
            "add", "資料を作る",
            "--due", "2026-09-20", "--priority", "high", "--tag", "仕事",
        )
        result = self.run_cli("edit", "1", "--priority", "medium")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "編集しました: #1 資料を作る")
        listing = self.run_cli("list")
        self.assertIn("優先度: medium", listing.stdout)

    def test_edit_tag_replaces_entire_set(self):
        self.run_cli("add", "資料を作る", "--tag", "仕事", "--tag", "至急")
        self.run_cli("edit", "1", "--tag", "仕事")
        result = self.run_cli("list", "--tag", "至急")
        self.assertEqual(result.stdout.strip(), "TODOはありません")

    def test_edit_clear_due_priority_tags(self):
        self.run_cli(
            "add", "資料を作る",
            "--due", "2026-09-20", "--priority", "high", "--tag", "仕事",
        )
        result = self.run_cli(
            "edit", "1", "--clear-due", "--clear-priority", "--clear-tags",
        )
        self.assertEqual(result.returncode, 0)
        listing = self.run_cli("list", "--all")
        self.assertEqual(listing.stdout.strip(), "#1 [ ] 資料を作る")

    def test_edit_missing_id_errors(self):
        result = self.run_cli("edit", "99", "内容")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "エラー: ID 99 は存在しません")

    def test_edit_no_fields_specified_errors(self):
        self.run_cli("add", "牛乳を買う")
        result = self.run_cli("edit", "1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "エラー: 変更する項目を指定してください")

    def test_edit_due_and_clear_due_conflict_errors(self):
        self.run_cli("add", "資料を作る")
        result = self.run_cli("edit", "1", "--due", "2026-09-25", "--clear-due")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stdout.strip(),
            "エラー: --due と --clear-due は同時に指定できません",
        )


class UndoneCommandTest(CliTestCase):
    def test_undone_prints_confirmation_and_exit_0(self):
        self.run_cli("add", "牛乳を買う")
        self.run_cli("done", "1")
        result = self.run_cli("undone", "1")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "未完了に戻しました: #1 牛乳を買う")
        listing = self.run_cli("list")
        self.assertEqual(listing.stdout.strip(), "#1 [ ] 牛乳を買う")

    def test_undone_missing_id_errors(self):
        result = self.run_cli("undone", "99")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "エラー: ID 99 は存在しません")


class ListWithOptionsTest(CliTestCase):
    def test_sort_due_places_null_last(self):
        self.run_cli("add", "資料を作る", "--due", "2026-09-20")
        self.run_cli("add", "牛乳を買う")
        result = self.run_cli("list", "--sort", "due")
        lines = result.stdout.strip().splitlines()
        self.assertTrue(lines[0].startswith("#1"))
        self.assertTrue(lines[1].startswith("#2"))

    def test_search_matches_text_case_and_width_insensitively(self):
        self.run_cli("add", "資料を作る")
        result = self.run_cli("list", "--search", "SIRYOU")
        self.assertEqual(result.stdout.strip(), "TODOはありません")
        result2 = self.run_cli("list", "--search", "資料")
        self.assertIn("資料を作る", result2.stdout)

    def test_status_all_matches_all_flag(self):
        self.run_cli("add", "牛乳を買う")
        self.run_cli("done", "1")
        result_status = self.run_cli("list", "--status", "all")
        result_all = self.run_cli("list", "--all")
        self.assertEqual(result_status.stdout, result_all.stdout)

    def test_tag_filter_is_or_condition(self):
        self.run_cli("add", "資料を作る", "--tag", "仕事")
        self.run_cli("add", "買い物", "--tag", "私用")
        result = self.run_cli("list", "--tag", "仕事", "--tag", "私用")
        lines = result.stdout.strip().splitlines()
        self.assertEqual(len(lines), 2)

    def test_due_before_and_due_after_are_inclusive(self):
        self.run_cli("add", "資料を作る", "--due", "2026-09-20")
        result_before = self.run_cli("list", "--due-before", "2026-09-20")
        result_after = self.run_cli("list", "--due-after", "2026-09-20")
        self.assertIn("資料を作る", result_before.stdout)
        self.assertIn("資料を作る", result_after.stdout)

    def test_overdue_item_shows_expired_marker(self):
        self.run_cli("add", "資料を作る", "--due", "2000-01-01")
        result = self.run_cli("list")
        self.assertIn("期限切れ", result.stdout)


class ServeParserTest(unittest.TestCase):
    """serve サブコマンドはDESIGN.md 8.4節の run_server(host, port) にのみ依存する。

    実サーバは起動せずパース結果のみを確認する(server.pyの完成を待たない)。
    """

    def test_serve_default_port(self):
        from todo.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["serve"])
        self.assertEqual(args.port, 8765)
        self.assertEqual(args.func.__name__, "_cmd_serve")

    def test_serve_custom_port(self):
        from todo.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["serve", "--port", "9000"])
        self.assertEqual(args.port, 9000)


class ServeCommandTest(CliTestCase):
    def test_serve_starts_and_prints_url_on_dynamic_port(self):
        env = dict(os.environ)
        env["TODO_HOME"] = self.tmpdir
        proc = subprocess.Popen(
            [sys.executable, "-m", "todo", "serve", "--port", "0"],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            first_line = proc.stdout.readline()
            self.assertRegex(first_line.strip(), r"^起動しました: http://127\.0\.0\.1:\d+$")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
