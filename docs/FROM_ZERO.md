# 在全新 Linux 环境从 0 开始

本手册不依赖旧服务器路径、旧 jobs.sqlite、旧渲染结果或先前会话。需要拿到完整的 VideoMatting 和 VideoMatting-Assets 两个文件夹；素材地址为 [Renz-7/VideoMatting-Assets](https://huggingface.co/datasets/Renz-7/VideoMatting-Assets)，当前上传尚未完成；先使用完整本地发布目录。上传完成后的下载和固定版本方式见 [素材来源说明](ASSET_SOURCE.md)。VideoMatting-Assets-Index 只是索引，不能代替完整素材文件夹。

## 1. 系统、GPU 与磁盘

以下安装命令面向 Ubuntu 22.04/24.04 x86_64。目标为 Python 3.10+、Blender 5.0.1、FFmpeg/ffprobe、zstd，以及能被 Cycles 识别的 NVIDIA GPU。驱动应由主机或云平台安装；容器需要可访问 GPU。先执行 nvidia-smi，若不能访问 GPU，应先修复主机驱动或容器配置。不要仅安装 Python CUDA 包来代替主机驱动。

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv ffmpeg zstd curl xz-utils ca-certificates libgl1 libegl1 libxrender1 libxi6 libxkbcommon0 libsm6 libxxf86vm1
nvidia-smi
```

无 sudo 权限时，请使用已经装有这些组件的环境或由管理员安装。RAM/VRAM 随人物复杂度变化，目前没有覆盖全部 159 人的最低容量测试，不能给出保证全库可用的最低显存数。

完整归档约 136.6 GiB，解包源文件约 175.1 GiB，另需元数据、渲染临时文件和输出。若全部放在同一磁盘，默认工作进程还要求至少 180 GiB 可用空间；加上早期估算约 105.5 GiB 的输出预算，建议开始前准备至少 650 GiB 可用空间并持续监测。该输出估算不是容量上限。最小 demo 只解出一个人物包及一个动作、一个背景文件，但仍需读取并校验它们所在的三个归档。

## 2. 固定 Blender 与宿主 Python

选择一个可写的安装目录。下面假设代码目录和素材目录已复制到 /data 下；将所有 /data 示例路径替换为本机真实的绝对路径。

```bash
mkdir -p /data/runtime
cd /data/runtime
curl -fLO https://download.blender.org/release/Blender5.0/blender-5.0.1-linux-x64.tar.xz
curl -fLO https://download.blender.org/release/Blender5.0/blender-5.0.1.sha256
sha256sum --check --ignore-missing blender-5.0.1.sha256
tar -xf blender-5.0.1-linux-x64.tar.xz
export BLENDER_BIN=/data/runtime/blender-5.0.1-linux-x64/blender

cd /data/VideoMatting
python3 -m venv /data/runtime/videomatting-venv
source /data/runtime/videomatting-venv/bin/activate
python -m pip install -r requirements.txt
export CUDA_VISIBLE_DEVICES=0
python scripts/check_environment.py
```

核心宿主脚本仅用标准库，requirements.txt 没有需要下载的 Python 依赖；bpy/mathutils 来自 Blender，不要另外安装 PyPI bpy。check_environment.py 使用出厂设置检查 Blender 精确版本和 OptiX/CUDA 设备，不依赖旧用户配置。

下载来源：[Blender 官方 5.0 发布目录](https://download.blender.org/release/Blender5.0/)。GPU 故障排查可参照 [Cycles GPU 渲染说明](https://docs.blender.org/manual/en/5.0/render/cycles/gpu_rendering.html)。

## 3. 先跑最小样例

在代码目录执行。目录必须是新的；重复试验请使用新的目录名，脚本不覆盖已存在的样例。

```bash
export ASSETS_DIR=/data/VideoMatting-Assets
export VIDEO_ROOT=/data/videomatting-demo-assets
export DATA_ROOT=/data/videomatting-demo-work
export CUDA_VISIBLE_DEVICES=0

python scripts/unpack_assets.py --assets "$ASSETS_DIR" --video-root "$VIDEO_ROOT" --profile demo
python scripts/prepare_demo.py --assets "$ASSETS_DIR" --video-root "$VIDEO_ROOT" --workspace "$DATA_ROOT"
python scripts/run_demo.py
```

此模式使用素材清单中的人物、动作和背景；解包前校验归档 SHA-256，解包后校验所选文件 SHA-256。它不创建生产队列，也不依赖其余 158 个人物或旧动作数据库。随包样例包含已准备的动作蓝图。

成功时，DATA_ROOT/smoke 下应有：

- rgba/00001.png：1920×1080、16 位 RGBA 人物前景。
- alpha.png：1920×1080、16 位灰度监督 alpha。
- composite.png：与包内背景合成的 2K 图。
- report.json 和衣物报告：来自标准渲染入口。
- demo_validation.json：status 为 passed，rendered_frames 为 1，authored_frames 为 120。

查看 DATA_ROOT/demo.log 排查错误。该样例执行完整 120 帧动作/衣物检查，只输出一张图，不是完整视频，也不能证明 159 人全部通过。应实际打开 composite.png 和 alpha.png，检查构图、贴图、发丝及透明边缘。

## 4. 创建全量工作目录

最小 demo 的目录不能直接当全库使用。用另外两个空目录完整解包并创建队列：

```bash
export VIDEO_ROOT=/data/videomatting-assets
export DATA_ROOT=/data/videomatting-work-v1
python scripts/unpack_assets.py --assets "$ASSETS_DIR" --video-root "$VIDEO_ROOT" --profile full
python scripts/prepare_workspace.py --assets "$ASSETS_DIR" --video-root "$VIDEO_ROOT" --workspace "$DATA_ROOT"

export MOTION_OBSERVATION_ROOT="$VIDEO_ROOT/motion_observation_v2"
export EXPANDED_PRESET_CATALOG="$MOTION_OBSERVATION_ROOT/expanded_motion_preset_catalog_v4_expanded.json.gz"
export EXPANDED_PRESET_CATALOG_VERSION=expanded-safe-presets-v4-expanded
export SCENE_PAIRING_REQUIRED=1
export SCENE_COMPOSITE_VLM_QC=0
export RENDER_MIN_FREE_GB=180
python scripts/render_control.py check
python scripts/render_control.py status
```

初始化成功应显示 159 人、1,908 条 waiting_release_review、背景缺失 0；没有 DONE 或运行中任务。这一步恢复新的动作元数据与路径，不沿用任何原服务器进度。将上述环境变量保存到工作目录外的本地环境文件，下次登录需重新加载宿主虚拟环境和这些变量。

## 5. 开始第一条完整生产任务并继续

查看已支持的人物 ID：

```bash
python - <<'PY'
import csv,os
from pathlib import Path
with (Path(os.environ['DATA_ROOT'])/'tools/asset_preflight_classified.tsv').open() as f:
    for row in csv.DictReader(f, delimiter='\t'):
        if row['status']=='supported_v6':
            print(row['subject_id'])
PY
```

选择已审阅人物的实际 ID，先启用一个人物，避免把未确认的全部素材投入生产：

```bash
python scripts/activate_subject.py --subject-id '<actual-reviewed-human-id>'
python scripts/render_control.py start
python scripts/render_control.py status
```

工作进程将依次执行该人物的 12 条任务，包括全帧预检、最终渲染、背景合成、mask 和视频验证。后台日志在 DATA_ROOT/logs/render_control.log，样例报告在 DATA_ROOT/composites_2k_round01_garment_v1 下。失败或被暂停时按 [续跑手册](AGENT_RUNBOOK.md) 修复后精确重试；不能为“从零跑通”关闭衣物检查或伪造适配支持。

确认首个人物的完整视频和监督数据后，可逐一启用其他已审阅人物，继续同一个队列。默认 84 个候选和 51 个不支持的入口仍需要适配；“从零启动流程”不等于“159 人无条件全自动通过”。

## 验证范围

从归档选择性解包、独立目录准备、清理旧环境变量后运行标准入口和单帧合成，是实际执行检查的范围。验证复用已有主机的 Linux/NVIDIA 驱动和 Blender 二进制，没有重装一台全新的操作系统。完整全库解包与 159 人全量渲染不属于最小样例验证；完整归档已做过校验，但人物适配问题仍存在。

本次隔离样例已通过：直接从三个归档提取所需文件，未使用旧素材软链接或旧队列；清理环境变量并使用 Blender 出厂设置，完整 120 帧衣物检查通过，生成单帧 2K 前景、16 位 alpha 和合成图，总耗时约 345 秒。此时间包含该次初始化和检查，不是全量平均用时。对应证据位于素材仓库 release/fresh_environment_demo_validation.json 和 release/fresh_environment_render_report.json。全量渲染未启动。


## 多张 GPU

新工作目录已包含多卡版队列工具。请按 [多卡调度与并发验证](MULTI_GPU.md) 选择空闲 GPU，并在独立基准中确定每卡槽位数量。不要因为存在多张卡就直接启动所有槽位；当前文档维护并未启动全量渲染。

## 本版强制预检补充

执行前遵循 [人物与任务预检](PREFLIGHT.md)：人物级审计现为可选诊断，默认登记资产可用；生产执行和重试始终由 worker 做任务级完整预检。旧工作目录缺少任务预检 v1 时启动器拒绝运行，需先停机备份并更新 tools，保留原队列、登记表和结果。人物级通过不得替代任务级通过；历史审计标签不再阻止显式启用或重试。

## 2026-09-09 默认策略更新

默认全部登记人物资产可用。本文历史预检统计不再作为启用门槛，无需先跑完人物审计；实际任务失败按报告定位。每段输出新增 render_record.json，索引与独立重渲染见 [视频记录说明](VIDEO_RECORDS.md)。此规则优先于文中历史“只启用 supported_v6”的限制。
