# Agent 接手、执行与续跑手册

全新环境先执行 [从零安装与首个样例](FROM_ZERO.md)，不要把原服务器交接路径当作默认依赖。

## 先确定是在新建还是续跑

- **新机器、新实验**：按 README 解包并运行 prepare_workspace.py，得到新的 DATA_ROOT。模板含 1,908 条 waiting_release_review 任务，没有原服务器的完成进度。
- **已有实验续跑**：直接使用已有 DATA_ROOT、VIDEO_ROOT、jobs.sqlite、tools、reports/pairing_reference 和输出目录。不要再次初始化，不要拿模板覆盖现有数据库。续跑依赖已有输出，代码仓库和素材仓库本身不包含生成的全部成品。
- **换机器迁移已有进度**：除源素材外，还要迁移运行工作目录、数据库、报告、完成标记及成品，并重建嵌套 JSON 和数据库中的绝对路径。当前 prepare_workspace.py 只支持新工作目录，没有自动迁移活动进度的功能；不得声称仅下载两个仓库就恢复了远端历史任务。

路径由用户或环境提供；不要猜测账号、服务器地址、密钥或工作目录。原服务器的精确路径见随任务提供的本地交接说明，不写入公开代码。

## 最短执行步骤

以下命令在 VideoMatting 代码目录执行。编辑 config/env.example 的三个绝对路径后保存为工作目录外的本地环境文件并加载，不把本机路径或凭据提交到仓库。

```bash
source /absolute/path/to/local-render.env
python scripts/render_control.py status
python scripts/render_control.py check
```

check 验证工作目录关键文件、Blender/FFmpeg 与磁盘门槛；不是所有人物的渲染验收。确认是用户指定的工作目录和版本。检查 tools/production_quality/setting.json 应为选定的 2K 策略，查看配对版本及上一次错误。

如果 pending 大于 0，且没有正在工作的进程：

```bash
python scripts/render_control.py start
python scripts/render_control.py status
```

start 启动脱离终端的单工作进程，日志为 DATA_ROOT/logs/render_control.log。同目录的 render_control 启动器有文件锁；仍应检查是否有人直接启动了旧工作进程。正常 shell 断开不终止该进程。前台执行可用 run 代替 start。

已有模板中的待审核人物，按 README 执行 activate_subject.py --all 或选择单个人物。仅调用 start 不会启用任何 waiting/paused/failed 任务。默认磁盘门槛是 180 GiB；空间不足应检查储存规划，不能为了让进程启动就盲目降低门槛。

## 状态决定下一步

| 状态 | 含义 | 下一步 |
|---|---|---|
| waiting_release_review | 新发布模板尚未审阅 | 查看人物预检和样例，使用 activate_subject.py 启用登记人物（可使用 --all） |
| pending | 可以被领取 | start 继续队列 |
| running | 已领取 | 先检查 pid、日志和报告；活进程存在时不要重复启动 |
| done | 已完成历史任务 | 保留成品和 DONE；不要改回 pending 来覆盖 |
| waiting_adapter | 原人物适配尚未准备好 | 实现/验证适配，不是文件缺失的同义词 |
| paused_motion_redesign | 动作方案被暂停 | 修复和验证动作方案后，仅重排选定任务 |
| failed_prepass / paused_subject_qc | 预检失败或同人物其他任务被连带暂停 | 查错误和衣物/动作报告，修复原因并验证；不得关闭检查强行继续 |
| failed | 运行或最终检查失败 | 查日志、资源、输出格式等原因，修复后重试 |
| waiting_missing_background | 背景路径不成立 | 修复配对/路径后确认可解码，再重试 |

原服务器的 24 个 supported_v6 人物对应 288 条任务；此前快照中 6 条 done、282 条 paused_motion_redesign。另 135 个人物对应 1,620 条 waiting_adapter。这是交接时历史状态说明，实时状态必须重新查询；不要把旧 done 当作全部通过最新衣物版本的证明。

## 修复后的精确重试

先在隔离验证目录重现问题并验证修复，记录问题原因、代码/设置版本和报告。原分类不是通过编辑标签即可升级的“开关”。以下命令只用于原因已经解决的明确任务：

```bash
python scripts/render_control.py retry --job-ids 123 124 --resolved 'Describe the verified fix and report location here'
python scripts/render_control.py start
```

123/124 只是示例，必须替换为实际 ID。retry 检查原状态、人物登记、资产与背景存在、没有 DONE，也不会重排正在运行的队列；修改前在 reports/resume_history 中备份数据库并记录说明。支持的重试状态见上表，waiting_release_review 应使用启用脚本，done 永不自动重试。

保留 attempts 与事件历史。失败任务重新生成整个片段，临时帧会重建；当前没有逐帧断点续渲。只有修复不改变既有成品契约时才能原目录重试；改变分辨率、采样、配对或算法应使用新的实验目录保留旧结果。

工作进程会将 /proc 下已不存在的 running PID 对应任务恢复为 pending。PID 复用、跨主机迁移等情况不能只凭 PID 判断，需人工核对进程和队列；不要删除活动任务的文件。

## 如何判断执行完成

不能以进程退出或 pending=0 作为成功标准：还可能全部被暂停。需要核对每种状态、预期完成数量、DONE 与成品目录，以及实际 render report、衣物检查、合成帧、16 位 mask 和视频帧数。测试目录的单帧 smoke 不是全量成品。

每次接手结束记录：工作目录、设置 ID、配对版本、完成/失败/暂停数量、正在运行的 PID、已改文件、验证报告、下一项阻塞及其处理条件。不得把尚未适配的 135 人说成可以立即开始全量渲染。


## 多卡接手

需要多张卡时使用 [多卡调度说明](MULTI_GPU.md) 中的 render_multi_gpu.py。render_control.py start 仍是单工作进程入口，status/retry 可用于同一个多卡队列。多卡启动器要求工作目录中的队列代码已升级，拒绝旧串行版本。

## 本版强制预检补充

执行前遵循 [人物与任务预检](PREFLIGHT.md)：人物级审计现为可选诊断，默认登记资产可用；生产执行和重试始终由 worker 做任务级完整预检。旧工作目录缺少任务预检 v1 时启动器拒绝运行，需先停机备份并更新 tools，保留原队列、登记表和结果。人物级通过不得替代任务级通过；历史审计标签不再阻止显式启用或重试。

## 2026-09-09 默认策略更新

默认全部登记人物资产可用。本文历史预检统计不再作为启用门槛，无需先跑完人物审计；实际任务失败按报告定位。每段输出新增 render_record.json，索引与独立重渲染见 [视频记录说明](VIDEO_RECORDS.md)。此规则优先于文中历史“只启用 supported_v6”的限制。

## 素材仓库对齐更新

统一使用 [Renz-7/VideoMatting-Assets](https://huggingface.co/datasets/Renz-7/VideoMatting-Assets)，目前上传未完成，运行使用完整本地副本。新工作目录自动绑定；旧目录停机更新 tools 后执行 bind_asset_source.py。视频记录中保留人物/背景/动作的仓库相对来源，见 [ASSET_SOURCE.md](ASSET_SOURCE.md)。
