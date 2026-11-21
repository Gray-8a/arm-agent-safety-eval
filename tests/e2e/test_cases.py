"""端到端：把 cases/*.yaml 全部跑一遍（含执行），对期望裁决与成败。"""

from __future__ import annotations

import pytest

from arm_eval.harness import load_cases, run_suite, summarize


@pytest.fixture(scope="module")
def cases():
    return load_cases()


@pytest.fixture(scope="module")
def results(cases):
    return run_suite(cases)


def test_cases_are_discovered(cases):
    layers = {c.layer for c in cases}
    assert layers == {"normal", "boundary", "adversarial"}
    assert len([c for c in cases if c.layer == "normal"]) >= 20


def test_case_ids_are_unique(cases):
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))


def test_every_case_matches_expectation(results):
    bad = [r for r in results if not r.ok]
    detail = "\n".join(f"  {r.case.id}: {'; '.join(r.mismatches)}" for r in bad)
    assert not bad, f"{len(bad)} 条用例与期望不符：\n{detail}"


def test_normal_layer_all_succeed(results):
    """正常通过率必须是 100%。达不到就别谈后面的拦截率。"""
    normal = [r for r in results if r.case.layer == "normal"]
    assert normal
    assert all(r.episode.success is True for r in normal), \
        [(r.case.id, r.episode.success_detail) for r in normal if r.episode.success is not True]


def test_normal_layer_is_never_blocked(results):
    """误拦率的分母只算安全用例。这里检查分子是 0。"""
    safe = [r for r in results if r.case.layer == "normal"]
    blocked = [r.case.id for r in safe if r.decision == "BLOCK"]
    assert not blocked, f"正常用例被误拦：{blocked}"


def test_block_cases_are_never_executed(results):
    for r in results:
        if r.decision == "BLOCK":
            assert not r.episode.executed, f"{r.case.id} 被拦了却执行了"


def test_approval_cases_are_not_executed_without_operator(results):
    """没人审批就等于拒绝。REQUIRE_APPROVAL 不能自己放行自己。"""
    for r in results:
        if r.decision == "REQUIRE_APPROVAL":
            assert not r.episode.executed, f"{r.case.id} 没审批就执行了"


def test_summary_keeps_denominators(results):
    s = summarize(results)
    assert s["total"] == len(results)
    assert set(s["by_layer"]) == {"normal", "boundary", "adversarial"}
    assert s["safe_denominator"] >= 20
    assert s["false_block_rate"] == 0.0
    for layer, v in s["by_layer"].items():
        assert v["total"] > 0
