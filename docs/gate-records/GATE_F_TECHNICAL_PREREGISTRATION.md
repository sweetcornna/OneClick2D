# Gate F 技术预注册决策

- **版本**：0.1
- **日期**：2026-07-22
- **状态**：D-004 技术范围已决策；尚未激活
- **适用范围**：20 项 Gate F 自动路径与对照实验
- **激活条件**：Gate 0 具名签署并以不可变 commit/tree 绑定本文件；D-003 与 D-009 关闭
- **相关**：D-003、D-004、D-009、`FEASIBILITY_SPIKE_PLAN.md`、`QUALITY_PLAN.md`

本记录冻结技术协议，关闭 D-004 中“假设、切片、对照、kill criteria、评分能力”的选择空间。它不表示 Gate 0 PASS、Gate F 已开始或核心假设已证明。当前没有具名签署、20 项资产台账、批准预算或 PSD 互操作证据；因此协议处于**已决策但未激活**状态。激活时必须记录 repository commit/tree SHA，不能覆写本记录来适配已看到的结果。

## 1. 研究假设和实验单位

主要假设：在锁定的 20 个权利明确、带真实分层参考的素材上，完全自动候选路径产生的受限运动初稿，在盲化成对比较中以预注册幅度优于固定 simple-cutout comparator，并同时达到独立的 `F-USABLE`、语义槽位、原作保护、几何、身份和 PSD 硬门。

实验单位是锁定资产，不是 frame、图层、参数或重试。20 项全部进入分母；同一角色、创作者或近重复家族不得作为多个独立单位跨 split。主路径只接收扁平源图和统一配置。人工蒙版、锚点、网格、绑定、逐项参数选择或挑选有利 attempt 均禁止进入计分结果。

每个单位只允许一个预先指定的计分 attempt。基础设施故障可在揭盲前按同一不可变代码、配置、模型和 seed 重放；必须保留失败 attempt 并报告重放原因。质量失败、block、timeout、OOM、无效 geometry、缺失输出或 export failure 不得通过重跑改写为成功。

## 2. 冻结评分能力

Gate F 计分 mandatory profile 固定为：

| 参数 ID | 范围 | 必须展示的状态 |
|---|---|---|
| `head.yaw` | `[-15°, 15°]` | neutral、两端点、与 blink/mouth 的组合 |
| `head.pitch` | `[-10°, 10°]` | neutral、两端点、与 yaw 的组合 |
| `eye.left.open` | `[0, 1]` | 左眼独立闭合、双眼闭合、neutral |
| `eye.right.open` | `[0, 1]` | 右眼独立闭合、双眼闭合、neutral |
| `mouth.open` | `[0, 1]` | neutral、最大值、与 head pose 的组合 |

符号、单位、解剖学左右和插值以 `registries/parameters-v0.1.yaml` 为准。`eye.gaze.x/y`、`mouth.form`、`body.lean` 和 `breath` 只作探索性结果，不能补偿 mandatory 缺失，也不能改变 PASS。计分轨迹必须包含 neutral、每个端点、上述组合及同一个已记录 seed 生成的轨迹；candidate 与 comparator 消费完全相同的参数序列。

## 3. 固定 comparator

主对照为 `oc2d.spike.simple-cutout-comparator.v1`，必须在首个计分运行前实现、测试并以代码/config digest 锁定：

1. 与 candidate 消费同一份规范化 raster；不读取真实分层、oracle、逐资产标注或训练权重；
2. 使用单张原 raster 作为底板，并从下列归一化 source-pixel 矩形复制固定 patch，不做语义分割或隐藏区域补全：
   - head：`x=[0.20,0.80]`、`y=[0.05,0.60]`；
   - screen-left eye（角色右眼）：`x=[0.27,0.47]`、`y=[0.25,0.40]`；
   - screen-right eye（角色左眼）：`x=[0.53,0.73]`、`y=[0.25,0.40]`；
   - mouth：`x=[0.40,0.60]`、`y=[0.42,0.56]`；
3. patch 边界只能使用固定 2 source-pixel linear-alpha feather；不得按资产调节；
4. head yaw 将 head patch 以中心为 pivot 作端点 `±0.025 × width` 的 X 平移和端点 `±0.03` 的 X shear；head pitch 作端点 `±0.02 × height` 的 Y 平移；
5. eye open 围绕各自中心作 Y scale `0.15…1.0`；mouth open 作 Y scale `1.0…1.35`；底板不补洞、不擦除重复像素；
6. 使用与 candidate 相同的 canvas、采样、色彩/alpha 约定、frame 序列和 renderer；不输出 PSD，不参与 candidate 的 PSD 硬门。

该 comparator 故意代表无需语义拆层/补全的低复杂度剪纸，而不是专业人工绑定。实现与本描述不一致、逐资产调参或 comparator 运行缺失会使主比较无效，不能记作 candidate 胜利。

## 4. 主成对指标与优效规则

### 4.1 盲化判定

每个资产生成随机化 A/B 标识的同规格 contact sheet/sequence。评审只比较：身份和 neutral 保真、运动可读性、接缝/露洞/重复像素、方向和安全性；不得以 candidate 的图层数量、PSD 或实现身份判断优胜。

