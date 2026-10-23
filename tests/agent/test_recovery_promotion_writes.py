import time
from agent import recovery_promotion as promotion


def test_hint_write_dedup_and_bounded_content():
    block = promotion.hint_block('file-search', 'session', [1, 2, 3], time.time())
    assert promotion.validate_hint_write('file-search', '', block) is None
    assert promotion.validate_hint_write('file-search', block, block + '\n' + block)
    assert promotion.validate_hint_write('other-owner', '', block)
    assert promotion.validate_hint_write('file-search', '', block.replace('try grep', 'PRIVATE try grep'))
