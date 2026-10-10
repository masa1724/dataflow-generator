import com.sun.source.tree.*;
import com.sun.source.util.*;
import javax.tools.*;
import java.nio.file.*;
import java.util.*;

/*
 * Java構文の事実をJSON配列で出力する。接続の推定はPython側で行う。
 * 各オブジェクトのkindが種類を表し、列位置ではなく項目名で読む。
 * CLASS: owner=完全修飾名、parent=親の宣言型、source=ソースURI。
 * IMPORT: owner=所有クラス、name=import先、static=static指定。
 * FIELD: owner=所有クラス、name=変数名、type=宣言型。
 * METHOD: id=メソッドID、owner=所有クラス、name=名前、arity=引数個数。
 * METHOD_INFO: id=メソッドID、line=宣言開始行、return_type=戻り値型。
 * PARAM/LOCAL: method=所有メソッドID、name=変数名、type=宣言型。
 * CALL: method=所有メソッドID、receiver=対象式、name=呼出名、arity=引数個数、line=開始行。
 * CALL_EXPR: method=所有メソッドID、expression=呼び出し式全体。
 * UNSUPPORTED: method=所有メソッドID、reason=省略した構文。
 * メソッドIDは「クラス#名前/引数個数@宣言開始行」。行番号は1始まり。
 * 対象式が空文字なら暗黙の呼び出し。実行順・分岐の実行有無は保証しない。
 */
public class ExtractSyntax {
    static final List<String> records = new ArrayList<>();

    /** 入力：文字列。処理：JSONの引用符・制御文字をエスケープ。戻り値：引用符付き文字列。 */
    static String quote(String value) {
        StringBuilder result = new StringBuilder("\"");
        for (char c : value.toCharArray()) {
            if (c == '"' || c == '\\') result.append('\\').append(c);
            else if (c < 32) result.append(String.format("\\u%04x", (int)c));
            else result.append(c);
        }
        return result.append('"').toString();
    }

