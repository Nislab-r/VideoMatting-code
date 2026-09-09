# 159 人物实际预检结果（2026-09-08）

本次完整逐人检查已完成。**仅 1 人通过这一套代表性人物检查，仍不是生产验收通过；不能直接宣称 159 人开箱即可批量渲染。** 其余问题包括动作适配、活动贴图、衣物识别/穿插和可见网格。

| 最终状态 | 人数 |
|---|---:|
| ready_for_task_preflight | 1 |
| needs_adapter | 98 |
| needs_asset_repair | 11 |
| needs_quality_review | 48 |
| inspection_failed | 1 |
| 合计 | 159 |

状态互斥但问题可重叠。`needs_quality_review` 的 48 人中，25 人在代表性动作生成时缺少 `ORG-spine.007`，实际仍需动作适配修复；另外 23 人进入了动作检查，但未通过衣物守卫。应按详细失败原因安排修复，不能只按标签理解为外观问题。

## 实测检查统计怎样读

- 159 人文件均打开：158 无加载警告，1 有 ShapeKey 加载警告。1 人随后找不到目标骨架的可见绑定网格，未能继续。加载成功不代表服装、绑定或渲染正常。
- 158 人完成控制器检查：49 人具备 10 个必要控制器，109 人不具备；控制器存在仍不足以证明全部内部骨骼链兼容。
- 147 人活动贴图检查通过，11 人失败，1 人未进入此阶段。这里检查当前可见服装，不覆盖所有隐藏服装组合；失败不一定是归档遗漏，也可能是源文件引用错误或素材本身缺失。
- 24 人成功生成 120 帧代表性动作，25 人生成失败；109 人因控制器/贴图条件不满足而跳过，1 人更早停止。
- 24 人完成衣物检查：1 通过，23 待复核。失败原因有 17 次下装覆盖无法分类、5 次命名服装缺少加权衣物骨骼、5 次检测到表面穿插、1 次特定裙骨缺少骨盆/大腿映射；原因会重叠，不能相加当人数。未知覆盖并不等于已证实穿模。
- 24 人的 5 帧几何运动检查通过；全部 120 帧四肢检查为 23 通过、1 失败。这些是各阶段统计，不是额外的“可渲染人物数”。
- 158 人各输出 3 张诊断图，共 474 张；唯一未出图者未找到可见绑定网格。诊断图为 CPU 320×180 / 2 samples，不能评估生产发丝细节，也不能证明所有肢体完整。

主审计用 4 个 CPU 进程、每进程 4 线程，耗时 912.3 秒（15.2 分钟）；修复 PNG 输出模式后另复查 2 人，用时 25.5 秒。159 个源入口的文件大小与修改时间保持一致。没有使用生产队列或开始全量渲染。

## 唯一可进入下一层检查的人物

`assets_human7_Storm_V1.3_5_ae41b8fd92fd`。通过只针对随仓库携带的一套动作、当前服装和当前检查范围；每个生产动作、机位仍需要独立任务预检。

## 活动贴图异常的 11 个入口

- `assets_human4/2/Primrose_OfficeLady_1.blend`
- `assets_human4/2/Primrose_OfficeLady_2.blend`
- `assets_human4/2/Primrose_OfficeLady.blend`
- `assets_human5/8655/[Odin Valhalla Rising] Sorceress - Magic Parade.blend`
- `assets_human6/Blender第一后裔芙蕾娜TFD Freyna高精度3D模型/The First Descendant Freyna.blend`
- `assets_human4/ElfPaladin__b3ffa1d4d9/ElfPaladin.blend`
- `assets_human4/ViSB__3319af8c22/ViSB_2.blend`
- `assets_human4/ViSB__3319af8c22/ViSB.blend`
- `assets_human4/ViSB__3319af8c22/ViSB_3.blend`
- `assets_human8/for young/Yor_Forger_2023-Rigg_Update2-003.blend`
- `assets_human6/赛车美女/Hayley.blend`

## 提前停止的入口

`Blender第一后裔芙蕾娜TFD Freyna高精度3D模型_The First Descendant Ultra Freyna V1.0_fe1c156c52dd`：目标骨架没有可见绑定网格，需要检查服装/集合可见性及绑定选择。没有把它当作检查通过。

## 诊断图复核与限制

全身缩略图复核发现：部分人物当前只显示头部/身体片段，部分有紫色缺贴图，另一些被异常包围盒或尺度压缩成极小人物；部分源人物自身朝向与世界坐标相机不同。这些现象已保留在逐人图中，需要进一步定位，不能仅凭低采样缩略图判断源模型永久损坏。没有对 474 张图做逐像素人工验收。

## 保存位置与版本

素材仓库 `audits/subject_preflight_v2/` 为最终汇总，含 summary.json、results.jsonl、逐人报告和三视图；`audits/subject_preflight_v1/` 保留首次完整审计。v2 复用了首次 157 人结果，仅替换修复视频/PNG输出模式后的 2 人复查；不宣称重新运行了所有 159 人。原始日志保留在私有运行工作目录，不随公开报告分发。

原历史 24/84/51 分类原样保留。这次历史 supported_v6 的 24 人中为 1 人通过人物级检查、23 人被衣物守卫阻断；原历史分类不能替代新报告。队列未自动启用、未改写任何人物支持标签。

后续执行见 [人物与任务预检](PREFLIGHT.md)。
