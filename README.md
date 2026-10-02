# 本地 AI 视频工作台

把故事想法、人物设定、分段提示词和视频制作放在同一个本地网页里。

这是现有 Windows 工作台的源代码发布候选版，不包含模型、用户项目或生成素材。默认调用本机模型服务；安装代码本身并不等于安装好了模型。

## 能做什么

- 输入一句话或一段故事想法，先生成文字，再检查、修改。
- 编辑人物卡、场景卡，生成对应的设定图。
- 调整人物的头身比例、体型、五官、发型与服装；在设置页修改预设和指令词。
- 检查剧本和视频分段，编辑每段的提示词与参考素材。
- 调用本地视频工作流逐段生成，再用 FFmpeg 合成成品。
- 在同一项目里制作续话，复用人物和场景设定。

人物、姿势、服装、动作、台词和接缝的最终效果取决于模型，仍需要人工验收。本仓库不承诺精确头身数、完全一致的人脸或无缝视频。

## 运行环境

现有流程在 Windows 上使用；其他系统尚未验证。

| 用途 | 外部程序或模型 | 默认服务地址 |
| --- | --- | --- |
| 写故事、设计人物、写提示词 | llama.cpp 的 llama-server + 兼容的 Qwen GGUF 模型 | 127.0.0.1:8080 |
| 人设图、场景图 | ComfyUI + 对应 Krea2 工作流、模型及节点 | 127.0.0.1:8188 |
| 视频 | ComfyUI + 对应 MiniMax H3 工作流、模型及节点 | 127.0.0.1:8190 |
| 视频读取与合成 | FFmpeg、ffprobe | 加入 PATH |
| 工作台 | Python 3.12+、Pillow、zhconv | 127.0.0.1:8853 |

Python 客户端与工作流 JSON 在 `models/` 中，它们不是模型权重。各模型和节点需另行安装，并遵守各自的使用许可。显存与速度取决于所选模型、分辨率和硬件，不保证普通电脑可以直接出片。

## 安装

下载源码并解压到可写目录。在该目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.json config.json
```

编辑 `config.json`，把示例路径改成自己的真实路径。不要填写其他人的路径，也不要提交这个文件。

```json
{
  "llama_server_exe": "X:/llama.cpp/llama-server.exe",
  "model_gguf": "X:/models/qwen.gguf",
  "mmproj_gguf": "X:/models/mmproj.gguf",
  "llama_log": "X:/llama.cpp/workbench.log",
  "llama_err_log": "X:/llama.cpp/workbench.err.log",
  "comfy_dir": "X:/ComfyUI",
  "comfy_output": "X:/ComfyUI/output",
  "h3_root": "X:/MiniMax-H3",
  "legacy_root": ""
}
```

`comfy_dir` 和 `h3_root` 必须匹配客户端预期的目录结构，不是任意一个安装目录都能直接接入。启动方式详见 `models/krea_client.py`、`models/h3_client.py`。使用自行管理的服务时，可以分别通过 `V41_QWEN_URL`、`V41_COMFY_URL`、`V41_H3_URL` 设置地址；仍需检查工作流节点与模型文件名是否匹配。

启动：

```powershell
.\.venv\Scripts\python.exe server.py
```

打开 `http://127.0.0.1:8853/`。首次启动会创建空的数据目录，不会导入作者的私人项目。

如使用双击启动脚本，需要 `python` 命令已指向装好依赖的环境；否则请使用上面的虚拟环境命令。

## 建议使用顺序

1. 新建项目，写故事想法，检查世界类型、画风和视点。
2. 先生成文字，检查故事和人物卡。
3. 需要时先出设定图，由自己检查外观。
4. 检查剧本和分段，确认每段提示词及参考素材。
5. 生成视频，逐段检查，再合成成品。

默认项目设置为：西方、网红自然感、电影第三人称、0.4 档、10 步。可以按项目修改。正文节奏有手动选项，视频分段并不等同于这个节奏选项。

## 可修改文件

| 想改什么 | 位置 |
| --- | --- |
| 人物、比例、体型等预设 | 设置页及 `presets/` |
| 给文字模型的指令 | 设置页及 `presets/instructions/` |
| 图片与视频工作流 | `models/*.json`、`models/video_workflows/` |
| 本机模型路径 | `config.json`（不提交） |
| 新项目的默认设定 | `cores/project_settings.py`；使用后可在设置页保存全局默认 |

手动改预设后，已有图片不会自动变化，需要按当前人物卡或提示词重新出图。

## 目录

```text
server.py             网页服务
api/                  接口
cores/                故事、人物、分段与任务逻辑
models/               本地模型客户端和工作流 JSON
production/           成品处理
web/                  原生 HTML / CSS / JavaScript 界面
presets/              预设与指令词
tests/                代码测试
docs/                 数据结构与架构说明
data/                 运行时项目数据（不入库）
outputs/              运行时素材与成品（不入库）
```

主流程不依赖旧版 8848 附带工具。本发布副本未包含旧版工具及其个人数据；`legacy_root` 保留为空，可选接入自己已有的旧工具目录。

## 安全与隐私

- 发布版默认只监听本机。没有面向公网的登录和权限系统，不要端口映射，不要用作公网服务。
- `config.json`、`data/`、`outputs/`、缓存、日志、备份和模型权重全部排除在 Git 之外。
- 若自己把模型地址改成远程服务，输入及参考素材可能发送到该服务；这不属于纯本地运行。
- 模型切换可能停掉其他本地模型服务。不要在重要的其他推理任务进行中使用自动切换。
- 对生成内容自行验收；人物年龄与内容边界仍需检查。不要移除年龄保护。

## 发布验证

本次仅做源代码、配置和空数据启动检查，不启动模型，不把历史项目的测试成绩当作本发布版保证。具体结果以发布检查记录为准。

## 许可证

本项目代码采用 [MIT 许可证](LICENSE)。模型、第三方节点和外部程序的许可不随本项目代码许可证改变。
