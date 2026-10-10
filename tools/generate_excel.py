"""テーブルアクセスCSVとジョブ名TSVからExcelを生成する。必要ライブラリ：openpyxl。"""
import argparse
import csv
import math
import re
from pathlib import Path
from extract_sql_table_accesses import read_ddl_tables

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


def display_table_name(name):
    """物理名は大文字にし、未確定のパラメーター名は元の表記を保つ。"""
    return name if re.search(r'[#$]\{[^}]*\}', name) else name.upper()


def table_label(access, fallback=''):
    """Excel表示用の論理名。推定SELECTには末尾に印を付ける。"""
    name = access.get('table_logical_name') or fallback
    return name + ('★推定' if access['operation'] == 'SELECT(推定)' else '')


def paint(cell, value=None, color=None, bold=False, size=11, text='253746', center=False):
    """入力：セルと表示設定。処理：値・書式を設定。戻り値：なし。例：見出しを太字にする。"""
    if value is not None:
        cell.value = str(value)
        cell.data_type = 's'  # ソース中の名前をExcel数式として実行させない。
    cell.font = Font(name='Arial', size=size, bold=bold, color=text)
    cell.alignment = Alignment(horizontal='center' if center else 'left', vertical='center', wrap_text=True)
    if color:
        cell.fill = PatternFill('solid', fgColor=color)


def read_job_names(path):
    """入力：ジョブID・論理名・機能名・色のTSV。処理：表示設定を読み込む。

    戻り値：ID→表示設定の辞書。例：J001に配送・淡い青を設定する。
    4列すべて必須。機能名は空欄不可・文字数制限なし。色は#RRGGBB形式。
    空欄・重複IDはエラー。UTF-8のBOM付きファイルも受け付ける。
    """
    names = {}
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream, delimiter='\t')
        if reader.fieldnames != ['job_id', 'logical_name', 'function_name', 'color']:
            raise ValueError('TSV columns: job_id, logical_name, function_name, color')
        for row in reader:
            job = (row.get('job_id') or '').strip()
            name = (row.get('logical_name') or '').strip()
            if not job or not name or None in row or job in names:
                raise ValueError(f'Invalid or duplicate job name at TSV line {reader.line_num}')
            function = (row.get('function_name') or '').strip()
            color = (row.get('color') or '').strip()
            if not function or not re.fullmatch(r'#[0-9A-Fa-f]{6}', color):
                raise ValueError(f'Expected non-empty function and #RRGGBB color at TSV line {reader.line_num}')
            names[job] = {'logical_name': name, 'function_name': function, 'color': color.lstrip('#') or 'E3EBF0'}
    return names


def read_accesses(path):
    """入力：解析ツールのアクセスCSV。処理：列と操作を確認して各行を読み込む。

    戻り値：アクセスの辞書リスト。例：J001・orders・SELECTの行を読み込む。
    CSVに登場するジョブだけをExcelに出す。根拠経路などの追加列は表示に使わない。
    """
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not {'job', 'table', 'operation'}.issubset(reader.fieldnames or []):
            raise ValueError('CSV requires job, table, operation columns')
        accesses = []
        for row in reader:
            if None in row or any(not (row.get(key) or '').strip() for key in ('job', 'table', 'operation')):
                raise ValueError(f'Invalid access at CSV line {reader.line_num}')
            row = {key: (value or '').strip() for key, value in row.items()}
            if row['operation'] not in ('SELECT', 'SELECT(推定)', 'INSERT', 'UPDATE', 'DELETE'):
                raise ValueError(f'Invalid operation at CSV line {reader.line_num}')
            accesses.append(row)
        return accesses


