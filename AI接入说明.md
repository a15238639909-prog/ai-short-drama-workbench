# 让 AI 帮你接入本机模型

这份说明用于让 Codex 等 AI 工具配置**本项目调用的模型服务**，不是把 Codex 自身切换成本地模型，也不需要修改 Codex 的 `model_provider`。

本项目已经实现了特定的 llama.cpp、ComfyUI / Krea2、MOSS、Anima、MiniMax H3 调用链，并提供可选的 Qwen Image 2.1 漫画局部编辑。下载工作台不等于同时安装模型、推理引擎、显卡驱动和工作流节点，也不能保证任意模型直接替换后可用。

## 直接复制给 AI

在 AI 工具中打开解压后的项目目录，将以下任务完整发给它：

```text
请帮我将这个 AI 视频工作台接入本机已有的模型环境。配置的是本项目客户端，不是修改你自身的模型提供商或 Codex 设置。

先只读调查，暂不安装、不下载、不启动模型、不生成内容，也不修改文件：
1. 确定当前项目根目录，读取 AGENTS.md、AI接入说明.md、docs/LOCAL_MODEL_INTEGRATION.md、README.md、requirements.txt 和 config.example.json。
2. 在本地读取已有 config.json（如存在），以及 cores/paths.py、models/qwen_client.py、models/gpu_manager.py、models/krea_client.py、models/krea_edit_client.py、models/moss_client.py、models/moss_prompt.py、models/anima_client.py、models/h3_client.py、models/story_video_client.py、cores/story_video_offline.py 和实际工作流。需要漫画局部修图时另读 models/qwen_image21_client.py、cores/long_comic_edit.py；需要角色查询时检查 cores/anima_names.py 和包内名字库。不要在回复、日志或提交中暴露任何密钥、令牌或私人内容。
3. 在已配置目录、我提供的目录、PATH 和当前运行进程中自动发现已有 Python、llama.cpp、ComfyUI、H3、模型文件和 FFmpeg。只做定向查找，不扫描整个磁盘，不覆盖或重装已有环境。若需要扩大搜索范围，先说明理由并征求同意。
4. 检查配置字段、环境变量覆盖、端口、实际目录布局、模型文件、节点类型及 API 能力是否匹配。对已运行的本机服务只做文档列出的只读状态请求；不要调用 /api/doctor，不要把生成请求当健康检查。
5. 给出“已找到 / 缺失 / 不兼容 / 未验证”清单，每个结论附证据。不要虚构路径、接口或依赖，不要把服务能响应等同于模型已接通。
6. 提出最小必要配置方案、会改动的文件、是否需要停止或启动服务、风险、备份和回滚方式。不要悄悄修改工作流以迁就另一种模型，也不要自动下载模型。

等我明确同意实施后，再备份并只修改必要配置；只安装或启动我批准的项目。修改前检查活动任务，避免中断生成或启动重复服务。每次调用本地模型前重新检测 PUBG；如果正在运行或无法确认状态，停止并询问我。不得自动关闭游戏或其他模型服务。

先运行不调用真实模型的模拟测试。需要真实验收时，另行说明会调用什么模型、占用什么资源并取得同意；只做我需要功能的一次最小、普通内容测试，不默认运行整个视频流水线。最后按详细文档的接入记录模板，报告实际改动、验证证据、未验证事项和回滚办法。配置凭据、私人日志、模型权重、项目数据和生成结果不要上传 Git。
```

## 开始前告诉 AI 的信息

- 你想使用哪些功能：文字、Krea2/MOSS生图、Anima、长篇漫画、可选Qwen图片编辑或H3视频；不必一次接通全部功能。
- 已安装模型环境的大致目录，以及是否已经有服务在运行；不知道的可以写“不知道”。
- 是否允许安装依赖、启动服务和做一次真实测试。默认先调查，不自动执行这些操作。

字段、协议、检查命令和验收模板见 [本地模型接入详解](docs/LOCAL_MODEL_INTEGRATION.md)。接入结论以你的机器和当前源码为准。
