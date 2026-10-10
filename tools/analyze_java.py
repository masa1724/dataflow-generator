"""Java構文抽出 → 呼び出し照合 → XML/SQL抽出 → ジョブ別集計。

まず末尾のmain()を読むと全体の流れが分かる。各段階は独立した関数に分割。
syntaxは抽出索引、reportは解析結果。関数の引数で渡し、グローバル状態を使わない。
外部Pythonライブラリは不要。未解決が残る場合、結果の網羅性は保証しない。
"""
import argparse
import csv
import json
import re
import subprocess
from urllib.parse import urlsplit
from urllib.request import url2pathname
from collections import defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET
from extract_sql_table_accesses import read_ddl_tables, read_ddl_logical_names, extract_table_accesses

def extract_java_syntax(java_dir, output_dir, source_regex=None):
    """Javaソースから構文情報を抽出する。
    
    入力：
        java_dir：JavaソースのフォルダPath。output_dir：出力先のPath。
    処理：
        Java抽出器をコンパイルして実行し、標準出力をJSONファイルにも保存する。
    戻り値：
        JSON全体の文字列。コンパイル・実行に失敗した場合は例外で停止する。
    具体例：
        sample/java/src/main/java以下の宣言・呼び出し → out/java_syntax.jsonと同内容の文字列。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    extractor = Path(__file__).with_name('ExtractSyntax.java')
    subprocess.run(['javac', '-encoding', 'UTF-8', '-d', str(output_dir), str(extractor)], check=True)
    command = ['java', '-cp', str(output_dir), 'ExtractSyntax', str(java_dir)]
    if source_regex is not None:
        command.append(source_regex)
    run = subprocess.run(command, check=True, capture_output=True, encoding='utf-8')
    (output_dir / 'java_syntax.json').write_text(run.stdout, encoding='utf-8')
    return run.stdout


def parse_syntax_records(syntax_json):
    """抽出したJSONを、名前で検索できる辞書に整理する。
    
    入力：
        syntax_json：extract_java_syntaxが返すJSON文字列。
    処理：
        各行の種類に応じて、クラス・メソッド・フィールド・呼び出し等を登録する。
    戻り値：
        syntax辞書。内部の各辞書のキーと値は下のコメントを参照。
    具体例：
        FIELD行のフィールド名と宣言型 → fields[所有クラス][フィールド名]に宣言型を保存。
    """
    # 2. JSONを索引化。キーはクラス完全修飾名またはメソッドID。
    # 補足情報のMETHOD_INFO/PARAM/CALL_EXPRは接続処理には使用しない。
    classes, methods, imports, fields = {}, {}, defaultdict(dict), defaultdict(dict)
    # 各辞書の形（キー → 値）：
    # classes: クラス完全修飾名 → 親の宣言型・ソースURI
    # methods: メソッドID → 所有クラス・名前・引数個数（arity）
    # imports: 所有クラス → 短い型名 → import先の完全修飾名
    # fields: 所有クラス → フィールド名 → ソース上の宣言型
    # defaultdict(dict)は初めてのキーにも空辞書を用意する。
    locals_, calls, issues = defaultdict(dict), [], []
    # locals_: メソッドID → ローカル変数/引数名 → 宣言型。
    # callsはCALLレコードのリスト、issuesは未対応の構文/importの記録。
    for record in json.loads(syntax_json):
        kind = record['kind']
        if kind == 'CLASS':
            classes[record['owner']] = {'parent': record['parent'], 'source': record['source']}
        elif kind == 'IMPORT':
            owner, name = record['owner'], record['name']
            if record['static'] or name.endswith('.*'):
                issues.append({'owner': owner, 'reason': 'unsupported import', 'value': name})
            else:
                imports[owner][name.rsplit('.', 1)[-1]] = name
        elif kind == 'FIELD': fields[record['owner']][record['name']] = record['type']
        elif kind == 'METHOD':
            methods[record['id']] = {key: record[key] for key in ('owner', 'name', 'arity')}
        elif kind == 'LOCAL': locals_[record['method']][record['name']] = record['type']
        elif kind == 'CALL':
            calls.append([record[key] for key in ('method', 'receiver', 'name', 'arity', 'line')])
        elif kind == 'UNSUPPORTED': issues.append({'method': record['method'], 'reason': record['reason']})
    return {'classes': classes, 'methods': methods, 'imports': imports, 'fields': fields, 'locals': locals_, 'calls': calls, 'issues': issues}


def type_name(owner, raw, syntax):
    """短い宣言型を、パッケージを含む名前の候補にする。
    
    入力：
        owner：宣言元クラスの完全修飾名。raw：ソース上の型名。
        syntax：parse_syntax_recordsが返す抽出情報。
    処理：
        型引数を取り除き、import先を探す。なければ同じパッケージの名前を補う。
    戻り値：
        型名の候補を表す文字列。実在や実装クラスを保証する型解決ではない。
    具体例：
        List<Long> → List → importにjava.util.Listがあればjava.util.List。
    """
    classes, imports = syntax['classes'], syntax['imports']
    # List<Long>の<Long>を除去する。型引数の中身は照合には使わない。
    raw = re.sub(r'<.*>', '', raw).strip()
    if raw in classes or '.' in raw: return raw
    return imports[owner].get(raw, owner.rsplit('.', 1)[0] + '.' + raw)

def ancestors(owner, syntax):
    """自クラスから親クラスへ順にたどる。
    
    入力：
        owner：起点のクラス完全修飾名。syntax：抽出情報。
    処理：
        抽出済みのクラスだけをたどり、未収集の親や訪問済みのクラスで止める。
    戻り値：
        クラス名を一つずつ返すジェネレータ。for文で順に取得できる。
    具体例：
        J001Job → JobBase。どちらも抽出済みなら、この順で名前を返す。
    """
    classes = syntax['classes']
    # 訪問済みクラスを覚え、継承関係の循環で無限にたどらないようにする。
    seen = set()
    while owner in classes and owner not in seen:
        seen.add(owner)
        yield owner
        parent = classes[owner]['parent']
        owner = type_name(owner, parent, syntax) if parent else ''

def field_type(owner, name, syntax):
    """自クラスまたは親クラスで宣言されたフィールドの型を探す。
    
    入力：
        owner：呼び出し元クラスの完全修飾名。name：フィールド名。
        syntax：抽出情報。
    処理：
        自クラスから親へ探し、見つかった宣言型をtype_nameで補う。
    戻り値：
        型名の候補を表す文字列。見つからなければNone。
    具体例：
        orderMapperの宣言型OrderMapperとimport → example.mapper.OrderMapper。
    """
    fields = syntax['fields']
    # 親で見つかったフィールドは、その親のimportで型名を補う。
    for cls in ancestors(owner, syntax):
        if name in fields[cls]: return type_name(cls, fields[cls][name], syntax)


def resolve_calls(syntax):
    """Javaの呼び出し元と呼び出し先を照合する。
    
    入力：
        syntax：クラス・メソッド・宣言型・呼び出しを収めた抽出情報。
    処理：
        対象式から候補クラスを探し、名前と引数個数が一致するメソッドを調べる。
    戻り値：
        (edges, unresolved)。edgesは一意に照合できた接続、unresolvedは未解決・曖昧な呼び出しのリスト。
    具体例：
        orderMapper.findOrders() → 宣言型OrderMapperに引数0個のfindOrdersが一つなら接続。
    """
    classes, methods = syntax['classes'], syntax['methods']
    imports, locals_, calls = syntax['imports'], syntax['locals'], syntax['calls']
    edges, unresolved = [], []
    # 3. 対象式から候補クラスを絞り、名前と引数個数が一意なら接続。
    # ローカル変数による名前の隠蔽や曖昧な候補を、フィールドへ誤接続しない。
    for src, recv, name, argc, line in calls:
        # src=呼び出し元メソッドID、recv=ドット左側、argc=実引数個数。
        # targetは相手クラスの候補。kindは照合の種類を結果に残すためのラベル。
        owner = methods[src]['owner']
        target, kind = None, 'unresolved'
        if recv in ('', 'this'): target, kind = owner, 'self'
        elif recv == 'super': target, kind = type_name(owner, classes[owner]['parent'], syntax), 'parent'
        elif recv in locals_[src]: kind = 'local/parameter receiver (unsupported)'
        elif recv.startswith('this.') and recv.count('.') == 1:
            target, kind = field_type(owner, recv[5:], syntax), 'field'
        elif field_type(owner, recv, syntax): target, kind = field_type(owner, recv, syntax), 'field'
        elif recv in imports[owner] or recv in classes or owner.rsplit('.', 1)[0] + '.' + recv in classes:
            target, kind = type_name(owner, recv, syntax), 'static'
        candidates = []
        # 子クラスに候補があれば親は調べない。同じ引数個数の候補が複数なら曖昧。
        # 引数の型やDIの実装クラスを使った選択は、このツールでは行わない。
        for cls in ancestors(target, syntax):
            candidates = [mid for mid, m in methods.items() if m['owner'] == cls and m['name'] == name and m['arity'] == int(argc)]
            if candidates: break
        base = {'from': src, 'receiver': recv, 'method': name, 'line': int(line), 'kind': kind, 'declared_type': target}
        if len(candidates) == 1: edges.append(dict(base, to=candidates[0]))
        else: unresolved.append(dict(base, reason='ambiguous overload' if candidates else 'external or unresolved', candidates=candidates))

    return edges, unresolved


def read_mapper_statements(mapper_dir, tables, source_regex=None):
    """Mapper XMLから、SQLによるテーブルアクセスを取り出す。
    
    入力：
        mapper_dir：Mapper XMLを探すフォルダのPath。
        tables：DDLから取得したテーブル名の辞書。
    処理：
        XMLからSQL本文を取り出し、extract_table_accessesへ本文とtablesを渡す。
    戻り値：
        (statements, sql_issues)。statementsはSQL識別キー→アクセス情報のリスト、
        sql_issuesは未対応のXMLやSQLの要確認事項。
    具体例：
        namespaceがexample.mapper.OrderMapper、idがfindOrdersなら、
        example.mapper.OrderMapper#findOrdersをキーにordersへのSELECT等を登録。
    """
    statements, sql_issues = defaultdict(list), []
    for xml in sorted(mapper_dir.rglob('*.xml')):
        # Javaと同じ指定で、XMLのフルパスも/区切りにして照合する。
        if source_regex is not None and not re.search(source_regex, xml.resolve().as_posix()):
            continue
        root = ET.parse(xml).getroot()
        if root.tag != 'mapper':
            continue
        for stmt in root:
            if stmt.tag not in ('select', 'insert', 'update', 'delete'):
                continue
            key = root.attrib['namespace'] + '#' + stmt.attrib['id']
            # 行コメントの終わりを保つため、トークン化前に改行を消さない。
            sql = ''.join(stmt.itertext()).strip()
            # 子要素を持つ動的SQL/includeは組み立てない。文字列展開は未確定の名前として抽出する。
            if len(stmt):
                accesses, problems = [], ['dynamic SQL/include is unsupported']
            else:
                accesses, problems = extract_table_accesses(sql, tables)
            for problem in problems:
                sql_issues.append({'statement': key, 'reason': problem, 'sql': sql})
            for table, operation in accesses:
                statements[key].append({
                    'table': table, 'operation': operation,
                    'xml': str(xml.resolve()), 'statement': key, 'sql': sql,
                })
    return statements, sql_issues


def collect_job_accesses(syntax, edges, unresolved, statements):
    """各ジョブの入口から、到達するテーブルアクセスを集計する。
    
    入力：
        syntax：抽出情報。edges：呼び出し接続。unresolved：未解決の呼び出し。
        statements：MapperのSQL識別キーごとのアクセス情報。
    処理：
        パッケージや引数個数を問わずmainProcessを探し、呼び出し先を順にたどる。
    戻り値：
        (jobs, results)。jobsは入口ごとの到達メソッド・未解決情報、
        resultsはジョブIDと根拠経路を付けたテーブルアクセスのリスト。
    具体例：
        mainProcess → loadOrders → OrderMapper.findOrders → ordersへのSELECT。
    """
    methods = syntax['methods']
    adj = defaultdict(list)
    # adj: 呼び出し元メソッドID → 呼び出し先IDのリスト（グラフの隣接リスト）。
    # 5. 共通グラフを一度作り、各mainProcessから到達可能な経路だけを集計。
    for edge in edges: adj[edge['from']].append(edge['to'])
    jobs, results = {}, []
    for entry, m in methods.items():
        match = re.search(r'\.job\.([^.]+)\.', m['owner'])
        if m['name'] != 'mainProcess': continue
        # 従来のIDは維持するが、パッケージ構成による対象の除外はしない。
        job = match.group(1).upper() if match else m['owner'].rsplit('.', 1)[-1]
        reached = set()
        def walk(node, path):
            """現在のメソッドから呼び出し先を順にたどる。
            
            入力：
                node：現在のメソッドID。path：ここへ来るまでのメソッドIDのリスト。
            処理：
                到達先を記録し、SQLとの対応を調べた後、各呼び出し先で同じ処理を行う。
            戻り値：
                なし。外側のreachedとresultsに結果を追加する。jobとentryも外側の値を使う。
            具体例：
                loadOrdersに来たら経路へ追加し、次にOrderMapper.findOrdersをたどる。
            """
            # 同一経路内の再訪は循環として止める。別経路からの到達は根拠として残す。
            if node in path: return
            reached.add(node)
            path = path + [node]  # 新しいリストにし、別の分岐の経路に混ざらないようにする。
            method = methods[node]
            # Java側の「所有クラス#メソッド名」とXML側のキーが一致したらSQLへ接続。
            # 一致しない場合は追加しない。対応SQLなしの専用警告はまだ実装していない。
            for access in statements.get(method['owner'] + '#' + method['name'], []):
                results.append(dict(access, job=job, entry=entry, path=path))
            for dst in adj[node]: walk(dst, path)
        walk(entry, [])
        jobs[entry] = {'job': job, 'reachable_methods': sorted(reached), 'unresolved': [x for x in unresolved if x['from'] in reached]}

    return jobs, results


def collect_java_literal_accesses(syntax, jobs, tables):
    """入力：構文索引・ジョブの到達メソッド・DDL辞書。

    処理：到達メソッドを定義したJavaファイル全体から、引用符付きDDL名を検索する。
    戻り値：推定SELECTの一覧。同じジョブ・テーブルは一件にまとめる。
    大文字のDDL名をそのまま照合し、コメントや未使用の定数も対象とする。
    値の受け渡しは追跡しない。到達メソッドがないクラスの定数だけの参照は対象外。
    複数ファイルで見つかる場合はパス順で最初のファイル・行を根拠にする。
    """
    accesses = []
    texts = {}
    seen = set()
    for entry, job in jobs.items():
        paths = set()
        for method in job['reachable_methods']:
            owner = syntax['methods'][method]['owner']
            source = syntax['classes'][owner]['source']
            uri = urlsplit(source)
            path = Path(url2pathname(('//' + uri.netloc if uri.netloc else '') + uri.path)) if uri.scheme == 'file' else Path(source)
            paths.add(path.resolve())
        for path in sorted(paths):
            if path not in texts:
                texts[path] = path.read_text(encoding='utf-8-sig')
            text = texts[path]
            for table in tables.values():
                identity = (job['job'], table)
                index = text.find('"' + table + '"')
                if index < 0 or identity in seen:
                    continue
                seen.add(identity)
                line = text.count('\n', 0, index) + 1
                accesses.append(dict(job=job['job'], entry=entry, table=table, operation='SELECT(推定)',
                    path=[entry, f'{path}:{line}'], xml='',
                    statement=f'java-literal:{line}', sql='', inferred=True))
    return accesses


def write_reports(report, output_dir):
    """解析結果をJSONとCSVに保存する。
    
    入力：
        report：解析結果の辞書。output_dir：作成済みの出力先Path。
    処理：
        解析全体をJSONへ、reportのaccessesを一覧CSVへ書き出す。
    戻り値：
        なし。出力先に2ファイルを作成または上書きする。
    具体例：
        詳細はjob_data_flow_analysis.json、一覧はjob_table_accesses.csv。
    """
    results = report['accesses']
    (output_dir / 'job_data_flow_analysis.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (output_dir / 'job_table_accesses.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['job', 'table', 'operation', 'path', 'xml', 'statement', 'table_logical_name'])
        for r in results: writer.writerow([r['job'], r['table'], r['operation'], ' -> '.join(r['path']), r['xml'], r['statement'], r.get('table_logical_name', '')])


def print_summary(report):
    """解析結果の概要をコンソールへ表示する。
    
    入力：
        report：解析結果の辞書。
    処理：
        アクセスごとのジョブ・表・操作・経路と、各種件数を標準出力へ書く。
    戻り値：
        なし。ファイルは作成しない。
    具体例：
        J001 | orders | SELECT | mainProcessからMapperまでの経路。
    """
    jobs, results = report['jobs'], report['accesses']
    edges, unresolved = report['edges'], report['unresolved']
    issues, sql_issues = report['source_issues'], report['sql_issues']
    for r in results: print(f"{r['job']} | {r['table']} | {r['operation']} | " + ' -> '.join(r['path']))
    print(f'jobs={len(jobs)} accesses={len(results)} edges={len(edges)} unresolved={len(unresolved)} source_issues={len(issues)} sql_issues={len(sql_issues)}')


def parse_arguments(argv=None):
    """入力：コマンドライン引数。処理：パスを検証。戻り値：設定したパス群。

    相対パスは実行時のカレントフォルダ基準。4つの引数はすべて必須。
    例：--java-dir sample/java/src/main/java --mapper-dir sample/java/src/main/resources --ddl schema.sql --output-dir out
    """
    parser = argparse.ArgumentParser(description='JavaとMapper XMLからジョブ別テーブルアクセスを解析します。')
    parser.add_argument('--java-dir', type=Path, required=True, help='Javaソースのフォルダ（配下を再帰検索）')
    parser.add_argument('--mapper-dir', type=Path, required=True, help='Mapper XMLのフォルダ（配下を再帰検索）')
    parser.add_argument('--ddl', type=Path, required=True, help='CREATE TABLEを記載したDDLファイル')
    parser.add_argument('--output-dir', type=Path, required=True, help='結果の出力フォルダ（同名の出力は上書き）')
    parser.add_argument('--source-regex', help='Javaフルパス（/区切り）への正規表現。省略時は全件')
    args = parser.parse_args(argv)
    for name in ('java_dir', 'mapper_dir', 'ddl', 'output_dir'):
        setattr(args, name, getattr(args, name).resolve())
    for name in ('java_dir', 'mapper_dir'):
        if not getattr(args, name).is_dir():
            parser.error(f'{name}: フォルダが見つかりません: {getattr(args, name)}')
    if not args.ddl.is_file():
        parser.error(f'DDLファイルが見つかりません: {args.ddl}')
    if not any(args.java_dir.rglob('*.java')):
        parser.error('指定フォルダ内にJavaソースがありません')
    if not any(args.mapper_dir.rglob('*.xml')):
        parser.error('指定フォルダ内にXMLファイルがありません')
    if args.output_dir.exists() and not args.output_dir.is_dir():
        parser.error('出力先にはフォルダを指定してください')
    return args


def main(argv=None):
    """解析全体を順に実行する入口。
    
    入力：
        argv：コマンドライン引数。省略時は実際の起動引数を使う。
    処理：
        下の1〜6の順で関数を呼び、各段階の結果を次の関数へ渡す。
    戻り値：
        なし。JSON・JSON・CSVを保存し、概要をコンソールへ表示する。
    具体例：
        python tools/analyze_java.pyで、この関数から処理を開始する。
    """
    args = parse_arguments(argv)
    output_dir = args.output_dir

    # 1. Javaソースから宣言・呼び出しを抽出する。
    syntax_json = extract_java_syntax(args.java_dir, output_dir, args.source_regex)
    # 2. 抽出レコードを検索しやすい索引にする。
    syntax = parse_syntax_records(syntax_json)
    # 3. Javaの呼び出し元と呼び出し先を照合する。
    edges, unresolved = resolve_calls(syntax)
    # 4. Mapper XMLからSQLと参照テーブルを取り出す。
    ddl_path = args.ddl
    tables = read_ddl_tables(ddl_path)
    logical_names = read_ddl_logical_names(ddl_path)
    statements, sql_issues = read_mapper_statements(args.mapper_dir, tables, args.source_regex)
    # 5. ジョブ入口からたどり、テーブルアクセスを集計する。
    jobs, accesses = collect_job_accesses(syntax, edges, unresolved, statements)
    accesses.extend(collect_java_literal_accesses(syntax, jobs, tables))
    for access in accesses:
        access['table_logical_name'] = logical_names.get(access['table'].upper(), '')
    # 6. 解析全体と一覧を保存する。
    report = {
        'jobs': jobs, 'accesses': accesses, 'edges': edges,
        'unresolved': unresolved, 'source_issues': syntax['issues'],
        'sql_issues': sql_issues,
        'ddl': {'source': str(ddl_path), 'tables': sorted(tables.values()), 'logical_names': logical_names},
    }
    write_reports(report, output_dir)
    print_summary(report)


# 別のスクリプトからimportしただけでは解析を実行しない。
if __name__ == '__main__':
    main()