def build_workbook(access_rows, job_names=None, table_order=None):
    """入力：CSVのアクセス一覧とTSVの表示設定。処理：ID順でジョブごとの入出力を縦に並べる。

    戻り値：保存前のWorkbook。例：J001、J002、J003を各1回表示する。
    実行順序は推定せず、配置やモードの追記は出力後のExcelで行う。
    """
    job_names = job_names or {}
    table_order = table_order or {}
    jobs = sorted({a['job'] for a in access_rows})
    wb = Workbook()
    ws = wb.active
    ws.title = 'DFD'
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = 'A7'
    paint(ws['A2'], 'ジョブとテーブルのアクセス', bold=True, size=16)
    paint(ws['A3'], 'ジョブID順の入出力一覧。実行順序は未解析。配置はExcelで編集できます。', size=10)
    ws['A3'].alignment = Alignment(wrap_text=False)
    col = 1
    # 手動でジョブを横へ移せるよう、見出しと列幅だけ5組分用意する。
    # 右側の空欄は実行順序を表すものではない。
    for group in range(5):
        start_col = group * 4 + 1
        for offset, title in enumerate(['入力（SELECT）', 'ジョブ', '出力']):
            paint(ws.cell(6, start_col + offset), title, '24445C', True, text='FFFFFF')
        for offset, width in enumerate([29, 20, 34, 3]):
            ws.column_dimensions[get_column_letter(start_col + offset)].width = width
    ws.row_dimensions[6].height = 26
    row = 7
    for job in jobs:
        accesses = [a for a in access_rows if a['job'] == job]
        unique = {(a['table'], a['operation']): a for a in accesses}
        # DDLの宣言順で表示する。DDLにない表は末尾に名前順で残す。
        ordered = sorted(unique.values(), key=lambda a: (
            table_order.get(display_table_name(a['table']), len(table_order)), a['table'], a['operation']))
        inputs = [a for a in ordered if a['operation'] in ('SELECT', 'SELECT(推定)')]
        outputs = [a for a in ordered if a['operation'] not in ('SELECT', 'SELECT(推定)')]
        count = max(1, len(inputs), len(outputs))
        height = count * 2
        metadata = job_names.get(job, {})
        function = metadata.get('function_name', '')
        job_color = metadata.get('color', 'E3EBF0')
        lines = ([metadata['logical_name']] if metadata.get('logical_name') else []) + [job]
        if function:
            lines.insert(0, f'【{function}】')
        needed = math.ceil((len(lines) + 1) * 18 / height)
        for r in range(height):
            dim = ws.row_dimensions[row + r]
            dim.height = max(dim.height or 0, 23 if r % 2 == 0 else 19, needed)
        for offset, entries in [(0, inputs), (2, outputs)]:
            for i, access in enumerate(entries):
                r = row + i * 2
                physical_name = display_table_name(access['table'])
                name = table_label(access, physical_name)
                title = name + (f" [{access['operation']}]" if offset == 2 else '')
                color = 'F3F6F8'
                paint(ws.cell(r, col + offset), title, color, True)
                paint(ws.cell(r + 1, col + offset), physical_name if access.get('table_logical_name') else '', color, size=9, text='6B7280')
        # 機能名・ジョブ名・IDを同じセルにまとめ、まとめて編集できるようにする。
        ws.merge_cells(start_row=row, start_column=col + 1, end_row=row + height - 1, end_column=col + 1)
        for r in range(row, row + height):
            ws.cell(r, col + 1).fill = PatternFill('solid', fgColor=job_color)
        paint(ws.cell(row, col + 1), '\n'.join(lines), job_color, True, text='24445C')
        row += height + 1
    # 一覧の並べ替えや絞り込み用。根拠経路が違っても同じアクセスは1行にまとめる。
    flat = wb.create_sheet('CRUD一覧')
    flat.freeze_panes = 'A2'
    flat.sheet_view.showGridLines = False
    headers = ['機能名', 'ジョブ論理名', 'ジョブ物理名', 'テーブル論理名', 'テーブル物理名', '操作']
    for col, (title, width) in enumerate(zip(headers, [14, 30, 20, 26, 32, 14]), 1):
        paint(flat.cell(1, col), title, '24445C', True, text='FFFFFF')
        flat.column_dimensions[get_column_letter(col)].width = width
    flat.row_dimensions[1].height = 26
    unique_rows = {(a['job'], display_table_name(a['table']), a['operation']): a for a in access_rows}
    ordered_rows = sorted(unique_rows.values(), key=lambda a: (
        a['job'], table_order.get(display_table_name(a['table']), len(table_order)), display_table_name(a['table']), a['operation']))
    for row, access in enumerate(ordered_rows, 2):
        metadata = job_names.get(access['job'], {})
        values = [metadata.get('function_name', ''), metadata.get('logical_name', ''),
                  access['job'], table_label(access), display_table_name(access['table']), access['operation']]
        for col, value in enumerate(values, 1):
            paint(flat.cell(row, col), value, 'F3F6F8' if row % 2 == 0 else 'FFFFFF')
        flat.row_dimensions[row].height = 30
    flat.auto_filter.ref = f'A1:F{max(1, flat.max_row)}'
    return wb


def main():
    """入力：必須のinput/job-names/output引数。処理：読込・生成・保存。戻り値：なし。

    例：python tools/generate_excel.py --input out/job_table_accesses.csv
        --job-names tools/job_names.tsv --output out/job_table_accesses.xlsx
    """
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--job-names', type=Path, required=True, help='job_id・logical_name・function_name・colorのUTF-8 TSV')
    parser.add_argument('--ddl', type=Path, required=True, help='テーブルの表示順に使うDDLファイル')
    args = parser.parse_args()
    table_order = {name: index for index, name in enumerate(read_ddl_tables(args.ddl))}
    wb = build_workbook(read_accesses(args.input), read_job_names(args.job_names), table_order)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    wb.save(args.output)
    print('Generated:', args.output)


if __name__ == '__main__':
    main()



