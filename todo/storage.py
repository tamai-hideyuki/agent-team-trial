"""TODO CLI のデータ永続化層。

保存先ディレクトリ、JSONファイルの読み書き、アトミックな書き込み、
簡易ファイルロック(stale lock対策込み)、ID採番ルール、
due/priority/tags を含むバリデーション、一覧の並び替え・検索・絞り込みを担当する。
公開関数のインターフェースは DESIGN.md 3章・4章・5章に準拠する。
"""

import json
import os
import time
import unicodedata
from datetime import datetime, timezone

DATA_FILENAME = "todo.json"
TMP_FILENAME = "todo.json.tmp"
LOCK_FILENAME = "todo.lock"

STALE_LOCK_SECONDS = 10

VALID_PRIORITIES = ("high", "medium", "low")
PRIORITY_SORT_ORDER = {"high": 0, "medium": 1, "low": 2}


class StorageError(Exception):
    """storage モジュールが送出する例外の基底クラス。"""


class LockAcquisitionError(StorageError):
    """ロック取得に失敗した場合に送出する。"""


class CorruptDataError(StorageError):
    """データファイルのJSONパースや構造検証に失敗した場合に送出する。"""


class ItemNotFoundError(StorageError):
    """指定されたIDのTODOが存在しない場合に送出する。"""

    def __init__(self, item_id):
        self.item_id = item_id
        super().__init__("エラー: ID {} は存在しません".format(item_id))


class InvalidTextError(StorageError):
    """TODOの本文が空文字・空白のみの場合に送出する。"""

    def __init__(self):
        super().__init__("エラー: TODOの内容を入力してください")


class InvalidDueError(StorageError):
    """期限の形式が YYYY-MM-DD として不正な場合に送出する。"""

    def __init__(self):
        super().__init__("エラー: 期限の形式が不正です(YYYY-MM-DD形式で指定してください)")


class InvalidPriorityError(StorageError):
    """優先度が high/medium/low のいずれでもない場合に送出する。"""

    def __init__(self):
        super().__init__("エラー: 優先度はhigh/medium/lowのいずれかで指定してください")


class NoFieldsSpecifiedError(StorageError):
    """edit で変更対象の項目が一つも指定されなかった場合に送出する。"""

    def __init__(self):
        super().__init__("エラー: 変更する項目を指定してください")


class ConflictingOptionsError(StorageError):
    """値指定オプションと対応する --clear-* が同時に指定された場合に送出する。"""

    def __init__(self, value_flag, clear_flag):
        super().__init__(
            "エラー: {} と {} は同時に指定できません".format(value_flag, clear_flag)
        )


def get_storage_dir():
    """保存先ディレクトリを返す。

    環境変数 TODO_HOME が設定されていればそれを使い、
    なければ ~/.todo を使う。
    """
    todo_home = os.environ.get("TODO_HOME")
    if todo_home:
        return os.path.abspath(os.path.expanduser(todo_home))
    return os.path.expanduser("~/.todo")


def _paths(storage_dir):
    return {
        "data": os.path.join(storage_dir, DATA_FILENAME),
        "tmp": os.path.join(storage_dir, TMP_FILENAME),
        "lock": os.path.join(storage_dir, LOCK_FILENAME),
    }


def now():
    """現在時刻をISO 8601形式の文字列で返す(created_at/completed_at用)。

    テストからは storage.now を monkeypatch することで固定できる。
    呼び出し側は `storage.now()` の形でモジュール経由で呼ぶこと。
    """
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today():
    """現在日付を "YYYY-MM-DD" 形式の文字列で返す(期限切れ判定用)。

    テストからは storage.today を monkeypatch することで固定できる。
    呼び出し側は `storage.today()` の形でモジュール経由で呼ぶこと。
    """
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def _stale_lock_seconds():
    raw = os.environ.get("TODO_LOCK_STALE_SECONDS")
    if raw is None:
        return STALE_LOCK_SECONDS
    try:
        return float(raw)
    except ValueError:
        return STALE_LOCK_SECONDS


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 存在はするが別ユーザー所有などでシグナル送信不可 -> 生存扱い
        return True
    except OSError:
        return False
    return True


