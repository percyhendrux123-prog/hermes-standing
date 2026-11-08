import sqlite3


def test_seed_and_proof_promotion_are_durable(tmp_path):
    from standing.ledger import Ledger
    path = tmp_path / 'ledger.db'
    ledger = Ledger(lambda: sqlite3.connect(path), default_level=1, promote_after=2, max_auto_level=2)
    ledger.seed({'worker': 0})
    assert ledger.actor('worker')['level'] == 0
    ledger.proof('worker', 'task', 'file', 'a' * 64, True, 'File matched.')
    assert ledger.actor('worker')['passes_window'] == 1
    ledger.proof('worker', 'task', 'file', 'a' * 64, True, 'File matched.')
    assert ledger.actor('worker')['level'] == 1
    assert ledger.actor('worker')['passes_window'] == 0
    assert Ledger(lambda: sqlite3.connect(path)).actor('worker')['level'] == 1


def test_seed_never_lowers_existing_actor_and_default_applies(tmp_path):
    from standing.ledger import Ledger
    ledger = Ledger(lambda: sqlite3.connect(tmp_path / 'db'))
    assert ledger.actor('new')['level'] == 1
    ledger.set_level('worker', 3)
    ledger.seed({'worker': 0, 'other': 0})
    assert ledger.actor('worker')['level'] == 3
    assert ledger.actor('other')['level'] == 0
    ledger.seed({'other': 2})
    assert ledger.actor('other')['level'] == 0


def test_seeds_only_initialize_missing_rows_even_after_reload(tmp_path):
    from standing.ledger import Ledger
    path = tmp_path / 'db'
    ledger = Ledger(lambda: sqlite3.connect(path), promote_after=3)
    ledger.seed({'worker': 2})
    ledger.proof('worker', 'task', 'file', 'hash', False, 'A completed mismatch.')
    ledger.proof('worker', 'task', 'file', 'hash', True, 'The check passed.')
    before = ledger.actor('worker')
    events = ledger.log('worker')
    for seed in (2, 0, 1, 2):
        reloaded = Ledger(lambda: sqlite3.connect(path), default_level=0)
        reloaded.seed({'worker': seed, 'new': 2})
        assert reloaded.actor('worker') == before
        assert reloaded.log('worker') == events
        assert reloaded.actor('new')['level'] == 2
    # Already-created unlisted rows are also not seedable retroactively.
    assert ledger.actor('unlisted')['level'] == 1
    ledger.seed({'unlisted': 2})
    assert ledger.actor('unlisted')['level'] == 1


def test_failure_demotes_immediately_and_resets_window(tmp_path):
    from standing.ledger import Ledger
    ledger = Ledger(lambda: sqlite3.connect(tmp_path / 'db'), promote_after=2, max_auto_level=2)
    ledger.seed({'worker': 2})
    ledger.proof('worker', 'task', 'file', 'a' * 64, True, 'The check passed.')
    ledger.proof('worker', 'task', 'http', 'b' * 64, False, 'The check failed.')
    actor = ledger.actor('worker')
    assert (actor['level'], actor['passes_window'], actor['fails_total']) == (1, 0, 1)
    ledger.proof('worker', 'task', 'file', 'a' * 64, True, 'The check passed.')
    assert ledger.actor('worker')['level'] == 1
    ledger.proof('worker', 'task', 'file', 'a' * 64, True, 'The check passed.')
    assert ledger.actor('worker')['level'] == 2


def test_auto_level_never_reaches_l3_and_failure_floors_at_l0(tmp_path):
    from standing.ledger import Ledger
    ledger = Ledger(lambda: sqlite3.connect(tmp_path / 'db'), promote_after=1, max_auto_level=2)
    for _ in range(8):
        ledger.proof('worker', None, 'file', 'hash', True, 'Passed.')
    assert ledger.actor('worker')['level'] == 2
    for _ in range(8):
        ledger.proof('worker', None, 'file', 'hash', False, 'Failed.')
    assert ledger.actor('worker')['level'] == 0
    assert ledger.actor('worker')['fails_total'] == 8


def test_default_ledger_never_promotes_past_l1(tmp_path):
    from standing.ledger import Ledger
    ledger = Ledger(lambda: sqlite3.connect(tmp_path / 'db'), default_level=0, promote_after=1)
    for _ in range(5):
        ledger.proof('worker', 'task', 'file', 'hash', True, 'The check passed.')
    assert ledger.actor('worker')['level'] == 1
    # An existing owner-set level is not automatically lowered by the new ceiling.
    ledger.set_level('worker', 2)
    ledger.proof('worker', 'task', 'file', 'hash', True, 'The check passed.')
    assert ledger.actor('worker')['level'] == 2


def test_owner_set_resets_window_and_log_is_last_twenty(tmp_path):
    from standing.ledger import Ledger
    ledger = Ledger(lambda: sqlite3.connect(tmp_path / 'db'))
    for _ in range(25):
        ledger.proof('worker', 'task', 'file', 'hash', True, 'Passed.')
    ledger.set_level('worker', 3, 'hashed-reason')
    assert ledger.actor('worker')['passes_window'] == 0
    assert len(ledger.log('worker')) == 20
    assert ledger.log('worker')[0]['kind'] == 'owner'
    assert ledger.listing()[0]['last_event'] == 'L3: Owner set the level.'
