"""TODO CLI のコマンドラインインターフェース。

DESIGN.md 2章(コマンド一覧・使用例)・5章(UX上の設計判断)・8.4節(serveの契約)に準拠する。
バリデーションロジックは持たず、todo/storage.py の公開関数を呼び出すのみとする。
"""

import argparse
import sys

from todo import storage

DEFAULT_SERVE_PORT = 8765


def _format_annotations(item, show_completed_at):
    """期限・優先度・タグ・(--all時の)完了日時を1つの括弧にまとめて返す。

    DESIGN.md 5.8節・5.9節に対応する。何も付記すべき項目が無ければ空文字を返す。
    """
    parts = []

    if show_completed_at and item["done"] and item["completed_at"]:
        dt = item["completed_at"]
        # ISO8601 "YYYY-MM-DDTHH:MM:SS..." から MM/DD を取り出す
        month = dt[5:7]
        day = dt[8:10]
        parts.append("完了: {}/{}".format(month, day))

    due = item.get("due")
    if due:
        parts.append("期限: {}".format(due))
        if storage.is_overdue(item):
            parts.append("期限切れ")

    priority = item.get("priority")
    if priority:
        parts.append("優先度: {}".format(priority))

    tags = item.get("tags")
    if tags:
        parts.append("タグ: {}".format(", ".join(tags)))

    if not parts:
        return ""
    return " ({})".format(", ".join(parts))


def _format_item(item, show_completed_at):
    mark = "x" if item["done"] else " "
    line = "#{} [{}] {}".format(item["id"], mark, item["text"])
    line += _format_annotations(item, show_completed_at)
    return line


def _cmd_add(args):
    item = storage.add(args.text, due=args.due, priority=args.priority, tags=args.tag)
    message = "追加しました: #{} {}".format(item["id"], item["text"])
    message += _format_annotations(item, show_completed_at=False)
    print(message)


def _cmd_list(args):
    show_all = args.all or args.status == "all"
    items = storage.list_items(
        include_done=args.all,
        sort=args.sort,
        search=args.search,
        status=args.status,
        tag=args.tag,
        due_before=args.due_before,
        due_after=args.due_after,
    )
    if not items:
        print("TODOはありません")
        return
    for item in items:
        print(_format_item(item, show_completed_at=show_all))


def _cmd_done(args):
    item = storage.done(args.id)
    print("完了にしました: #{} {}".format(item["id"], item["text"]))


def _cmd_undone(args):
    item = storage.undone(args.id)
    print("未完了に戻しました: #{} {}".format(item["id"], item["text"]))


def _cmd_rm(args):
    item = storage.remove(args.id)
    print("削除しました: #{} {}".format(item["id"], item["text"]))


def _cmd_edit(args):
    item = storage.edit(
        args.id,
        text=args.text,
        due=args.due,
        clear_due=args.clear_due,
        priority=args.priority,
        clear_priority=args.clear_priority,
        tags=args.tag,
        clear_tags=args.clear_tags,
    )
    print("編集しました: #{} {}".format(item["id"], item["text"]))


def _cmd_serve(args):
    from todo import server

    server.run_server(port=args.port)


def _build_parser():
    parser = argparse.ArgumentParser(prog="todo", description="軽量なTODO管理CLI")
    subparsers = parser.add_subparsers(dest="command")

    add_parser = subparsers.add_parser("add", help="新規TODOを追加する")
    add_parser.add_argument("text", nargs="?", default="")
    add_parser.add_argument("--due", default=None)
    add_parser.add_argument("--priority", default=None)
    add_parser.add_argument("--tag", action="append", default=None)
    add_parser.set_defaults(func=_cmd_add)

    list_parser = subparsers.add_parser("list", aliases=["ls"], help="TODOを一覧表示する")
    list_parser.add_argument("--all", action="store_true")
    list_parser.add_argument("--sort", default=None)
    list_parser.add_argument("--search", default=None)
    list_parser.add_argument("--status", default=None)
    list_parser.add_argument("--tag", action="append", default=None)
    list_parser.add_argument("--due-before", dest="due_before", default=None)
    list_parser.add_argument("--due-after", dest="due_after", default=None)
    list_parser.set_defaults(func=_cmd_list)

    done_parser = subparsers.add_parser("done", help="指定IDのTODOを完了にする")
    done_parser.add_argument("id", type=int)
    done_parser.set_defaults(func=_cmd_done)

    undone_parser = subparsers.add_parser("undone", help="指定IDの完了済みTODOを未完了に戻す")
    undone_parser.add_argument("id", type=int)
    undone_parser.set_defaults(func=_cmd_undone)

    rm_parser = subparsers.add_parser("rm", help="指定IDのTODOを削除する")
    rm_parser.add_argument("id", type=int)
    rm_parser.set_defaults(func=_cmd_rm)

    edit_parser = subparsers.add_parser("edit", help="指定IDのTODOを編集する")
    edit_parser.add_argument("id", type=int)
    edit_parser.add_argument("text", nargs="?", default=None)
    edit_parser.add_argument("--due", default=None)
    edit_parser.add_argument("--clear-due", action="store_true")
    edit_parser.add_argument("--priority", default=None)
    edit_parser.add_argument("--clear-priority", action="store_true")
    edit_parser.add_argument("--tag", action="append", default=None)
    edit_parser.add_argument("--clear-tags", action="store_true")
    edit_parser.set_defaults(func=_cmd_edit)

    serve_parser = subparsers.add_parser("serve", help="ブラウザから操作できるローカルサーバを起動する")
    serve_parser.add_argument("--port", type=int, default=DEFAULT_SERVE_PORT)
    serve_parser.set_defaults(func=_cmd_serve)

    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        args = parser.parse_args(["list"])

    try:
        args.func(args)
    except storage.StorageError as e:
        print(str(e))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