def _read_lock_file(lock_path):
    """ロックファイルの内容を (pid, acquired_at) で返す。壊れていれば (None, None)。"""
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return None, None
    lines = content.splitlines()
    if len(lines) < 2:
        return None, None
    try:
        pid = int(lines[0])
        acquired_at = float(lines[1])
    except ValueError:
        return None, None
    return pid, acquired_at


def _is_stale(pid, acquired_at):
    if pid is None:
        return True
    if not _pid_alive(pid):
        return True
    if time.time() - acquired_at > _stale_lock_seconds():
        return True
    return False


def acquire_lock(storage_dir):
    """ロックファイルを作成する。取得できなければ LockAcquisitionError を送出する。

    stale なロック(所有プロセスが存在しない、または一定時間経過)は
    破棄したうえで取得を試みる。cli.py・server.py の両方から呼び出せる公開関数。
    """
    lock_path = _paths(storage_dir)["lock"]
    os.makedirs(storage_dir, exist_ok=True)

    for _ in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pid, acquired_at = _read_lock_file(lock_path)
            if _is_stale(pid, acquired_at):
                try:
                    os.remove(lock_path)
                except FileNotFoundError:
                    pass
                continue
            raise LockAcquisitionError(
                "エラー: 他のtodoプロセスが実行中です。しばらくして再試行してください"
            )
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("{}\n{}\n".format(os.getpid(), time.time()))
            return

    raise LockAcquisitionError(
        "エラー: 他のtodoプロセスが実行中です。しばらくして再試行してください"
    )


def _release_lock(storage_dir):
    lock_path = _paths(storage_dir)["lock"]
    pid, _acquired_at = _read_lock_file(lock_path)
    if pid == os.getpid():
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass


class LockGuard:
    """ファイルロックを取得・解放する with 文用のコンテキストマネージャ(公開)。"""

    def __init__(self, storage_dir):
        self.storage_dir = storage_dir

    def __enter__(self):
        acquire_lock(self.storage_dir)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _release_lock(self.storage_dir)
        return False


def _default_data():
    return {"next_id": 1, "items": []}


def load(storage_dir=None):
    """保存データを読み込んで dict で返す。

    ファイルが存在しない場合は初期状態 {"next_id": 1, "items": []} を返す
    (ファイルはこの時点では作成しない)。
    JSONのパースに失敗、または想定した構造でない場合は CorruptDataError を送出する。
    既知フィールドだけを抜き出して再構築せず、item は読み込んだ dict のまま保持する
    (ラウンドトリップ安全性、DESIGN.md 3章)。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()
    data_path = _paths(storage_dir)["data"]

    try:
        with open(data_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return _default_data()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise CorruptDataError(
            "エラー: データファイルが壊れています。手動で確認してください: {}".format(
                data_path
            )
        )

    if (
        not isinstance(data, dict)
        or "next_id" not in data
        or "items" not in data
        or not isinstance(data["next_id"], int)
        or not isinstance(data["items"], list)
    ):
        raise CorruptDataError(
            "エラー: データファイルが壊れています。手動で確認してください: {}".format(
                data_path
            )
        )

    return data


def save(data, storage_dir=None):
    """データをアトミックに書き込む。

    一時ファイル(todo.json.tmp)に全件書き出したのち rename で置き換える。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()
    os.makedirs(storage_dir, exist_ok=True)
    paths = _paths(storage_dir)

    with open(paths["tmp"], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())

    os.replace(paths["tmp"], paths["data"])


def _next_item_id(data):
    max_existing = max((item["id"] for item in data["items"]), default=0)
    return max(data.get("next_id", 1), max_existing + 1)


