# 模型与系统评估规范

- **状态**：支持性细节；冲突时以 PRODUCT_REQUIREMENTS.md 和 QUALITY_PLAN.md 为准。

## 1. 原则

- 评估窄范围成功和拒绝能力；
- 角色/原图/创作者/近重复 family 跨 split 隔离；
- 锁定验收标注对开发者隐藏；
- 每个结果带样本数、区间、数据/模型/配置/硬件身份和切片；
- 客户内容不进入训练、校准、评估、人工质量审核或营销；
- 学习指标可分流，未证明相关性前人工评分负责感知门。

## 2. 数据组合与规模

实际规模由 DATA_ACQUISITION_AND_RIGHTS_PLAN.md 的预算和决策需要驱动，不把大规模愿望写成已承诺资源。

- D0：合成契约/几何/攻击集；
- D1：创作者委托、带真实分层和明确用途；
- D2：从权利明确分层图构建真实 motion-reveal 补全对；
- D3：适用/不适用和多标签原因；
- D4：绑定/参数轨迹及人工动画质量；
- D5：可再分发性能/可靠性集。

每项有 dataset card、asset ledger、allowed-use tags、source digest、annotation version、duplicate cluster 和 takedown。

## 3. 标注

记录适用性、可见语义蒙版、ignore/uncertain、角色左右、图层实例/顺序、关键点/可见性、仅有真实来源时的隐藏像素，以及透明材质/共享线稿/非标准结构。验证/测试双标，重大分歧裁决并保留。

## 4. 组件指标

### 适用性

逐原因 precision/recall、eligible false-block、ineligible recall、ECE/Brier/risk-coverage 和姿态/裁切/分辨率/风格/发饰/背景切片。

### 拆层/关键点

per-class/macro IoU/Dice、1024 归一化 1/2/4px boundary F-score、required-part presence、split/merge、左右、occlusion edge、landmark normalized error 和中性原作区域重建。

### 补全

有真实隐藏 ground truth 时报告 L1/PSNR/SSIM 及经批准感知指标；所有输出报告边界色/梯度、线条延续、语义泄漏、透明针孔和原作像素修改。自然未知区域由盲评判断 plausibility/style/semantic/motion suitability，不把 PSNR 当艺术正确性。

### 网格/绑定/渲染

有限/索引/拓扑、deformed signed area/foldover、alpha coverage、方向/单调性、seam/crack/overlap、安全范围、参数覆盖、两实现渲染差异。物理不在 v0.2 P0，除非后续 ADR 及样例加入。

### 端到端

首次成功、拒绝/review/fallback、修正成功、取消/恢复、双导出、active review time、stage p50/p95、RAM/VRAM、frame time、摄像头本地性和删除完整性。

## 5. 校准与选择性自动化

在独立 calibration split 选择 auto/review/block 阈值，基于 risk-coverage 与错误代价而非圆整数。评估 style/hardware/precision shift。模型升级不得以增加 auto coverage 为由提高关键错误风险。`unavailable` 不是 pass。

## 6. 实验身份

每个实验记录：commit、lock/container digest、模型结构/配置/权重摘要、精确数据 manifest/split、preprocess/augmentation/seed、硬件/驱动/provider/precision/determinism、命令、切片、资源和可展示资产权利。

## 7. 回归

候选在以下情况失败：任一硬不变量；总体 Gate 不达标；预注册关键切片无批准的显著退化；p95/runtime/VRAM 超过批准回归上限；确定性或格式语义无版本变化；缺模型/数据/许可记录。

对比使用 paired examples 和不确定性。Golden 容差不能隐藏裂缝、缺层、符号反向或原作像素改写。

## 8. 人工协议

Gate 4 至少两名训练评审；必要时第三人裁决。盲化版本，随机顺序，查看中性/叠加/标准动作，使用 anchored 1–5 和 binary usable/severity。插入重复项测一致性；报告分布、agreement、切片和失败分类。

## 9. 失败分类

运营 cause 与质量 finding 分轴。稳定顶层质量域：`INPUT`、`PARSE`、`INSTANCE`、`DEPTH`、`MATTE`、`INPAINT`、`COMPOSITE`、`MESH`、`RIG`、`TRACKING`、`RENDER`、`EXPORT`、`RESOURCE`、`PRIVACY`、`PACKAGE`、`LICENSE`。每项映射具体 code、severity、entity、evidence 和 fallback。
