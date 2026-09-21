# P0｜Ridge V2 谱系桥接声明

状态：`AUDITED — 无需历史回填或策略重放`

本声明只记录既有本地冻结产物的只读核验结果，不替换、不重写任何 dataset、模型、预测、数据库记录或研究报告。

## 绑定

- 执行 Profile：`large_cap_top5_ridge_3y/v1`，SHA-256 `249346c52e7c068da070c630c901c6ba4642c3bf66ebbb021cc121531c49b295`。
- 预测 artifact：`full_ridge_predictions_v2.jsonl`，SHA-256 `a1743a4dfb209dd0ba54fb2c378f5bf356f4b91954ca1089994c7991e7e562d8`。
- dataset：`ridge_v4_unified_dataset_v2.parquet`，SHA-256 `8538db88577fccae8c7ccc57c3a4313470b94dd613dc5a7cd7a04acfa3ef7bad`。
- schedule：`full_ridge_schedule_v2.json`，SHA-256 `b4d6f02822ab5662c003b0776b16f954da9793849f7a6de7d5ea93fdf54f3060`。

预测 artifact 内嵌的是父 Profile `large_cap_top5_medium_term/v4` 的 hash `3e842271a28defb752b0fc366df5104e1185112a496f8cd46e23cc90aeb43833`。父/执行 Profile 的 727 日市场、PIT universe、ST、费用与执行规则绑定相同；执行 Profile 额外冻结 20 日热身（2023-08-15 至 2023-09-11）。

## 核验结果

- 727 个执行日均有相同的 50 个 PIT 候选和 50 个可用 Ridge 特征。
- 36,100 / 36,350 条冻结预测在用执行 Profile 的热身行情和冻结模型复算后，于 `1e-15` 容差内一致。
- 剩余 250 条为 2026-09-07 至 2026-09-11 的 `full_w13` 预测；每个日期的 50 条均与复算值相差相同常数 `+0.01575221647477`（冻结值相对复算值）。

## 可作出的结论

该常数不会改变任一最后五日的截面排序、top-N、买入候选或五策略冻结回放中的订单。因此，五策略的交易执行比较不需要因该问题重放。

不应将最后五日的冻结 Ridge `prediction_score` 作为精确的模型绝对收益预测、校准指标或跨窗口数值比较依据。若产品需要展示这些绝对分数，应另建版本化替代预测 artifact；不得修改现有冻结文件。