def validate_due(due_str):
    """due の形式("YYYY-MM-DD")を検証し、そのまま返す。不正なら InvalidDueError。"""
    try:
        datetime.strptime(due_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise InvalidDueError()
    return due_str


def validate_priority(priority_str):
    """priority を検証し、小文字に正規化して返す。不正なら InvalidPriorityError。"""
    if not isinstance(priority_str, str):
        raise InvalidPriorityError()
    normalized = priority_str.strip().lower()
    if normalized not in VALID_PRIORITIES:
        raise InvalidPriorityError()
    return normalized


def _normalize_tags(tags):
    """タグ配列をトリム・空文字除去・去重(順序維持)して返す。"""
    result = []
    seen = set()
    for tag in tags:
        if tag is None:
            continue
        trimmed = tag.strip()
        if trimmed == "" or trimmed in seen:
            continue
        seen.add(trimmed)
        result.append(trimmed)
    return result


def _normalize_for_search(text):
    return unicodedata.normalize("NFKC", text).lower()


def _due_sort_key(item):
    due = item.get("due")
    if due is None:
        return (1, "")
    return (0, due)


def _priority_sort_key(item):
    priority = item.get("priority")
    if priority is None:
        return (1, len(PRIORITY_SORT_ORDER))
    return (0, PRIORITY_SORT_ORDER.get(priority, len(PRIORITY_SORT_ORDER)))


def add(text, storage_dir=None, due=None, priority=None, tags=None):
    """新規TODOを追加する。

    引数:
        text: TODOの本文。空文字・空白のみは InvalidTextError。
        due: "YYYY-MM-DD" 形式の文字列、または None。不正な形式は InvalidDueError。
        priority: "high"/"medium"/"low"(大文字小文字は区別しない)、または None。
        tags: 文字列のリスト、または None(トリム・空文字除去・去重を行う)。
        storage_dir: 保存先ディレクトリ(省略時は get_storage_dir() を使用)。

    返り値: 追加された item(dict)。

    例外:
        InvalidTextError, InvalidDueError, InvalidPriorityError,
        LockAcquisitionError, CorruptDataError。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()
    if text is None or text.strip() == "":
        raise InvalidTextError()
    if due is not None:
        due = validate_due(due)
    if priority is not None:
        priority = validate_priority(priority)
    tags = _normalize_tags(tags) if tags else []

    with LockGuard(storage_dir):
        data = load(storage_dir)
        new_id = _next_item_id(data)
        item = {
            "id": new_id,
            "text": text,
            "done": False,
            "created_at": now(),
            "completed_at": None,
            "due": due,
            "priority": priority,
            "tags": tags,
        }
        data["items"].append(item)
        data["next_id"] = new_id + 1
        save(data, storage_dir)

    return item


def list_items(include_done=True, storage_dir=None, sort=None, search=None,
                status=None, tag=None, due_before=None, due_after=None):
    """保存済みのTODO一覧を返す(読み取り専用、ロック不要)。

    include_done=False の場合は未完了のみを返す(後方互換)。
    status ("all"/"pending"/"done") が指定された場合はそちらを優先する。
    search: text(本文)に対する大文字小文字・全角半角を区別しない部分一致(NFKC正規化)。
    tag: 文字列、または文字列のリスト。複数指定時はOR条件。
    due_before/due_after: "YYYY-MM-DD"。境界値を含む(閉区間)。両方指定時はAND条件。
    sort: "due" または "priority"。安定ソートで、値がnullの項目は末尾に配置する。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()
    data = load(storage_dir)
    items = sorted(data["items"], key=lambda item: item["id"])

    if status == "pending":
        items = [item for item in items if not item["done"]]
    elif status == "done":
        items = [item for item in items if item["done"]]
    elif status is None and not include_done:
        items = [item for item in items if not item["done"]]

    if search:
        needle = _normalize_for_search(search)
        items = [
            item for item in items
            if needle in _normalize_for_search(item["text"])
        ]

    if tag:
        tags_filter = [tag] if isinstance(tag, str) else list(tag)
        items = [
            item for item in items
            if set(item.get("tags", [])) & set(tags_filter)
        ]

    if due_before:
        items = [
            item for item in items
            if item.get("due") is not None and item["due"] <= due_before
        ]

    if due_after:
        items = [
            item for item in items
            if item.get("due") is not None and item["due"] >= due_after
        ]

    if sort == "due":
        items = sorted(items, key=_due_sort_key)
    elif sort == "priority":
        items = sorted(items, key=_priority_sort_key)

    return items


def _find_item(data, item_id):
    for item in data["items"]:
        if item["id"] == item_id:
            return item
    return None


def done(item_id, storage_dir=None):
    """指定IDのTODOを完了にする。

    返り値: 更新後の item(dict)。
    例外: ItemNotFoundError(該当IDが存在しない場合)。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()

    with LockGuard(storage_dir):
        data = load(storage_dir)
        target = _find_item(data, item_id)
        if target is None:
            raise ItemNotFoundError(item_id)

        target["done"] = True
        target["completed_at"] = now()
        save(data, storage_dir)

    return target


def undone(item_id, storage_dir=None):
    """指定IDの完了済みTODOを未完了に戻す。

    返り値: 更新後の item(dict)。
    例外: ItemNotFoundError(該当IDが存在しない場合)。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()

    with LockGuard(storage_dir):
        data = load(storage_dir)
        target = _find_item(data, item_id)
        if target is None:
            raise ItemNotFoundError(item_id)

        target["done"] = False
        target["completed_at"] = None
        save(data, storage_dir)

    return target


def edit(item_id, text=None, due=None, clear_due=False, priority=None,
         clear_priority=False, tags=None, clear_tags=False, storage_dir=None):
    """指定IDのTODOの内容・期限・優先度・タグを編集する(指定した項目のみ更新)。

    --tag(tags)を指定した場合は既存のタグ配列全体を置き換える(追記ではない)。
    due/priority/tags を未設定(null/[])に戻すには clear_due/clear_priority/clear_tags を使う。
    値指定と対応する clear_* を同時に指定した場合は ConflictingOptionsError。
    変更対象の項目が一つも指定されなかった場合は NoFieldsSpecifiedError。

    返り値: 更新後の item(dict)。
    例外:
        ConflictingOptionsError, NoFieldsSpecifiedError, InvalidTextError,
        InvalidDueError, InvalidPriorityError, ItemNotFoundError。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()

    if due is not None and clear_due:
        raise ConflictingOptionsError("--due", "--clear-due")
    if priority is not None and clear_priority:
        raise ConflictingOptionsError("--priority", "--clear-priority")
    if tags is not None and clear_tags:
        raise ConflictingOptionsError("--tag", "--clear-tags")

    if (
        text is None and due is None and not clear_due
        and priority is None and not clear_priority
        and tags is None and not clear_tags
    ):
        raise NoFieldsSpecifiedError()

    if text is not None and text.strip() == "":
        raise InvalidTextError()
    if due is not None:
        due = validate_due(due)
    if priority is not None:
        priority = validate_priority(priority)
    if tags is not None:
        tags = _normalize_tags(tags)

    with LockGuard(storage_dir):
        data = load(storage_dir)
        target = _find_item(data, item_id)
        if target is None:
            raise ItemNotFoundError(item_id)

        if text is not None:
            target["text"] = text
        if due is not None:
            target["due"] = due
        if clear_due:
            target["due"] = None
        if priority is not None:
            target["priority"] = priority
        if clear_priority:
            target["priority"] = None
        if tags is not None:
            target["tags"] = tags
        if clear_tags:
            target["tags"] = []

        save(data, storage_dir)

    return target


def remove(item_id, storage_dir=None):
    """指定IDのTODOを削除する。

    返り値: 削除された item(dict)。
    例外: ItemNotFoundError(該当IDが存在しない場合)。
    """
    if storage_dir is None:
        storage_dir = get_storage_dir()

    with LockGuard(storage_dir):
        data = load(storage_dir)
        target = _find_item(data, item_id)
        if target is None:
            raise ItemNotFoundError(item_id)

        data["items"] = [item for item in data["items"] if item["id"] != item_id]
        save(data, storage_dir)

    return target
