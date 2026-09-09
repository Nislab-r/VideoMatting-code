# 唯一素材仓库与路径对齐

人物、动作参考和背景的统一来源为 [Renz-7/VideoMatting-Assets](https://huggingface.co/datasets/Renz-7/VideoMatting-Assets)。用户确认该仓库对应完整服务器目录：

```text
/nvmedata/workspace2/users/rzc/datasets/video_matting/RenderMatte-4K-Motion/releases/VideoMatting-public/VideoMatting-Assets
```

当前远端上传尚未完成。现阶段使用上述完整本地目录，不根据远端未上传文件判断素材损坏，不覆盖源素材。远端 commit 尚未核验，不能声称远端与本地逐文件同步完成。

| 路径变量 | 用途 |
|---|---|
| ASSETS_DIR | HF 仓库完整下载/本地发布目录，包含 manifests、metadata、subjects/packages、backgrounds 等 |
| VIDEO_ROOT | 从该发布目录解包的实际输入文件根目录；Blender/FFmpeg 使用这里的文件 |
| DATA_ROOT | 渲染工作目录，保存队列、绑定信息、输出和追溯记录 |

不把 ASSETS_DIR 直接当成 VIDEO_ROOT；素材包须解包。可以复用已存在且内容符合清单的解包目录，不必重复复制素材。

## 上传完成后的下载

在已创建的 Python 环境中执行：

```bash
python -m pip install huggingface_hub
python scripts/download_assets.py --assets /data/VideoMatting-Assets --revision main
```

脚本先解析 main 的完整 commit，再按固定 commit 下载；中断后用同一命令复用记录的 commit。`.videomatting_download.json` 保存下载来源和版本，不保存 token。需要认证时使用 HF 官方登录或 HF_TOKEN 环境变量，不把凭据写进仓库。

此命令下载完整素材仓库。上传未完成时不要执行；不完整下载不会被当成可用数据集。下载后仍由 unpack_assets.py 核对归档哈希并解包。用新的目录下载新版本，不能混入其他版本。

下载 API 行为参见 [Hugging Face 官方文档](https://huggingface.co/docs/huggingface_hub/guides/download)。

## 解包、准备与运行

继续按 FROM_ZERO.md 的环境配置操作，然后：

```bash
python scripts/unpack_assets.py --assets "$ASSETS_DIR" --video-root "$VIDEO_ROOT"
python scripts/prepare_workspace.py --assets "$ASSETS_DIR" --video-root "$VIDEO_ROOT" --workspace "$DATA_ROOT"
```

config/asset_source.json 固定仓库身份与三个素材清单的 SHA256。解包/准备时验证清单；准备时把清单和绑定信息复制到 runtime/asset_source.json、runtime/asset_source_files.jsonl。demo 的 prepare_demo/run_demo 使用同样来源约束。

正式 worker 和 Blender 生成入口在运行前检查人物 Blend、动作源文件、背景视频：必须位于绑定的 VIDEO_ROOT、列于此仓库的 files.jsonl，并匹配源文件 SHA256。这样不会静默混用别处同名背景或人物。此项是来源一致性检查，不是人物质量预检；所有登记人物默认可用的规则不变。

旧工作目录必须先停机、更新 tools，再显式绑定。不会替换旧队列或成品：

```bash
python scripts/bind_asset_source.py --assets "$ASSETS_DIR" --video-root "$VIDEO_ROOT" --workspace "$DATA_ROOT"
python scripts/render_control.py check
```

已绑定的目录不会被此脚本覆盖。修改资产版本或迁移 VIDEO_ROOT 时使用新的工作目录，避免旧记录路径与新文件混合。

## 每个视频的来源记录

render_record.json 和最终 render_report.json 中的 dataset_source_refs 分别记录人物、动作与背景的 repo_id、repo_url、revision、archive_path、path_in_video_root、sha256。archive_path 是 HF 仓库中的压缩包相对路径，path_in_video_root 是包解开后的实际文件路径，二者不能混淆。

服务器本地完整发布目录的 revision 为 null、origin 为 local_release，使用清单哈希标识版本；通过下载脚本获得的副本记录真实 commit。原有素材包不改名、不搬动，此次不执行 HF 上传，也不启动渲染。
