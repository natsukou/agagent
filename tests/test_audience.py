"""用户适配层的单元测试。

重点测两件事：逐年前向测试真的没有看未来，以及三道交付门槛不能被绕过。
"""

from __future__ import annotations

import pytest

from agro.audience import MIN_TRAIN_YEARS, passes_gates, rolling_skill
from agro.multicrop import GroupSample

NAMES = ["stress", "lag_target"]


def _sample(year: int, y: float, lag: float, stress: float = 1.0) -> GroupSample:
    return GroupSample(
        crop="棉花",
        scope="TEST",
        year=year,
        y=y,
        features=[stress, lag],
        names=NAMES,
        factor=stress,
    )


def test_needs_min_train_years_before_scoring():
    """只有 5 年数据、需要 4 个训练年，就只有最后一年可评。"""
    samples = [_sample(2010 + i, 10.0, 10.0) for i in range(MIN_TRAIN_YEARS + 1)]
    rs = rolling_skill(samples)
    assert rs.n == 1
    assert rs.years == [2010 + MIN_TRAIN_YEARS]


def test_year_filters_partition_without_overlap():
    """选择段与验证段必须互不重叠，且合起来等于全部可评年份。"""
    samples = [_sample(2010 + i, 10.0 + i, 9.0 + i) for i in range(10)]
    full = rolling_skill(samples)
    early = rolling_skill(samples, max_year=2016)
    late = rolling_skill(samples, min_year=2017)
    assert set(early.years).isdisjoint(late.years)
    assert sorted(early.years + late.years) == full.years


def test_training_set_never_includes_the_scored_year():
    """把某一年的真值改得极端，之前年份的评分不能变。这是没看未来的直接证据。"""
    base = [_sample(2010 + i, 10.0, 9.0) for i in range(8)]
    tampered = list(base)
    tampered[-1] = _sample(2017, 999.0, 9.0)
    a = rolling_skill(base, max_year=2016)
    b = rolling_skill(tampered, max_year=2016)
    assert a.mean_abs_err_model == b.mean_abs_err_model
    assert a.wins == b.wins


def test_model_wins_when_persist_is_wrong():
    """真值恒定、上年值来回跳：模型应该赢过照抄去年，技能分为正。"""
    samples = [
        _sample(2010 + i, 10.0, 5.0 if i % 2 == 0 else 15.0) for i in range(10)
    ]
    rs = rolling_skill(samples)
    assert rs.n > 0
    assert rs.win_rate == pytest.approx(1.0)
    assert rs.skill_pooled is not None and rs.skill_pooled > 0
    assert rs.deliverable is True


def test_deliverable_needs_both_skill_and_win_rate():
    """技能分靠一年大胜拉起来、逐年胜率不过半的组，第一道门槛就不该过。"""
    # 前九年照抄去年完全正确，模型只能小错；最后一年照抄去年大错，模型赢一次。
    samples = [
        _sample(2010 + i, 10.0 + 0.1 * (-1) ** i, 10.0 + 0.1 * (-1) ** i, stress=float(i))
        for i in range(9)
    ]
    samples.append(_sample(2019, 10.1, 40.0, stress=9.0))
    rs = rolling_skill(samples)
    assert rs.skill_pooled is not None and rs.skill_pooled > 0
    assert rs.win_rate is not None and rs.win_rate < 0.5
    assert rs.deliverable is False


def test_three_gates_are_conjunctive():
    assert passes_gates(True, True, 0.3) is True
    assert passes_gates(True, True, -0.01) is False
    assert passes_gates(True, False, 0.9) is False
    assert passes_gates(False, True, 0.9) is False


def test_missing_verification_blocks_delivery():
    """验证段算不出技能分时按不交付处理，不能默认放行。"""
    assert passes_gates(True, True, None) is False
