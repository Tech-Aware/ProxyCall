import argparse
import logging

from demo.cli import DemoClient, MockJsonStore, MockPoolStore, do_pool_assign, do_pool_provision


logger = logging.getLogger("demo-pool-test")
logger.addHandler(logging.NullHandler())


def test_mock_pool_assign_updates_client(tmp_path):
    clients_path = tmp_path / "clients.json"
    pools_path = tmp_path / "pools.json"

    store = MockJsonStore(clients_path, logger)
    pool = MockPoolStore(pools_path, logger)

    store.save(
        DemoClient(
            client_id=1,
            client_name="Client Demo",
            client_mail="demo@example.com",
            client_real_phone=33601020304,
            client_proxy_number=None,
            client_iso_residency="FR",
            client_country_code="33",
        )
    )

    args = argparse.Namespace(client_id="1", yes=True)
    do_pool_assign(args, store, pool, logger)

    updated = store.get_by_id(1)
    assert updated is not None
    assert updated.client_proxy_number is not None


def test_mock_pool_provision_adds_numbers(tmp_path):
    pools_path = tmp_path / "pools.json"
    pool = MockPoolStore(pools_path, logger)

    before = pool.list_available("FR")
    args = argparse.Namespace(country="FR", batch_size=3)
    do_pool_provision(args, pool, logger)
    after = pool.list_available("FR")

    assert len(after) >= len(before) + 3
