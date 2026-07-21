# 分层 PSD 导出配置

- **状态**：Gate F/Gate 1 候选
- **边界**：栅格交换投影，不承载 CIR 绑定、物理或跟踪语义。

## 1. 基线

- PSD，不是 PSB；
- 8-bit/channel RGB；
- 嵌入或无歧义指定 sRGB；
- 与 CIR 完全相同的画布、方向和透明度；
- 普通栅格层、normal blend；
- 稳定名称，角色解剖学左右明确；
- 不支持能力必须预检阻止，禁止静默压平。

## 2. 规范面板顺序

以下顺序是 Photoshop 面板**从上到下**：

```text
OneClick2D — Read Me（隐藏）
最前方语义组/图层
  可见部件
  Generated Fill — 同一部件（紧邻可见层下方）
……逐步向后……
Background
Source Reference（隐藏、锁定，最底部）
```

合成器绘制顺序与面板顺序相反。每个生成填补层必须紧邻其所属可见层下方，不能集中放在一个与部件脱离的总组中。

具体语义从前到后由经过验证的 CIR draw order 决定；不可用固定示意列表覆盖项目顺序。

## 3. 明确排除

一期不承诺 PSB、CMYK/Lab/indexed、16/32-bit、矢量、可编辑文本、smart object、adjustment、effect、linked asset、timeline、外部内容、任意 blend mode、PSD 语义回导、绑定/变形器/物理/跟踪或第三方运行时数据。

## 4. 预检

写入前计算：画布/像素、色彩配置、层/组数、预计解码字节和文件大小、库/编辑器限制、不支持特性。保守阻止阈值必须低于解析器硬上限，并写入报告。

## 5. 独立验证

独立于写入路径的读取器必须验证：

1. signature/version/bit depth/color mode/profile；
2. 画布精确一致；
3. 图层存在、唯一、面板顺序、可见性、opacity、blend 和 Unicode 名称；
4. bounds/offset/alpha；
5. 每个 generated fill 位于所属可见层正下方；
6. source reference 位于最底部且隐藏/锁定（能力支持时）；
7. 按反向面板顺序合成与 CIR 中性结果在全局和局部容差内一致；
8. 无修复警告、脚本、外链、秘密、路径或无关元数据。

失败导出不得提供下载。

## 6. 合成 Golden

必须有合成非对称测试图，证明：

- 面板方向与 compositor 方向；
- 多层 alpha；
- 紧边界层 offset；
- visible/hidden；
- clipping（若支持）；
- Unicode、重复显示名和长名称；
- 透明边及 soft hair alpha；
- 目标 Photoshop/Krita 的打开、编辑、保存行为。

## 7. 编辑器和许可门

Gate F/Gate 1 选择写入库、独立读取器及精确 Photoshop/Krita 版本，并记录代码/二进制/商用/托管/再分发条款、席位/机器/自动化测试权利、Unicode/色彩/限制、维护状态和替换路径。

双输出是当前章程要求。PSD 失败时必须暂停并正式重新立项，不能把 `.oc2d` 单输出作为静默降级成功。
