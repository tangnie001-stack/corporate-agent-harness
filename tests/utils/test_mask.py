"""节点脱敏：两条链路同一实现。"""

from src.utils.mask import MASKED_NODE, mask_nodes


def test_masks_ipv4_with_port():
    assert mask_nodes("node 10.1.2.3:8080 slow") == f"node {MASKED_NODE} slow"


def test_masks_bare_ipv4():
    assert mask_nodes("host=10.1.2.3") == f"host={MASKED_NODE}"


def test_keeps_non_ip_text():
    assert mask_nodes("p50 30ms") == "p50 30ms"
