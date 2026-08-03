from __future__ import annotations

import pandas as pd

from kospi_shadow.features import build_feature_table
from kospi_shadow.validation import expanding_walk_forward


def test_outer_training_ends_before_test(synthetic_bundle):
    table, cols = build_feature_table(synthetic_bundle.target, synthetic_bundle.factors)
    oos, _, _ = expanding_walk_forward(
        table, cols, "intraday_up", "intraday_return",
        min_train=504, test_block=84, inner_splits=3, gap=1, random_state=7,
    )
    assert (pd.to_datetime(oos["train_end_date"]) < pd.to_datetime(oos["Date"])).all()
    assert len(oos) >= 250
