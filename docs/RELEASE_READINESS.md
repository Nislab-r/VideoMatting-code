# 发布就绪说明（2026-09-09）

**在默认所有登记人物资产可用的前提下，本发布包支持初始化全量计划、显式启用全部人物、多卡调度、视频追溯及单条重渲染。** 不要求先重做所有人物预检；历史分类不阻止启用与 retry。此次只检查与更新发布文件，没有启动全量渲染。

| 核对项目 | 结果 |
|---|---|
| 人物入口 | 159 个唯一 human，无动物混入 |
| 素材归档 | 166/166 存在，SHA256 与完整 zstd 解压测试通过 |
| 清单文件 | 8680/8680 在原素材根目录存在，文件大小一致 |
| 背景引用 | 1908 条计划均可定位，756 个不同背景路径，无缺失引用 |
| 计划索引 | 1908 条唯一视频输出路径；1902 条动作尚待执行时分配 |
| 程序测试 | 23 项通过，包括人物全部启用、单/多卡相关队列检查、失败追溯记录 |
| 新队列模板 | 1908 条 waiting_release_review，未擅自设为运行中或已完成 |
| 全量执行验收 | 未执行；不能把打包完整和测试通过解释为1908条已完成 |

完整性是按当前打包清单核对。未再次遍历全部隐藏服装引用；人物资产可用是本次用户指定的默认执行前提，历史诊断报告仍保留供运行失败时定位。

当前正式 setting 为 `production_2k_round01_garment_v1_20260908`：1920×1080、30 fps、Cycles 64/16、threshold 0.01、OIDN、filter 0.75、subdivision cap 2。已同步在25人验证过的头部骨骼名称兼容修复。GPU并发容量尚未实测，默认每卡一个进程，多卡入口支持显式选择GPU。

## 全量启动顺序（命令未执行）

新环境按 FROM_ZERO.md 安装、完整解包并 prepare_workspace，配置 DATA_ROOT / VIDEO_ROOT / BLENDER_BIN 等变量；确认选择的是新建工作目录，随后：

```bash
python scripts/render_control.py check
python scripts/activate_subject.py --all
python scripts/render_multi_gpu.py --gpus 0 1 --slots 1 --start
```

GPU 0/1 仅是示例，必须换成实际空闲的卡。历史人物审计标签不阻止启用；实际文件缺失仍会报告。activate 只改变待启动状态，不会自行启动渲染。

本次检查原服务器约有 268.46 GiB 可用磁盘，超过当前 180 GiB 门槛；8 张 GPU 都在忙，所以没有可直接交给本启动器的空闲卡。不要为了启动而抢占其他任务，也不要把此服务器快照当作新机器的资源条件。

已有生产工作目录不会随发布包更新自动升级。必须先停止该工作目录的进程、备份并更新 tools，保留登记表、jobs.sqlite、配对与成品，再执行 check；缺少 render_record.py 的旧工作目录会被新版启动器拒绝。原队列仍为 done 6 / paused_motion_redesign 282 / waiting_adapter 1620，本次未修改。需要续跑旧队列时显式选择任务 retry，而不是拿新模板覆盖。

完整机器记录位于素材仓库 `release/release_readiness.json`。逐视频追溯与独立重渲染见 [VIDEO_RECORDS.md](VIDEO_RECORDS.md)。

## 素材仓库对齐更新

统一使用 [Renz-7/VideoMatting-Assets](https://huggingface.co/datasets/Renz-7/VideoMatting-Assets)，目前上传未完成，运行使用完整本地副本。新工作目录自动绑定；旧目录停机更新 tools 后执行 bind_asset_source.py。视频记录中保留人物/背景/动作的仓库相对来源，见 [ASSET_SOURCE.md](ASSET_SOURCE.md)。
