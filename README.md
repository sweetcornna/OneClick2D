# OneClick2D

> **状态：项目立项 / 可行性验证，尚未形成可发布产品。**

OneClick2D 是暂定的内部项目代号。项目探索把一张经过授权、接近正面的二次元半身立绘，自动转换为可检查、可有限修正、具有基础参数化运动能力的 2D 角色初稿。

## 一期目标

在明确限定的输入范围内完成：

1. 文件安全检查和角色适用性验证；
2. 语义拆层及有限的遮挡区域补全；
3. 网格、最小参数集和安全运动范围生成；
4. 浏览器手动参数预览；
5. 经独立验证的 `.oc2d` 可编辑项目及分层 PSD 导出；
6. 在另行验证后，提供仅由应用在浏览器本地处理的摄像头预览。

“一键”仅指用户执行一次生成操作，不代表无需检查、无需修正，也不承诺达到专业建模师成品水平。

## 明确边界

- 一期**不生成、解析、检查、逆向或承诺兼容** Live2D Cubism `.moc3`；
- Live2D/Cubism 是第三方产品及商标，本项目不暗示隶属、认可或合作；
- 一期不做全身、侧脸、多人、真人照片、重度遮挡和复杂团队协作；
- 用户上传内容及衍生物不用于训练、微调、校准、评估、人工质量审核、演示或营销；
- 产品代码不提供接收摄像头帧、裁剪、关键点、嵌入、校准样本或表情信号的服务端接口，也不主动传输或持久化这些数据；
- `.oc2d` 是项目自有的实验性格式；分层 PSD 只承载栅格图层，不承载绑定语义。

## 当前阶段

项目必须先通过 [Gate F 可行性预研](docs/FEASIBILITY_SPIKE_PLAN.md)，证明“扁平立绘 → 自动可动初稿”的核心假设，然后才进入生产化建设。

文档入口见 [docs/index.md](docs/index.md)。开发和评审要求见 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [开发规范](docs/DEVELOPMENT_STANDARDS.md)。

## 当前检查与预研烟雾测试

```bash
python scripts/validate_docs.py
python -m unittest discover -s tests -p "test_*.py"
python -m spikes.gate_f_runner smoke --run-id run.local-smoke
```

前两项是立项文档 lint 和单元测试；Pillow 相关测试仅在依赖存在时运行。第三项仅验证可丢弃的标准库本地 Gate F 编排骨架能以不可变输入、确定性种子和 attempt 输出生成 typed manifest；结果写入被 Git 忽略的 `workspaces/gate-f-spike/`。另有固定 Pillow 12.1.0 的真实 raster normalization Adapter，但它是按显式 `raster` 命令和 run spec 运行的非计分 ingest preflight，不改变当前固定 smoke 命令或生产技术栈。

这些命令不证明 JSON Schema/package conformance、安全隔离、模型质量、拆层/补全/绑定、PSD 互操作或 Gate F 可行性。

## 许可

仓库代码、模型、数据、素材和贡献许可尚未决策。在 [D-002](docs/OPEN_DECISIONS.md) 关闭前，本项目按私有、不可分发处理。
