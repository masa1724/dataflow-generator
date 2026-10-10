"""注文→配送サンプルの解析結果を期待値と照合する。先にanalyze_java.pyを実行する。"""
import json
from pathlib import Path
r = json.loads((Path(__file__).resolve().parents[1] / 'out/job_data_flow_analysis.json').read_text(encoding='utf-8'))
# job、Mapper、メソッド、途中のサービスメソッド、表→操作の期待値。
cases = [
    ('J003', 'ShippingMapper', 'updateShipmentTime', None, {'SHIPMENTS': 'UPDATE'}),
    ('J001', 'OrderMapper', 'findOrders', 'loadOrders', {'ORDERS': 'SELECT'}),
    ('J001', 'OrderMapper', 'findOrderDetails', 'loadOrders', {'ORDERS': 'SELECT', 'CUSTOMERS': 'SELECT', 'ORDER_ITEMS': 'SELECT'}),
    ('J001', 'UserMapper', 'findById', 'loadUsers', {'USERS': 'SELECT'}),
    ('J001', 'ShippingMapper', 'createRequests', 'createShippingRequests', {'SHIPPING_REQUESTS': 'INSERT', 'ORDERS': 'SELECT', 'ORDER_ITEMS': 'SELECT', 'CUSTOMERS': 'SELECT'}),
    ('J001', 'RegistrationManageMapper', 'recordJ001', None, {'REGISTRATION_MANAGE': 'INSERT'}),
    ('J002', 'InventoryMapper', 'findById', None, {'INVENTORY': 'SELECT'}),
    ('J002', 'ShippingMapper', 'createShipments', 'shipOrders', {'SHIPMENTS': 'INSERT', 'SHIPPING_REQUESTS': 'SELECT', 'ORDER_ITEMS': 'SELECT', 'INVENTORY': 'SELECT'}),
    ('J002', 'ShippingMapper', 'markRequestsShipped', 'shipOrders', {'SHIPPING_REQUESTS': 'UPDATE', 'SHIPMENTS': 'SELECT'}),
    ('J002', 'RegistrationManageMapper', 'findJ001Registration', None, {'REGISTRATION_MANAGE': 'SELECT'}),
]
assert sum(len(c[-1]) for c in cases) == 19
assert len(r['accesses']) == 21
inferred = [a for a in r['accesses'] if a['operation'] == 'SELECT(推定)']
assert {(a['job'], a['table']) for a in inferred} == {('J003', 'SHIPMENTS'), ('J001', 'REGISTRATION_MANAGE')}
assert len(inferred) == 2
for access in inferred:
    assert access['inferred'] and access['xml'] == ''
    assert access['statement'].startswith('java-literal:')
    source, line = access['path'][-1].rsplit(':', 1)
    assert '"' + access['table'] + '"' in Path(source).read_text(encoding='utf-8').splitlines()[int(line) - 1]
    if access['job'] == 'J001':
        assert Path(source).name == 'JobBase.java'
for job, mapper, method, via, expected in cases:
    statement = 'example.mapper.' + mapper + '#' + method
    rows = [a for a in r['accesses'] if a['job'] == job and a['statement'] == statement]
    assert len(rows) == len(expected)
    assert {(a['table'], a['operation']) for a in rows} == set(expected.items())
    for a in rows:
        assert [m.split('#')[1].split('/')[0] for m in a['path']] == ['mainProcess'] + ([via] if via else []) + [method]
        assert a['path'][-1].split('/')[0] == statement
        assert Path(a['xml']).name == mapper + '.xml'
j = next(j for j in r['jobs'].values() if j['job'] == 'J001')
for name in ('JobBase#validateJob/', 'JobHelper#checkEnvironment/', '#checkInput/'):
    assert any(name in m for m in j['reachable_methods'])
assert not any('#beforeProcess/' in m or '#unreachable/' in m for m in j['reachable_methods'])
assert any(u['receiver'] == 'items' and u['declared_type'] == 'java.util.List' for u in j['unresolved'])
assert not any(e['receiver'] == 'items' for e in r['edges'])
assert not any(a['table'] in ('ORDER_ARCHIVE', 'SECRET_AUDIT', 'SHOULD_NOT_APPEAR') for a in r['accesses'])
assert not r['sql_issues'] and not r['source_issues']
print('PASS: 19 XML access paths and 2 Java inferred SELECTs; JobBase and unreachable exclusions')



