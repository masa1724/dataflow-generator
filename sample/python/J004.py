"""登録管理を参照するジョブ4。書き込み先の定義はjobs.propertiesに置く。"""

SQL = """
SELECT JOB_ID, REGISTERED_AT
FROM REGISTRATION_MANAGE
"""

SHIPMENTS_TABLE = "SHIPMENTS"

SHIPMENTS_SQL = f"""
SELECT ORDER_ID, SHIPPED_AT
FROM {SHIPMENTS_TABLE}
"""


def read_registrations(connection):
    """入力：DB接続。処理：登録管理を参照。戻り値：取得した行。解析時は実行しない。"""
    return connection.execute(SQL).fetchall()


def read_shipments(connection):
    """入力：DB接続。処理：出荷を参照。戻り値：取得した行。解析時は実行しない。"""
    return connection.execute(SHIPMENTS_SQL).fetchall()
