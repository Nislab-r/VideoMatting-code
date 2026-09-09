# VideoMatting preparation — 2026-09-08

- Separate the human-only code and source-asset repositories; exclude the 36 non-human inventory rows.
- Preserve the selected 2K first-round appearance policy and mandatory garment guard.
- Replace host-specific runtime paths with configuration; add an explicit human-registry gate.
- Default new JIT assignments to the expanded v4 catalog used by the current validated demos.
- Provide checked extraction, metadata relocation, reviewed-subject activation, and progress tools.
- Preserve 159 human entries, 130 source packages and the 1,908-job plan without reusing run logs or completion flags.
- Expose missing backgrounds and unresolved asset/license checks instead of concealing them.

## background_replacement_v2

将 44 个缺失背景涉及的 85 条任务重新匹配到现有背景，保留原配对和队列快照。保持其余 1,823 条配对、train/test 划分、渲染设置及队列状态；当前背景缺失为 0。

## multi_gpu_queue_v1

队列改为不同人物可并行、同人物串行，新增显式 GPU/槽位监督器和隔离并发基准。真实每卡最优并发尚未测量；没有启动正式渲染或修改旧生产工具。
