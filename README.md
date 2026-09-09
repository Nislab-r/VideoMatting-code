# VideoMatting

**人物专用的合成视频抠像渲染代码。** 输入为人物 Blend、动作参考及背景视频；不处理动物，不包含源素材或生成的视频数据集。人物、动作与背景统一来自 **[Renz-7/VideoMatting-Assets](https://huggingface.co/datasets/Renz-7/VideoMatting-Assets)**。

完整人物清单有 **159 个独立人物入口、130 个依赖包**；这不代表 159 个人物均已完成渲染适配。

## 素材来源必须对齐

HF 仓库对应服务器目录 `/nvmedata/workspace2/users/rzc/datasets/video_matting/RenderMatte-4K-Motion/releases/VideoMatting-public/VideoMatting-Assets`。`ASSETS_DIR` 指向该完整目录，`VIDEO_ROOT` 指向其解包后的素材根目录。准备和渲染都会校验仓库清单及输入文件哈希；视频记录保留仓库、素材包和相对路径。详见 [下载、路径绑定和版本记录](docs/ASSET_SOURCE.md)。

## 默认执行规则与视频追溯

默认所有 159 个登记人物资产可用。历史人物预检仅供定位问题，不作为启动或重试门槛，也不要求先重新审计全部人物。新建队列保持待启动状态，可用 `python scripts/activate_subject.py --all` 启用全部人物，再按多卡手册显式启动；本次打包没有启动任何渲染。

每条实际任务仍先做当前已实现的完整低分辨率任务预检，通过后直接生成并保留正式成品，不额外做一轮 2K 全量预演。低分辨率预检目前仍为完整帧序列，尚未实现自动关键帧抽检；不要把这一项说成已经改为稀疏帧。

每个视频新增 **render_record.json**，记录人物、背景、实际动作、截取时间、输出名称、版本与失败信息。汇总索引及保留原片的单条重渲染命令见 [视频素材记录与重渲染](docs/VIDEO_RECORDS.md)。素材仓库另提供 metadata/render_video_plan.jsonl，列出全部 1908 条计划，未分配动作不会伪造。

本版合入已在 25 人上验证的头部骨骼名称兼容修复；画质 setting 保持下面的 2K 配置。资产默认可用是一项执行前提，不等于每条任务已经渲染成功，运行错误仍如实保留。

## 新环境从 0 开始

**首次执行请按 [从零安装与首个样例](docs/FROM_ZERO.md) 操作**：安装系统依赖和固定版本 Blender → 检查 GPU → 从素材包解出最小样例 → 渲染人物、alpha 和背景合成图 → 完整解包并创建新队列 → 显式启用人物并开始视频渲染。无需旧服务器或历史 jobs.sqlite。

完整素材文件夹是必需输入；本地素材索引不能替代源归档。最小 demo 与全量工作目录分开创建。

## Agent 从这里开始

**先读 [接手与续跑手册](docs/AGENT_RUNBOOK.md)，再执行操作。** 仓库根目录的 AGENTS.md 提供接手约束。新建工作目录与已有队列续跑必须分开处理：素材仓库的队列模板没有原服务器历史进度。

已配置 DATA_ROOT、VIDEO_ROOT、BLENDER_BIN 后，先运行 `python scripts/render_control.py status` 和 `python scripts/render_control.py check`；有待执行任务且无活跃工作进程时，用 `python scripts/render_control.py start` 续跑。暂停任务需要先修复，再按手册明确选择 ID 重试，不能批量解除质量阻断。

## 阅读入口与实现范围


| 能力            | 当前实现                                   |
| ------------- | -------------------------------------- |
| 单人物 + 背景视频    | 已实现，单条任务只处理一个合成人物                      |
| 多个人物依次批量渲染    | 已实现，159 人的 1,908 条独立计划；默认资产可用；运行失败单独记录 |
| 多人物在同一镜头中出现   | **尚未实现**，没有跨人物遮挡、碰撞或实例 alpha 输出        |
| 多 GPU / 每卡多任务 | 已提供显式调度；每卡并发需实测，当前仅完成队列与配置测试           |


- [多卡调度与每卡并发验证](docs/MULTI_GPU.md)
- [渲染流程、批量操作与多人同镜头设计边界](docs/RENDER_PIPELINE.md)
- [人物/背景完整性审计与缺失贴图入口清单](docs/ASSET_COMPLETENESS.md)
- [四轮外观、耗时和已知质量限制](docs/RENDER_VALIDATION.md)



## 当前渲染策略

以 `tools/production_quality/setting.json` 为唯一配置依据，版本 `production_2k_round01_garment_v1_20260908`。


| 项目                  | 当前值                              |
| ------------------- | -------------------------------- |
| 最终分辨率 / 帧率          | 1920×1080 / 30fps                |
| Cycles              | OptiX，CUDA 回退                    |
| 最大 / 最小采样           | 64 / 16                          |
| 自适应阈值               | 0.01                             |
| 去噪                  | OpenImageDenoise，开启              |
| Pixel filter / 细分上限 | 0.75px / 2                       |
| Motion blur         | 关闭                               |
| 前景与监督 alpha         | 临时 RGBA16 PNG；持久保存原生 16-bit mask |
| 合成帧                 | 2K JPEG，4:4:4，FFmpeg qscale 3    |
| 视频预览                | 960×540 H.264，CRF 24             |
| 调度                  | 可指定多 GPU 和每卡槽位；同人物串行，按 GPU/槽位加锁  |


四轮比较后保留第一轮外观策略：后续轮次没有表现出足够明确的整体收益。采样提升不等于模型发丝几何更细；粗发束、侧后方偏暗等限制见 [渲染验证](docs/RENDER_VALIDATION.md)。

## 人物和动作

人物清单只包含 `asset_kind=human` 的 159 项，运行入口再次验证人物 ID 和资产路径。源目录中的 36 个动物条目没有进入本项目清单。

全量计划为每人物 12 个动作，共 1,908 项、399,900 帧。每人物 6 项全身、3 项半身、3 项头发近景；halfbody 当前实际上较接近膝上构图。计划角度为 −30°、−15°、0°、15°、30°循环。更多机位可用于独立诊断，但不表示通过全部生产验收。

动作使用参考库的时序与运动特征，重新构造目标人物的原生安全动作，不直接把源骨骼旋转应用到目标人物。新分配默认使用 `expanded-safe-presets-v4-expanded` 目录，与本次选定的 demo 策略一致。HF 中保留动作特征、目录和配对元数据，运行脚本重建绝对路径。

## 衣物和完整性检查

预览和最终渲染都执行完整时序的裙骨适配与衣物/腿部面交叉检查。骨骼语义缺失、有约束或驱动、未知衣物、交叉、报告缺失、帧数不完整等不能直接通过。该模块不是任意衣物的物理仿真器，也不能证明没有一切穿模；当前严格模式可能拦截隐藏的内部交叠。无可识别裙骨的人物也会进入待适配状态。

同时保留动作幅度、末段稳定、肢体长度和可达性、几何、头发、构图、PNG 格式、采样和最终视频检查。具体程序位于 `tools/procedural_reference_production.py`、`tools/procedural_render_worker.py` 和 `tools/production_quality/`。

## 素材现状

本次打包 159 个人物入口及相关依赖、2,076 个可用动作参考。当前全量配对使用 756 个现存背景。原先缺失的 44 个旧城市背景所涉及的 85 条任务，已在 `background_replacement_v2` 中使用现有背景重新匹配；其余 1,823 条配对不变。保持 train/test 划分，重新检查构图、场景条件、时长，并验证所选片段可完整解码。当前背景缺失为 0；历史缺失清单与旧配对保存在 HF 文件夹 `release/history/before_background_replacement_v2/`，逐任务替换记录位于 `release/background_replacement_changes.json`。这不表示人物贴图及适配问题已经解决。

人物原预检包含 24 个 supported_v6、84 个 provisional_direct、51 个 unsupported。原预检中 8 项记录了活动贴图缺失。全部图片数据块检查发现 22 个人物共 304 个未解析图片数据块（包含未启用服装），另有 1 个 Blend 的形态键加载警告；这与活动贴图预检的统计口径不同。检查发现的 3,704 个现存外部依赖均已打包。130 个依赖包内共有 235 个 Blend 文件，包含人物的额外版本；人物数量按 159 个唯一清单入口统计。

## 安装和准备

经过本项目验证的环境是 Linux、Blender 5.0.1、NVIDIA GPU、FFmpeg/ffprobe 和 zstd。宿主 Python 至少 3.10，核心宿主工具只需标准库；bpy/mathutils 由 Blender 提供。不要把 Blender、GPU 驱动、模型权重或凭据提交到代码仓库。可选场景 VLM 默认关闭。

先将 HF 文件夹中的源归档解包到新的空目录，磁盘需容纳清单中的未压缩体积：

```bash
python scripts/unpack_assets.py --assets /path/to/VideoMatting-Assets --video-root /path/to/video
python scripts/prepare_workspace.py --assets /path/to/VideoMatting-Assets --video-root /path/to/video --workspace /path/to/VideoMatting-workspace
```

准备程序验证 159 项人物，复制代码，恢复动作特征和数据库中的路径，并创建 1,908 项计划。默认状态全部为 `waiting_release_review`，**不会启动渲染或沿用旧 DONE 记录**。已有工作目录和素材元数据不会被覆盖。

按 `config/env.example` 设置 `VIDEO_ROOT`、`DATA_ROOT` 和 `BLENDER_BIN` 等环境变量。先运行随素材附带的单帧标准入口样例：

```bash
python scripts/run_demo.py
```

该样例仍生成并检查完整 120 帧动作，输出第 1 帧透明前景、16 位 alpha 和包内背景合成图。它不旁路衣物检查，也不改动正式计划；不会生成完整合成视频，也不能代替工作进程的端到端验证。

确认某个人物适配后，可显式启用该人物的计划。结构上不支持的资产不能直接启用；缺失背景的任务保持阻塞：

```bash
python scripts/activate_subject.py --subject-id '<human-subject-id>'
python "$DATA_ROOT/tools/procedural_render_worker.py" --worker-id human-0
python scripts/check_progress.py
```

多个已审阅人物可以分别调用启用脚本，然后运行一个工作进程依次处理。默认磁盘可用空间门槛为 180 GiB，低于门槛时每 300 秒复查；可通过 RENDER_MIN_FREE_GB 配置。详细队列行为见流程文档。

启用不是合格保证；实际预览和最终检查仍可暂停任务。对源人物的原始 `.blend` 不保存修改。

## 输出和版本

输出目录由 setting.json 指定为 `composites_2k_round01_garment_v1`，下设 train/test 和 sample ID；每个样例包含 composite、mask、video、metadata 和 render report。完整检查通过后才写 DONE，随后清理临时前景。诊断图和未通过样例不得混入合格产出。

HF 的 `docs/RENDER_SETTINGS.md` 与本 README 同步，文档链接按所在目录调整；`release/code_release.json` 分别记录两个文件的 SHA-256 和代码清单指纹。尚未创建 GitHub commit 或 release tag，不使用虚构的提交号。恢复旧版本必须同时匹配代码和 setting.json；旧策略缺少新增衣物检查。

资产再分发许可没有从文件名推断；详见 [许可证说明](LICENSING.md)。本次仅整理文件夹，不进行发布。

历史诊断（不作为启用门槛）：[159 人实际检查结果](docs/SUBJECT_PREFLIGHT_RESULTS.md)：1 人可进入任务预检、98 人缺少控制器、11 人活动贴图异常、48 人动作/衣物待修复、1 人未找到可见绑定网格。原历史分类不变，未开始全量渲染。

发布目录核验结果见 [发布就绪说明](docs/RELEASE_READINESS.md)。