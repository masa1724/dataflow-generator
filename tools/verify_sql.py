"""DDL照合と読み書き判定の検証。DB実行・外部ライブラリは不要。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from extract_sql_table_accesses import tokenize_sql, read_ddl_tables, extract_table_accesses
from analyze_java import read_mapper_statements


def main():
    """想定するSQLと負例を検証する。結果が異なればassertで停止。"""
    root = Path(__file__).resolve().parents[1]
    tables = read_ddl_tables(root / 'sample/java/src/main/resources/schema.sql')
    assert set(tables) == {'ORDERS', 'SECRET_AUDIT', 'SHOULD_NOT_APPEAR', 'CUSTOMERS', 'ORDER_ITEMS', 'ORDER_ARCHIVE', 'INVENTORY', 'USERS', 'REGISTRATION_MANAGE', 'SHIPPING_REQUESTS', 'SHIPMENTS'}
    cases = [
        ('SELECT order_id FROM ORDERS', {('ORDERS', 'SELECT')}),
        ('SELECT order_id FROM ORDERS WHERE order_id IN (1,2) ORDER BY order_id', {('ORDERS', 'SELECT')}),
        ('INSERT INTO SECRET_AUDIT(id) SELECT order_id FROM ORDERS', {('SECRET_AUDIT', 'INSERT'), ('ORDERS', 'SELECT')}),
        ('INSERT INTO ORDERS(order_id) SELECT order_id FROM ORDERS', {('ORDERS', 'INSERT'), ('ORDERS', 'SELECT')}),
        ('INSERT INTO ORDERS(order_id) VALUES (#{id})', {('ORDERS', 'INSERT')}),
        ('DELETE FROM ORDERS WHERE order_id IN (SELECT id FROM SECRET_AUDIT)', {('ORDERS', 'DELETE'), ('SECRET_AUDIT', 'SELECT')}),
        ('DELETE FROM ORDERS WHERE order_id IN (SELECT order_id FROM ORDERS)', {('ORDERS', 'DELETE'), ('ORDERS', 'SELECT')}),
        ('UPDATE ORDERS SET processed = (SELECT id FROM SECRET_AUDIT)', {('ORDERS', 'UPDATE'), ('SECRET_AUDIT', 'SELECT')}),
        ('SELECT o.order_id FROM ORDERS o LEFT JOIN SECRET_AUDIT a ON a.id=o.order_id', {('ORDERS', 'SELECT'), ('SECRET_AUDIT', 'SELECT')}),
        ('SELECT o.order_id FROM ORDERS o,SECRET_AUDIT a WHERE a.id=o.order_id', {('ORDERS', 'SELECT'), ('SECRET_AUDIT', 'SELECT')}),
        ('SELECT * FROM (SELECT order_id FROM ORDERS) o JOIN SECRET_AUDIT a ON a.id=o.order_id', {('ORDERS', 'SELECT'), ('SECRET_AUDIT', 'SELECT')}),
        ("SELECT 'FROM SECRET_AUDIT', 'it''s JOIN SHOULD_NOT_APPEAR' FROM ORDERS -- JOIN SECRET_AUDIT\r\nWHERE processed=0 /* FROM SHOULD_NOT_APPEAR */", {('ORDERS', 'SELECT')}),
        ('select * from ORDERS', {('ORDERS', 'SELECT')}),
    ]
    for sql, expected in cases:
        accesses, issues = extract_table_accesses(sql, tables)
        assert set(accesses) == expected and not issues, (sql, accesses, issues)
    # テーブル位置だけプレースホルダーを残し、値のパラメーターは拾わない。
    for sql, expected in [
        ('SELECT * FROM ${tableName} JOIN ORDERS ON ID=#{id}', {('${tableName}', 'SELECT'), ('ORDERS', 'SELECT')}),
        ('INSERT INTO ${outTable} SELECT * FROM #{inTable}', {('${outTable}', 'INSERT'), ('#{inTable}', 'SELECT')}),
        ('DELETE FROM ${tableName} WHERE ID=#{id}', {('${tableName}', 'DELETE')}),
        ('UPDATE ${tableName} SET ID=#{id}', {('${tableName}', 'UPDATE')}),
    ]:
        accesses, issues = extract_table_accesses(sql, tables)
        assert set(accesses) == expected and issues, (sql, accesses, issues)

    # 構文の妥当性を検査するものではない。DDLにない名前は完全一致しない。
    bad_cases = ['SELECT * FROM missing_table', 'SELECT * FROM orders_archive']
    for sql in bad_cases:
        accesses, issues = extract_table_accesses(sql, tables)
        assert issues and not accesses, (sql, accesses, issues)
    assert tokenize_sql('INSERT\tINTO ORDERS(id,processed)\r\nVALUES(1,0);') == [
        'INSERT', 'INTO', 'ORDERS', 'id', 'processed', 'VALUES', '1', '0']
    # この方式の制約：列名がDDLの表名と一致するとSELECTとして拾う。
    accesses, _ = extract_table_accesses('SELECT SECRET_AUDIT FROM ORDERS', tables)
    assert set(accesses) == {('SECRET_AUDIT', 'SELECT'), ('ORDERS', 'SELECT')}

    # DDLの改行・コメントと、XMLを通したINSERT SELECT・行コメントも確認。
    with TemporaryDirectory() as tmp:
        project = Path(tmp)
        resources = project / 'sample/java/src/main/resources'
        resources.mkdir(parents=True)
        ddl = resources / 'schema.sql'
        ddl.write_text('-- CREATE TABLE fake (id INT);\r\nCREATE\r\nTABLE ORDERS (id INT);\nCREATE TABLE SECRET_AUDIT (id INT);', encoding='utf-8')
        catalog = read_ddl_tables(ddl)
        assert set(catalog) == {'ORDERS', 'SECRET_AUDIT'}
        (resources / 'Mapper.xml').write_text('''<mapper namespace="example.Mapper">
<insert id="copy">INSERT INTO SECRET_AUDIT(id)
SELECT order_id FROM ORDERS -- FROM missing_table
WHERE order_id IN (SELECT order_id FROM ORDERS)</insert>
<select id="parameter">SELECT * FROM ${tableName} JOIN ORDERS ON ID=#{id}</select>
<select id="unknown">SELECT * FROM missing_table</select>
<select id="dynamic">SELECT * FROM ORDERS <where>id=1</where></select>
<sql id="source">FROM ORDERS <if test="x">JOIN SECRET_AUDIT ON 1=1</if></sql>
<sql id="loop"><include refid="loop"/></sql>
<select id="included">SELECT * <include refid="source"/></select>
<select id="cross">SELECT * <include refid="other.Mapper.source"><property name="table" value="ORDERS"/></include></select>
<select id="missing">SELECT * FROM ORDERS <include refid="absent"/></select>
<select id="cycle">SELECT * FROM ORDERS <include refid="loop"/></select>
<select id="branches">SELECT * FROM <choose><when test="x">ORDERS</when><otherwise>SECRET_AUDIT</otherwise></choose></select>
</mapper>''', encoding='utf-8')
        (resources / 'Other.xml').write_text('<mapper namespace="other.Mapper"><sql id="source">FROM ${table}</sql></mapper>', encoding='utf-8')
        statements, issues = read_mapper_statements(resources, catalog)
        for name, expected in [('dynamic', {'ORDERS'}), ('included', {'ORDERS', 'SECRET_AUDIT'}), ('cross', {'ORDERS'}), ('branches', {'ORDERS', 'SECRET_AUDIT'}), ('missing', {'ORDERS'}), ('cycle', {'ORDERS'})]:
            assert {a['table'] for a in statements['example.Mapper#' + name]} == expected, name
        assert {(a['table'], a['operation']) for a in statements['example.Mapper#copy']} == {('SECRET_AUDIT', 'INSERT'), ('ORDERS', 'SELECT')}
        assert {i['statement'] for i in issues} == {'example.Mapper#unknown', 'example.Mapper#parameter', 'example.Mapper#missing', 'example.Mapper#cycle'}
        assert {(a['table'], a['operation']) for a in statements['example.Mapper#parameter']} == {('${tableName}', 'SELECT'), ('ORDERS', 'SELECT')}
    print(f'PASS: {len(cases)} supported SQL cases, {len(bad_cases)} negative cases, DDL and Mapper XML integration')


if __name__ == '__main__':
    main()



