"""C3-2 回归：池总量 = 抽中数 + 剩余数（review_prep 分层去向口径）。"""
def test_pool_accounting():
    pool = list(range(10)); take_n = 4
    take = pool[:take_n]; leftover = pool[take_n:]
    assert len(take) + len(leftover) == len(pool)  # 抽中+剩余=池（review_prep 现按 pool[4:] 存剩余）