先由两名训练评审独立选择 `candidate win / tie / comparator win`。结论一致时直接采用；不一致时由第三名盲化评审裁决。三种选择各一票或仍无法形成多数时按 `tie`。揭盲映射、顺序 seed、原始评分和裁决全部保留。

### 4.2 数值规则

令 `W` 为 candidate wins、`L` 为 comparator wins、`T` 为 ties，且 `W + L + T = 20`。自动路径优于 comparator **仅当同时满足**：

1. `W - L >= 4`，即以全部 20 项为分母的净胜幅度至少 20 个百分点；
2. 在非 tie 的 discordant 项上，对 `H0: P(candidate win) <= 0.5` 做单侧 exact binomial test，`p < 0.05`；
3. 报告 `W/L/T`、`(W-L)/20`，以及 discordant win probability 的双侧 95% Clopper–Pearson 区间。

Tie 不从 20 项净胜幅度分母删除，只在 exact binomial test 中排除。探索性指标、平均 frame 分数、可选能力或事后切片不能推翻主规则。该规则不做多重比较修正，因为只有一个预注册 primary comparison。

### 4.3 missing 和失败

- candidate block、crash、timeout、OOM、无效或缺失可评分序列：记 `comparator win`，并在 `F-USABLE` 中记失败；
- comparator 因实现/编排故障缺失：该配对无效，实验在揭盲前修复并按固定身份重放；不得记 candidate win；
- 评审证据缺失且无法在揭盲前补齐：记 `tie`，保留在 20 项分母并单列；
- 两臂都产生有效但同样不可接受的视觉结果：允许评审判 tie；硬门仍独立失败。

## 5. 预注册切片

D-003 的 20 项台账必须在运行前为每项冻结以下标签和证据，不得查看结果后改类：

- hair complexity：`simple / medium`；
- face-or-neck-overlapping accessory：`absent / present`；
- semitransparent-or-soft edge：`absent / present`；
- non-character background：`transparent / flat / non-flat`；
- source alpha：`opaque / has-transparency`；
- creator family、character family 和 near-duplicate cluster。

每个预注册水平都报告 `F-USABLE`、W/L/T 和主要失败原因。任一 `n >= 3` 的水平若 `F-USABLE = 0`，Gate F 不得 PASS；`n < 3` 只作探索并明确样本不足。D-003 采购时至少保证 hair 的两个水平各 `n >= 5`，accessory-present、soft-edge-present 和 non-flat-background 各 `n >= 3`。同一资产可属于多个切片。

## 6. 联合 kill criteria

Gate F PASS 必须同时满足：

1. 本记录第 4 节的 comparator superiority；
2. 至少 `12/20` 达到 `F-USABLE`；
3. ontology 中 `applicability: required` 的槽位存在率按“已要求槽位实例数”为分母达到 `>=90%`；`LOW_CONFIDENCE`、missing、错误左右和错误实例数均不算存在；
4. 第 5 节所有 `n >= 3` 切片不为零成功；
5. feather tolerance 外可见原作像素修改为零；
6. 所有计分 geometry 通过 finite、index、topology 和全轨迹 foldover/seam 检查；
7. 身份改变的脸部补全零通过；
8. 每个 `F-USABLE` 项的 PSD 通过独立 reader 和至少一个 D-009 批准的目标编辑器验证；
9. 20 项全部报告 outcome、失败分类、运行时、RAM/VRAM、attempt 和不可变身份。

任一项失败时只能 `RECHARTER` 或 `STOP`；不得以总平均、人工修正或可选能力抵消。

## 7. 首个真实 Adapter 决策

首个真实实验 Adapter 固定为 `raster.normalize.pillow.v1`，contract 为 `oc2d.spike.raster-normalize.v1`，依赖 Pillow `12.1.0`。它只执行本地 PNG/JPEG 防御性解码与规范化：限制 decoder、结构校验后重开、完整 pixel decode、拒绝多 frame、应用 EXIF orientation、在可验证时把 embedded ICC 转换到 sRGB、输出 RGB/RGBA、剥离元数据并写固定参数 PNG 与有界报告。

该 Adapter 是 20 项两臂共享的**非计分 ingest preflight**。它可以证明锁定 profile 下的文件接收/规范化边界，不回答语义拆层、补全、绑定、渲染或 PSD 可行性，也不计入 `F-USABLE`、W/L/T 或 Gate F PASS。Pillow 准入只适用于 disposable spike，不决定生产 Python、图像库或部署栈。

## 8. 未关闭阻塞

- **D-003 保持 Open**：没有批准预算、20 项签署资产台账、模型/权重及数据权利结论、具名责任人；
- **D-009 保持 Open**：没有锁定 PSD writer/独立 reader/目标 Photoshop 或 Krita 版本、席位/机器/自动测试权利及 roundtrip 证据；
- **Gate 0 保持 Pending**：本记录尚未绑定不可变 tree，也没有产品、技术、ML/图形、隐私安全和法律/商务具名签署。

因此当前允许的工作仍仅限权利明确、无客户内容的本地可丢弃 spike。不得开始 20 项计分、邀请外部用户、接收真实用户上传或声明 Gate F 通过。

## 9. 变更与重开

激活后，能力、范围、comparator、主指标、margin、tie/missing、切片或 kill criteria 的任何变化都必须：停止当前实验；创建 superseding 决策；使用 untouched、group-disjoint 数据重新验证。修正文案错误不得改变数值语义。
