"""区切り文字で分割し、DDLにある名前と一致するトークンを照合する簡易処理。

SQLの構造は解析しない。列名・別名が表名と一致すると過検出する前提の推定。

読み進める順番：
1. tokenize_sql：SQLを区切り文字で分け、文字列リストにする。
2. read_ddl_tables：DDLファイルを読み、内容をトークンのリストにして、CREATE TABLE直後の名前を登録する。
3. extract_table_accesses：SQLの各トークンをDDLから取得したテーブル名と照合し、直前の語で操作を決める。
"""
import re


def tokenize_sql(sql):
    """SQLを区切り文字で分割する。

    入力：
        sql：SQL全体の文字列。
    処理：
        コメントを除き、文字列値だけを ? に置換する。MyBatisパラメーターはそのまま保持する。
        空白・タブ・改行・カンマ・括弧・セミコロンで分割する。
    戻り値：
        トークン（文字列）のリスト。区切り文字と空文字は含めない。
    具体例：
        "INSERT INTO orders(id) VALUES(#{id});"
        → ['INSERT', 'INTO', 'orders', 'id', 'VALUES', '#{id}']
    """
    # 文字列とコメントを一括で処理し、文字列内の--や/*をコメントと誤認しない。
    pattern = r"'(?:''|[^'])*'|--[^\r\n]*|/\*[\s\S]*?\*/"
    # --行コメントと/*ブロックコメント*/は空白にする。
    # '文字列'は ? にする。パラメーターは下の分割でもひとかたまりとして保持する。
    cleaned = re.sub(pattern, lambda m: ' ' if m.group().startswith(('--', '/*')) else ' ? ', sql)
    # ドットは分割しないのでschema.tableは1トークン。括弧の階層は管理しない。
    # \sはスペース・タブ・改行。+は区切り文字の連続をまとめて扱う指定。
    # 先頭・末尾が区切り文字だと空文字ができるため、if tokenで取り除く。
    return re.findall(r'[^\s,();]*[#$]\{[^}]*\}[^\s,();]*|[^\s,();]+', cleaned)


def read_ddl_tables(path):
    """DDLファイルからテーブル名を取得する。

    入力：
        path：CREATE TABLE 名前 (...) を複数記載できるDDLファイルのPath。
    処理：
        ファイル全体を読み、tokenize_sqlで内容をトークンのリストにする。
        CREATE TABLEの直後のトークンをテーブル名として登録する。
    戻り値：
        テーブル名の辞書。キーは大文字小文字を統一した照合用の名前、
        値は結果表示に使うDDL上の名前。重複や定義なしの場合は例外にする。
    具体例：
        ファイル内容 "CREATE TABLE Orders (id BIGINT);"
        → {'orders': 'Orders'}
    """
    # ファイル自体は分割しない。読み込んだ内容だけを文字列リストにする。
    tokens = tokenize_sql(path.read_text(encoding='utf-8-sig'))
    tables = {}
    # CREATE TABLEの2語より後ろから走査する。DDL全体の文法検証はしない。
    for i in range(2, len(tokens)):
        # iは現在位置。i-2:iは現在のトークンを含まない直前2語。
        if [t.upper() for t in tokens[i - 2:i]] == ['CREATE', 'TABLE']:
            name = tokens[i]
            if name.upper() in tables:
                raise ValueError('duplicate DDL table: ' + name)
            tables[name.upper()] = name.upper()
    if not tables:
        raise ValueError('no CREATE TABLE definitions found in DDL')
    return tables


def read_ddl_logical_names(path):
    """入力：DDLのPath。処理：CREATE TABLE直前の -- ID:論理名 を読む。

    戻り値：物理名のupperをキー、論理名を値とする辞書。
    具体例：-- ENT01:注文 の次にCREATE TABLE ordersがあればorders→注文。
    空行は許可する。指定形式のコメントがないテーブルは登録しない。
    """
    text = path.read_text(encoding='utf-8-sig')
    # ブロックコメント内の記載は定義として扱わない。
    text = re.sub(r'/\*[\s\S]*?\*/', '', text)
    pattern = r'^\s*--\s*[^:\r\n]+:[ \t]*([^\r\n]+)\r?\n\s*CREATE\s+TABLE\s+([^\s(]+)'
    return {m.group(2).upper(): m.group(1).strip()
            for m in re.finditer(pattern, text, re.MULTILINE | re.IGNORECASE)}


def extract_table_accesses(sql, tables):
    """SQL内のテーブル名と操作を取得する。

    入力：
        sql：SQL全体の文字列。
        tables：read_ddl_tablesが返す、DDLから取得したテーブル名の辞書。
    処理：
        SQLの各トークンを、DDLから取得したテーブル名と完全一致で照合する。
        一致した名前の直前がINSERT INTOならINSERT、DELETE FROMならDELETE、
        UPDATEならUPDATE、それ以外はSELECTとする。
    戻り値：
        (アクセス一覧, 要確認事項の一覧)。アクセスは(テーブル名, 操作)の組。
        同じテーブル・操作の重複は除く。一致がない場合は要確認事項を返す。
    具体例：
        SQL "INSERT INTO secret_audit(id) SELECT order_id FROM orders"
        両テーブルがtablesにあれば、
        ([('secret_audit', 'INSERT'), ('orders', 'SELECT')], [])
    """
    tokens = tokenize_sql(sql)
    accesses = []
    issues = []
    for i, token in enumerate(tokens):
        # 部分一致ではない。orders_archiveはordersには一致しない。
        # upper()で大文字小文字を統一し、ORDERSもordersと照合する。
        table = tables.get(token.upper())
        # 未登録の名前は無視する。既知の表と混在する未知名にも個別警告は出さない。
        if table is None:
            # テーブル位置のパラメーターは実名未確定のまま残す。WHEREやVALUESの値は拾わない。
            before = tokens[i - 1].upper() if i else ''
            pair = [t.upper() for t in tokens[max(0, i - 2):i]]
            if re.search(r'[#$]\{[^}]*\}', token) and (before in ('FROM', 'JOIN', 'UPDATE') or pair == ['INSERT', 'INTO']):
                table = token
                issues.append('parameterized table name: ' + token)
            else:
                continue
        # 先頭付近では前の語が存在しないので、空文字・空リストを使う。
        previous = tokens[i - 1].upper() if i >= 1 else ''
        previous_two = [t.upper() for t in tokens[i - 2:i]] if i >= 2 else []
        if previous_two == ['INSERT', 'INTO']:
            operation = 'INSERT'
        elif previous_two == ['DELETE', 'FROM']:
            operation = 'DELETE'
        elif previous == 'UPDATE':
            operation = 'UPDATE'
        else:
            # FROM/JOINの構造は見ず、書き込み3条件に当てはまらないものをSELECTにする。
            # 列名や別名がテーブル名と一致する場合も拾うのが、この方式の制約。
            operation = 'SELECT'
        # 同じ表へのSELECTはまとめるが、同じ表のINSERTとSELECTは両方残す。
        if (table, operation) not in accesses:
            accesses.append((table, operation))
    if not accesses:
        issues.append('no DDL table name matched')
    return accesses, issues
