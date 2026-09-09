# VideoMatting agent handoff

Read README.md; for a new environment follow docs/FROM_ZERO.md, then docs/AGENT_RUNBOOK.md before changing or starting work.

Use the existing workspace when continuing a run. Do not run prepare_workspace.py against it, replace jobs.sqlite with the release template, delete DONE markers, or overwrite tools during active rendering. New rendering settings require a new output/workspace version.

Start by reading queue status and errors. Zero pending jobs does not mean completion. Waiting/paused/failed states must be resolved explicitly. Do not promote adapters merely by editing labels, disable garment checks, or turn all blocked states into pending.

render_control.py status/check are read-only. start runs only already eligible pending work; retry requires exact job IDs and a resolved-cause note and preserves a database snapshot. Retry is whole-job restart, not frame-level resume. Keep existing completed outputs.

Current code supports one synthetic person per task and sequential batch rendering. Same-shot multiple synthetic people are not implemented. Multi-GPU scheduling is available; read docs/MULTI_GPU.md and distinguish queue tests from unmeasured GPU concurrency performance. Separate proposed designs from tested functionality.

After changes, validate the affected scope and update README/docs plus release hashes if maintaining a paired asset release. Do not claim all 159 subjects pass render QC. Never infer redistribution permissions for source assets.

## 本版强制预检补充

执行前遵循 [人物与任务预检](docs/PREFLIGHT.md)：新环境完整解包后先做人物级审计；生产执行和重试始终由 worker 做任务级完整预检。旧工作目录缺少任务预检 v1 时启动器拒绝运行，需先停机备份并更新 tools，保留原队列、登记表和结果。人物级通过不得替代任务级通过；不得自动启用阻断人物。

## Current user policy (2026-09-09)
Assume all registered human assets are usable. Historical supported/provisional/unsupported labels are informational, not activation/retry gates; --all activation is supported but packaging must not start rendering. Preserve real runtime errors and never fabricate completed results. Per-video render_record.json and its index support tracing and isolated rerenders; read docs/VIDEO_RECORDS.md. This overrides older instructions requiring an all-person audit before rendering.
