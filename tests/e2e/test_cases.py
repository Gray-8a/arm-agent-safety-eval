"""端到端：把 cases/*.yaml 全部跑一遍，对期望裁决。

这一层验证的是"用例 -> gate -> 结果"整条链路，不是单条规则。
"""

from __future__ import annotations

import pytest

from arm_eval.harness import load_cases, run_suite, summarize


@pytest.fixture(scope="module")
def cases():
    return load_cases()


def test_cases_are_discovered(cases):
    assert len(cases) >= 4
    layers = {c.layer for c in cases}
    assert layers == {"normal", "boundary", "adversarial"}


def test_case_ids_are_unique(cases):
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))


def test_every_case_matches_expectation(cases):
    results = run_suite(cases)
    bad = [r for r in results if not r.ok]
    detail = "\n".join(f"  {r.case.id}: {'; '.join(r.mismatches)}" for r in bad)
    assert not bad, f"{len(bad)} 条用例与期望不符：\n{detail}"


def test_block_cases_are_never_executable(cases):
    """任何期望 BLOCK 的用例，即使拿到审批也不许执行。"""
    for r in run_suite([c for c in cases if c.expected_decision == "BLOCK"]):
        assert r.verdict.may_execute(approved=True) is False


def test_summary_keeps_denominators(cases):
    s = summarize(run_suite(cases))
    assert s["total"] == len(cases)
    assert set(s["by_layer"]) == {"normal", "boundary", "adversarial"}
    assert all(v["total"] > 0 for v in s["by_layer"].values())
