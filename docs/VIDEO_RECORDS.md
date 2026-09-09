# 视频素材记录与精确重渲染

每个新执行的视频任务生成 `输出根目录/<split>/<sample_id>/render_record.json`。不再仅凭一个相同的 preview 文件名定位任务；使用 split + sample_id + 相对视频路径作为唯一名称。

记录包括人物 ID/Blend 路径、背景路径和实际截取起点、动作源文件与源起始帧、动作段 ID、完整分配参数、实际生成后的动作参数、可见网格和头发变体、帧数、机位、完整 setting、代码逐文件 SHA256、三个主输入文件 SHA256、运行状态、失败原因及所有输出路径。贴图等完整依赖仍需保留在对应人物素材包；三个主输入的哈希不代表逐项校验了全部外部贴图。新工作目录另记录配套素材发布版本。

记录位于成品目录，失败时也会尽力写入；磁盘已满、进程被强杀等可能使状态停在 preflight，须结合日志和 DONE 判断。重试前旧记录保存在 record_history。completed 表示该次程序执行完成，不能代替人工画质复检。旧 DONE 输出不会自动补造本次记录。

## 汇总所有已执行视频

```bash
python scripts/build_render_index.py \
  --output-root "$DATA_ROOT/composites_2k_round01_garment_v1" \
  --index "$DATA_ROOT/render_video_index.jsonl"
```

每行对应一个视频，包含完整素材关系和 record_path；脚本重新构建索引，不由多卡进程竞争追加同一文件。可反复执行。尚未运行的模板任务见素材仓库 `metadata/render_video_plan.jsonl`：这是 1908 条计划索引，预期视频路径不是已生成结果；尚未分配的动作明确留空，执行时再记录实际分配，不能把占位动作当成真实动作。

正式输出为 2K 合成 JPEG 序列和 16 位 alpha；`video/preview_960x540.mp4` 是 960×540 检查视频，不是 2K MP4。原生前景 RGBA 是临时文件，成功后清理。

## 定位问题后单独重渲染

先根据索引找到该视频的 render_record.json。已完成但画面有问题的片段使用独立新输出目录，保留原片：

```bash
python scripts/rerender_sample.py \
  --record /data/run/output/train/SAMPLE/render_record.json \
  --output-root /data/rerenders/review-001 \
  --gpu 0
```

脚本复用已记录的动作、背景与任务参数，不重新随机分配、不修改 jobs.sqlite；先核对主输入哈希、代码和 setting。GPU 必须空闲。选择的输出根目录必须不存在，防止覆盖。

若修复代码后重渲染，增加 `--change-note '说明修复内容与验证依据'`，明确允许新代码/setting，并记录旧 record 的路径与哈希。素材主文件有变化则拒绝此精确重放；应创建新的任务版本记录变化，不能伪装成同输入复现。迁移机器后必须正确恢复记录中的路径及依赖；此脚本不自动改写旧机器路径。不同 GPU/运行库可能导致像素级差异，不承诺逐位相同。

尚未完成的失败任务也可按接手手册显式 retry 指定 job ID；旧 DONE 不会被重排。任何资产的历史审计标签都不再阻止启用或 retry，实际文件存在性、人物登记和任务运行检查仍保留。

## 素材仓库对齐更新

统一使用 [Renz-7/VideoMatting-Assets](https://huggingface.co/datasets/Renz-7/VideoMatting-Assets)，目前上传未完成，运行使用完整本地副本。新工作目录自动绑定；旧目录停机更新 tools 后执行 bind_asset_source.py。视频记录中保留人物/背景/动作的仓库相对来源，见 [ASSET_SOURCE.md](ASSET_SOURCE.md)。
