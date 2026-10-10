"""Pythonファイル全体からDDLのテーブル名を検索し、既存のテーブルアクセスCSVに追記する。"""
import argparse
import csv
import ast
import re
import tokenize
from pathlib import Path

from extract_sql_table_accesses import read_ddl_tables, read_ddl_logical_names, tokenize_sql

COLUMNS = ['job', 'table', 'operation', 'path', 'xml', 'statement', 'table_logical_name']


def read_selects(path, tables):
    """入力：PythonファイルとDDLのテーブル辞書。

    処理：文字列を取り出し、JavaのSQL解析と共通のtokenize_sqlで分割する。
    各トークンを大文字に揃え、DDLのテーブル辞書と完全一致で照合する。
    戻り値：(文字列の開始行, テーブル名)の一覧。同じ行・表は一件にする。
    コメントや変数名は除外する。説明用の文字列も対象で、SELECTの有無は問わない。
    f文字列は固定部分だけを検索し、埋め込み式の値は追跡しない。
    ソースは実行しない。変数展開や文字列連結、他ファイルの値は追跡しない。
    """
    with tokenize.open(path) as stream:
        source = stream.read()
    tree = ast.parse(source, filename=str(path))
    results = set()

    def visit(node):
        # f文字列の式は検索しない。固定部分だけを文字列として扱う。
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    collect(part.value, node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            collect(node.value, node.lineno)
        else:
            for child in ast.iter_child_nodes(node):
                visit(child)

    def collect(value, line):
        for token in tokenize_sql(value):
            table = tables.get(token.upper())
            if table is not None:
                results.add((line, table))

    visit(tree)
    return sorted(results)


def read_inserts(path, jobs, tables):
    """入力：プロパティ・ジョブID一覧・DDL辞書。処理：大文字Tで始まる項目の指定を読む。

    戻り値：(ジョブID, テーブル, 行番号, キー)の一覧。
    ファイル名のジョブIDと完全一致を優先し、不一致なら最後の_以降だけ除いて照合する。
    ジョブ名の途中の_は残す。
    項目名の残りや添え字は検査しない。UTF-8の1行1設定を対象とする。
    """
    result = []
    for line, text in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        text = text.strip()
        if not text or text.startswith(('#', '!')):
            continue
        key, separator, value = text.partition('=')
        match = re.fullmatch(r'(?:[^.\s]+\.)*([^.\s]+)\.T.*', key.strip())
        if not separator or not match:
            continue
        segment = match.group(1)
        job = segment if segment in jobs else segment.rsplit('_', 1)[0] if '_' in segment else None
        if job not in jobs:
            continue  # 別のジョブの設定は取り込まない。
        table = tables.get(value.strip().upper())
        if table is None:
            raise ValueError(f'{path}:{line}: table not found in DDL: {value.strip()}')
        result.append((job, table, line, key.strip()))
    return result


def append_accesses(python_dir, ddl, output, properties=None, source_regex=None):
    """入力：Pythonフォルダ・DDL・既存CSV。処理：ファイル名をジョブIDとして追記。

    戻り値：新しく追加した行数。
    出力先は指定されたプロパティからINSERTとして取得。xml列は空欄、path列に出典を記録。
    同じ行の再追記は除外する。ソース変更前の古い行を消す場合はJava解析から再生成する。
    """
    if not python_dir.is_dir():
        raise ValueError(f'Python directory not found: {python_dir}')
    pattern = re.compile(source_regex) if source_regex is not None else None
    files = [p for p in sorted(python_dir.rglob('*.py')) if pattern is None or pattern.search(p.resolve().as_posix())]
    if not files:
        raise ValueError('No Python files matched source regex')
    tables = read_ddl_tables(ddl)
    names = read_ddl_logical_names(ddl)
    with output.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != COLUMNS:
            raise ValueError('Unexpected access CSV columns')
        rows = list(reader)
    known = {tuple(row[c] for c in COLUMNS) for row in rows}
    added = 0
    jobs = {}
    for path in files:
        if path.stem in jobs:
            raise ValueError(f'Duplicate job filename: {path} and {jobs[path.stem]}')
        jobs[path.stem] = path
        for line, table in read_selects(path, tables):
            row = dict(zip(COLUMNS, [path.stem, table, 'SELECT(推定)',
                f'{path.resolve()}:{line}', '', f'python:{line}', names.get(table.upper(), '')]))
            key = tuple(row[c] for c in COLUMNS)
            if key not in known:
                rows.append(row)
                known.add(key)
                added += 1
    if properties is not None:
        for job, table, line, key in read_inserts(properties, jobs, tables):
            row = dict(zip(COLUMNS, [job, table, 'INSERT',
                f'{properties.resolve()}:{line}', '', key, names.get(table.upper(), '')]))
            identity = tuple(row[c] for c in COLUMNS)
            if identity not in known:
                rows.append(row)
                known.add(identity)
                added += 1
    # 全ファイルを読み終えてから保存し、抽出エラー時は既存CSVを変更しない。
    temporary = output.with_suffix(output.suffix + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return added


def main():
    """入力：必須の3引数。処理：抽出とCSV追記。戻り値：なし。"""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('python-dir', 'ddl', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--properties', type=Path, help='INSERT先を定義したUTF-8プロパティ')
    parser.add_argument('--source-regex', help='Pythonフルパス（/区切り）への正規表現。省略時は全件')
    args = parser.parse_args()
    print(f'Added {append_accesses(args.python_dir, args.ddl, args.output, args.properties, args.source_regex)} accesses')


if __name__ == '__main__':
    main()