    /** 入力：種類と抽出値。処理：名前付きJSONレコードへ変換。戻り値：なし（配列へ蓄積）。 */
    static void emit(Object... values) {
        String kind = String.valueOf(values[0]);
        String[] keys = switch (kind) {
            case "CLASS" -> new String[]{"owner", "parent", "source"};
            case "IMPORT" -> new String[]{"owner", "name", "static"};
            case "FIELD" -> new String[]{"owner", "name", "type"};
            case "METHOD" -> new String[]{"id", "owner", "name", "arity"};
            case "METHOD_INFO" -> new String[]{"id", "line", "return_type"};
            case "PARAM", "LOCAL" -> new String[]{"method", "name", "type"};
            case "CALL" -> new String[]{"method", "receiver", "name", "arity", "line"};
            case "CALL_EXPR" -> new String[]{"method", "expression"};
            case "UNSUPPORTED" -> new String[]{"method", "reason"};
            default -> throw new IllegalArgumentException(kind);
        };
        StringJoiner record = new StringJoiner(", ", "  {", "}");
        record.add("\"kind\": " + quote(kind));
        for (int i = 0; i < keys.length; i++) {
            Object value = values[i + 1];
            // 個数・行番号は数値、static指定は真偽値。それ以外は文字列。
            String encoded = value instanceof Number || value instanceof Boolean
                ? value.toString() : quote(String.valueOf(value));
            record.add(quote(keys[i]) + ": " + encoded);
        }
        records.add(record.toString());
    }
    /**
     * 指定フォルダ以下のJavaソースを読み、宣言・呼び出しを抽出する実行入口。
     * @param args args[0]は解析対象のJavaソースフォルダ
     * @throws Exception ファイル読み込み等に失敗した場合。構文エラーは終了コード2。
     * 処理順：ファイル列挙 → 構文木を作成 → クラス/メソッドを走査 → 診断を確認。
     */
    public static void main(String[] args) throws Exception {
        var compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) throw new IllegalStateException("JDK required");
        // fmはJavaソースをコンパイラへ渡すファイル管理機構。try終了時に閉じる。
        try (var fm = compiler.getStandardFileManager(null, null, java.nio.charset.StandardCharsets.UTF_8);
             var paths = Files.walk(Path.of(args[0]))) {
            // 照合用のフルパスだけ区切りを/に統一する。読込みには元のPathを使う。
            var pattern = args.length > 1 ? java.util.regex.Pattern.compile(args[1]) : null;
            var files = paths.filter(p -> p.toString().endsWith(".java"))
                .filter(p -> pattern == null || pattern.matcher(p.toAbsolutePath().normalize()
                    .toString().replace('\\', '/')).find()).sorted().toList();
            if (files.isEmpty()) throw new IllegalArgumentException("No Java files matched source regex");
            // 診断は標準エラーへ出し、JSONを汚さない。-proc:noneは注釈処理を無効にする。
            var diagnostics = new DiagnosticCollector<JavaFileObject>();
            var task = (JavacTask) compiler.getTask(null, fm, diagnostics, List.of("-proc:none"), null, fm.getJavaFileObjectsFromPaths(files));
            // CRLF/LFや式の途中の改行はJavaパーサーが解釈する。
            var units = task.parse();
            // unitはJavaファイル1つの構文木。positionsは構文要素の文字位置を取得する。
            // 文字位置をunit.getLineMap()で人が読む行番号へ変換する。
            var positions = Trees.instance(task).getSourcePositions();
            for (var unit : units) {
                String pkg = unit.getPackageName() == null ? "" : unit.getPackageName().toString();
                for (var decl : unit.getTypeDecls()) if (decl instanceof ClassTree cls) {
                    // ownerはクラスの完全修飾名。別パッケージの同名クラスを区別する。
                    String owner = pkg.isEmpty() ? cls.getSimpleName().toString() : pkg + "." + cls.getSimpleName();
                    emit("CLASS", owner, cls.getExtendsClause() == null ? "" : cls.getExtendsClause(), unit.getSourceFile().toUri());
                    for (var imp : unit.getImports()) emit("IMPORT", owner, imp.getQualifiedIdentifier(), imp.isStatic());
                    // クラス直下のVariableTreeはフィールド、MethodTreeはメソッド宣言。
                    // メソッド内部の変数は下のTreeScannerで別に記録する。
                    for (var member : cls.getMembers()) {
                        if (member instanceof VariableTree field) emit("FIELD", owner, field.getName(), field.getType());
                        if (member instanceof MethodTree method) {
                            long start = positions.getStartPosition(unit, method);
                            // メソッドID = 所有クラス#名前/引数個数@宣言開始行。
                            // 行番号を含めて、同じ名前・引数個数の宣言も区別する。
                            String id = owner + "#" + method.getName() + "/" + method.getParameters().size() + "@" + unit.getLineMap().getLineNumber(start);
                            emit("METHOD", id, owner, method.getName(), method.getParameters().size());
                            emit("METHOD_INFO", id, unit.getLineMap().getLineNumber(start), method.getReturnType() == null ? "constructor" : method.getReturnType());
                            for (var param : method.getParameters()) emit("PARAM", id, param.getName(), param.getType());
                            // TreeScannerは構文木の各要素でvisitXXXを自動的に呼ぶ。
                            // <Void, Void>は「集約する戻り値なし・受け渡す追加情報なし」。
                            // 以下のメソッドはこの宣言のidを使い、出力の所有メソッドを揃える。
                            new TreeScanner<Void, Void>() {
                                /**
                                 * メソッド内のクラス宣言を検出したときの処理。
                                 * tはその宣言、pは未使用。nullを返し、その内部の走査を止める。
                                 * 内部クラスの呼び出しを外側のメソッドへ混ぜないため。
                                 */
                                public Void visitClass(ClassTree t, Void p) {
                                    emit("UNSUPPORTED", id, "nested/local/anonymous class");
                                    return null;
                                }

                                /**
                                 * 引数・ローカル変数の宣言を記録する。tは変数宣言、pは未使用。
                                 * superを呼び、初期化式に含まれる呼び出しの走査も続ける。
                                 * 引数はPARAMとLOCALの両方に出る。LOCALは名前の隠蔽判定用。
                                 */
                                public Void visitVariable(VariableTree t, Void p) {
                                    emit("LOCAL", id, t.getName(), t.getType());
                                    return super.visitVariable(t,p);
                                }

                                /**
                                 * callはメソッド呼び出しの構文要素、pは未使用。
                                 * 対象式・名前・引数個数・行番号と、式全体を出力する。
                                 * 戻り値は集計に使わず、superで子の構文要素も走査する。
                                 */
                                public Void visitMethodInvocation(MethodInvocationTree call, Void p) {
                                    // a.b()なら対象式=a、メソッド名=b。b()なら対象式は空文字。
                                    // aがフィールドかクラス名かは、ここでは判定しない。
                                    String recv = "", name = call.getMethodSelect().toString();
                                    if (call.getMethodSelect() instanceof MemberSelectTree s) { recv = s.getExpression().toString(); name = s.getIdentifier().toString(); }
                                    emit("CALL", id, recv, name, call.getArguments().size(), unit.getLineMap().getLineNumber(positions.getStartPosition(unit,call)));
                                    emit("CALL_EXPR", id, call.toString());
                                    // 引数や対象式に入れ子になった呼び出しも抽出する。
                                    return super.visitMethodInvocation(call,p);
                                }
                            }.scan(method, null); // このメソッドの引数・本体等から走査を開始。
                        }
                    }
                }
            }
            boolean bad = false;
            for (var d : diagnostics.getDiagnostics()) { System.err.println(d); bad |= d.getKind() == Diagnostic.Kind.ERROR; }
            if (bad) System.exit(2);
            System.out.println("[\n" + String.join(",\n", records) + "\n]");
        }
    }
}
