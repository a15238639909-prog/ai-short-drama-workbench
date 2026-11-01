# 本地模型接入详解

## 1. 接入边界

本项目是模型服务的客户端和流程编排器，不包含完整的模型运行环境。当前实现针对特定 llama.cpp、ComfyUI / Krea2、MOSS、Anima、MiniMax H3及可选Qwen Image 2.1编辑工作流；协议相似不等于可以直接替换模型。

本说明用于配置本项目，**不要求修改 Codex 的 `model_provider` 或把 AI 工具自身改成本地推理**。接入前先读实际源码和本机配置；未检查到的内容写“未验证”，缺少的内容写“缺失”，不要生成看似可用的假路径。

推荐先接通一个需要的功能。模型权重、推理引擎、显卡驱动、CUDA、ComfyUI 节点、FFmpeg 和其运行依赖需要独立核对，不由 `requirements.txt` 自动提供。

## 2. 工作台与配置入口

### Python 和启动方式

- 按 `README.md` 使用 Windows、Python 3.12+；其他系统未经本说明验收。
- `requirements.txt` 当前只有 `Pillow`、`zhconv`，不是完整模型依赖清单。
- `server.py` 默认监听 `127.0.0.1:8853`，可用 `V41_PORT` 更改端口。当前没有面向公网的多用户认证边界，不要直接对外暴露。
- `启动 AI视频工作台.vbs` 和 `启动（带窗口看报错）.bat` 使用 PATH 中的 `python`；创建 `.venv` 不会自动改变它们使用的解释器。
- VBS 启动器的就绪检查和浏览器地址仍固定为 `8853`。更改 `V41_PORT` 后，需要明确使用匹配的启动方式，不能只改端口就认为双击入口也同步了。
- 启动工作台会执行启动检查，并可能恢复已保存任务。先确认没有活动、排队或待恢复任务，不要为了检查依赖直接启动第二个工作台。

获准安装工作台依赖后，可以使用独立虚拟环境；这是安装步骤，不属于只读调查：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`config.json` 位于 `server.py` 同目录。只有确认它不存在时，才从 `config.example.json` 复制；已有配置先备份、按项合并，不覆盖整个文件。获准启动且核对任务状态后，用明确的解释器运行：

```powershell
.\.venv\Scripts\python.exe server.py
```

### 九个路径字段

`cores/paths.py` 的优先级是：**非空环境变量 > 根目录 `config.json` > 空默认值**。配置在模块加载时读取，修改后需在安全停机条件下重启工作台进程；刷新浏览器不等于重新加载配置。

| `config.json` 字段 | 覆盖环境变量 | 含义与检查重点 |
| --- | --- | --- |
| `llama_server_exe` | `V41_LLAMA_EXE` | 已有 `llama-server.exe` 的完整路径 |
| `model_gguf` | `V41_MODEL_GGUF` | 与当前客户端和模板兼容的 GGUF 模型 |
| `mmproj_gguf` | `V41_MMPROJ_GGUF` | 匹配该模型的多模态投影文件；当前自动启动检查也要求它存在 |
| `llama_log` | `V41_LLAMA_LOG` | 标准输出日志路径；文件尚未生成不等于模型缺失 |
| `llama_err_log` | `V41_LLAMA_ERR_LOG` | 错误日志路径；不要将包含私人输入的日志公开 |
| `comfy_dir` | `V41_COMFY_DIR` | Krea / MOSS / Anima 使用的 ComfyUI 便携环境根目录；可选Qwen编辑复用该实例 |
| `comfy_output` | `V41_COMFY_OUTPUT` | 对应 ComfyUI 的实际输出目录，不要填另一个实例的输出目录 |
| `h3_root` | `V41_H3_ROOT` | H3 专用便携环境根目录 |
| `legacy_root` | `V41_LEGACY_ROOT` | 可选旧工具目录；留空使用包内 `_legacy_8848`，不要求另开一个 8848 服务 |

`config.example.json` 使用 `X:/...` 占位路径，必须改成下载者自己的实际路径。这里没有通用 `provider`、`api_key` 或任意模型自动适配配置；不要自行发明字段后宣称已接通。

当前自动启动器期望以下结构，不代表任意 ComfyUI 安装都符合：

```text
<comfy_dir>/python/python.exe
<comfy_dir>/ComfyUI/main.py
<comfy_dir>/ComfyUI/input/
<comfy_dir>/ComfyUI/output/
<h3_root>/python/python.exe
<h3_root>/ComfyUI/main.py
<h3_root>/ComfyUI/input/
<h3_root>/ComfyUI/output/
```

发现非便携式安装时，先判断可否复用已运行服务，或列出需要适配的启动逻辑；不要重装覆盖现有环境。

## 3. 服务、协议和兼容性

| 功能 | 客户端与默认地址 | 无生成的状态接口 | 实际生成协议 |
| --- | --- | --- | --- |
| 文字 / 部分图文理解 | `models/qwen_client.py`；`http://127.0.0.1:8080` | `GET /v1/models`，上下文能力另看 `GET /props` | `POST /v1/chat/completions` |
| Krea 文生图 | `models/krea_client.py`；`http://127.0.0.1:8188` | `GET /system_stats`、`GET /queue` | `POST /prompt` 提交图，再查 `GET /history/{prompt_id}` |
| Krea 图片编辑 | `models/krea_edit_client.py`；共用 Krea 服务 | 同上 | 同上，但使用专用编辑图和节点 |
| Anima | `models/anima_client.py`；共用 Krea 服务 | 同上 | 同上，但使用 Anima 图 |
| MOSS | `models/moss_client.py`；共用8188服务 | 同上 | 同上，图为 `models/moss_workflow.json` |
| 漫画局部编辑（可选） | `models/qwen_image21_client.py`；只连已运行的本机8188 | 同上 | 同上，专用Qwen Image 2.1编辑图及图像输入 |
| H3 视频 | `models/h3_client.py`；`http://127.0.0.1:8190` | `GET /system_stats`、`GET /queue` | 同样使用 ComfyUI 图协议，不是通用视频 API |

健康接口成功只证明该地址有服务响应；还应核对返回结构、实际模型、工作流节点和资源条件。可以读取 ComfyUI `GET /object_info` 检查节点定义，但不要把全部返回内容复制到公共报告。

### 文字与图文理解的额外要求

- `V41_QWEN_URL` 可覆盖文字服务地址；否则按 `V41_QWEN_PORT`（默认 `8080`）构造地址。客户端固定发送模型标识 `qwen3.6-35b-a3b`，并非自动选择服务器上的任意模型。
- 请求使用 `messages`、`temperature`、`max_tokens`、`stream=false`，并发送 `chat_template_kwargs.enable_thinking`；部分调用使用 `response_format`。结果从 `choices[0].message.content` 读取。
- 图文请求还需要支持 `image_url` 内容和本地图片转成的数据 URL，模型与 `mmproj` 必须匹配。
- 正式导演调用启用精确额度检查：`GET /props` 读取 `default_generation_settings.n_ctx`，再向 `POST /v1/chat/completions/input_tokens` 发送对应请求体，读取 `input_tokens`。为请求的输出和额外 32 token 留出空间；不足时明确失败，不截原文、不偷偷降低输出额度。
- 因此只有 OpenAI 风格的聊天接口还不够；缺少上述 llama.cpp 扩展、JSON 或模板能力时，应报告不兼容，不关闭检查来掩盖问题。只读调查阶段先检查版本、源码或服务文档，不发送生成请求。
- 当前客户端没有认证请求头或通用云服务密钥配置。需要认证的远程服务不是直接改一个 URL 就完成接入。
- 自动启动参数包含 `--mmproj`、`--jinja`、`--host 127.0.0.1`、单并发槽 `-np 1`。`V41_QWEN_CTX` 默认 `16384`，`V41_QWEN_NGL` 默认 `999`，`V41_QWEN_START_WAIT` 默认 `180` 秒；容量不能脱离显存和实际模型随意调大。
- `V41_QWEN_TRACE` 会记录请求与结果，可能包含私人输入，不要为了接入默认打开或将日志放进分发包。

### 图像工作流的实际依赖

- Krea 文生图使用 `models/krea2_2stage_workflow.json`，图片编辑在 `models/krea_edit_client.py` 构造专用图。
- 当前模型文件名包含 `krea2\Krea2-MuseByStable_v15Turbo_fp8.safetensors`、`qwen3vl_4b_fp8_scaled.safetensors`、`qwen_image_vae.safetensors`；编辑还需要 `krea2\Krea2-编辑identity_edit_v1_2.safetensors`。
- 核对实际图中的类名和文件名，不只看文件“像不像”：例如 `ConditioningKrea2Rebalance`、`ColorMatchV2`、`Lora Loader (LoraManager)`、`Seed (rgthree)`，编辑还使用 `Krea2EditGroundedEncode`、`Krea2EditModelPatch`。
- Krea 自动启动启用特定自定义节点白名单。节点已经安装但被禁用，同样不能运行；应检查启动参数，不盲目重装。
- 文生图入口不接参考图；参考图编辑是另一个入口。当前文生图负面条件接 `ConditioningZeroOut`，不要承诺负面提示词一定生效。
- Anima 使用 `models/anima_workflow.json`，文件名包含 `anima_baseV10.safetensors`、`qwen_3_06b_base.safetensors`、`qwen_image_vae.safetensors`。其提示词要求英文词或标签，中文会被拒绝；自动写英文提示词还依赖文字模型。
- 图片编辑的 `build_graph` 也会复制输入文件，不属于只读探测函数。

### MOSS与主流程生图

- 读取 `models/moss_workflow.json`、`models/moss_client.py`、`models/moss_prompt.py` 与 `cores/main_images.py`。MOSS复用8188 ComfyUI，正常图片与漫画整页使用相同的生图客户端；不是新的云端API。
- 本版图文件使用 `Moss_image/Moss-dirtyMoss_int8_convrot.safetensors`、`qwen3.5_9b_int8_convrot.safetensors`、`flux2-vae.safetensors`。模型文件名、加载器和自定义节点应逐项对照实际图；相同模型家族的另一种量化或节点实现不能直接当成已兼容。
- 主流程人物与场景模型分别保存。MOSS的2倍与4K档由底图和双阶段放大参数共同决定；当前实际PNG尺寸记录在结果中。旧项目缺省字段、任务快照与已采用图片按兼容规则保留。
- MOSS写词需要本地Qwen。普通画面返回中文描述，人设多视图、漫画与文字排版需求可使用布局规划；布局坐标与生成画布绑定，切换尺寸后应重新规划或按程序转换。
- 出图词的混合待生产列表在页面内存中，各项入队时冻结模型、画布及种子。它不等于磁盘持久队列，刷新页面会丢失尚未生成的列表。

### Anima名字库

- 包内 `data/anima_names/20261026/characters.sqlite3` 是公开作品/人物名称与触发词库；具体来源和字段范围见 [ANIMA_CATALOG_SOURCE.md](ANIMA_CATALOG_SOURCE.md)。它不含角色外貌、图片或用户项目。
- `cores/anima_names.py` 以只读SQLite连接查询。英文名字或标签用于精确候选，中文只能匹配已整理别名；不能宣称覆盖任意中文人物名。重名需要补充作品，缺库或不完整记录会明确报错。
- 人物名命中不等于模型能精确还原人物；提示词身份检查与最终图片还原度是两项验收。

### 可选Qwen Image 2.1漫画局部编辑

- 这项功能只在用户明确提交漫画修图时调用，不是MOSS整页生成的前置依赖。接入文件为 `models/qwen_image21_client.py`、`cores/long_comic_edit.py` 和 `api/long_comic_api.py`。
- 本版客户端使用 `Qwen21-rayQwen21_v1_int8_convrot.safetensors`、`qwen3vl_8b_int8_convrot.safetensors`、`qwen_image_2.1_vae_bf16.safetensors`。需要 `QwenImage21Cache`、`TextEncodeQwenImage21` 等工作流节点，以及对应ComfyUI量化支持。
- 编辑连接地址复用 `models/krea_client.py`，但额外限制为已运行的本机8188服务。客户端不会为修图自动启动服务、停止其他服务或清空队列；服务缺席、排队或状态未知时应保留输入并报告。
- 用户上传的人物参考与人物ID绑定。框选坐标对应原图像素，程序扩展上下文后编辑并向内羽化贴回；框外原图像素保留。候选不自动采用，需检查人物、画风、动作与接缝。
- 接口或构图函数可能复制输入、写候选或实际提交任务，不能拿来做只读健康检查。只读阶段仍先检查端口、`/queue`、`/system_stats`、模型文件及 `/object_info`。

### H3 工作流的实际依赖

- 图文件为 `models/video_workflows/t2va.json`、`models/video_workflows/i2va_fl2va.json`、`models/video_workflows/ref2va.json`。
- 客户端支持 `t2va`、`i2va`、`fl2va`、`ref2va`；当前最多 9 张参考图，参考视频最多 3 个且仅用于 `ref2va`。
- 文件名包含 `DasiwaMinimaxH3_dasiwaREF2VAHybridV1.safetensors`、`qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`、`minimax_h3_video_vae_fp16.safetensors`、`minimax_h3_audio_vae_fp32.safetensors`。
- 需要对应 H3 节点，例如 `MiniMaxH3ImageToVideo`、`MiniMaxH3ReferenceToVideo`、`MiniMaxH3MemoryEfficientSageAttentionPatch`，以及 `ReservedVRAMSetter`、`VHS_VideoCombine`、`VAEDecodeAudio` 等。应逐项对照实际工作流与 `/object_info`，不能只验证 ComfyUI 能打开。

### 端口与资源调度限制

- `V41_COMFY_URL`、`V41_H3_URL` 可覆盖客户端连接地址，但 Krea / H3 自动启动参数仍分别固定端口 `8188`、`8190`，`models/gpu_manager.py` 也使用这两个固定端口。只改 URL 不会同步修改启动器和 GPU 调度。
- 文字服务的启动与调度端口使用 `V41_QWEN_PORT`。若 `V41_QWEN_URL` 指向别处，必须单独核对连接地址与本机进程管理是否一致。
- `cores/story_video_offline.py` 的独立工作进程明确使用 `V41_QWEN_PORT=18080`、`V41_QWEN_URL=http://127.0.0.1:18080`，不能只验证 8080 后就认为该链路也接通。
- `models/story_video_client.py` 的相关入口只接受 HTTP(S) 回环地址：`localhost` 或回环 IP；拒绝非本机地址、URL 用户凭据、查询参数和片段。它采用离线环境，不自动回退云端。
- 模型调用可能通过 GPU 调度停止其他模型服务。不要用 `chat`、`generate`、`start`、`claim` 或 `guard` 来“检查有没有安装”；任何实际调用前先检查活动任务并说明资源影响。
- 远程服务、多实例、非标准目录或另一种模型栈需要单独适配和验收，不能宣称当前包自动支持。

## 4. 只读发现与检查

以下命令在项目根目录执行，不安装依赖、不启动服务、不生成模型内容。不要批量运行未阅读过的脚本，也不要直接调用 `/api/doctor`：该接口会做文件写入探测，不是严格只读检查。

### 确定解释器、依赖与已有进程

```powershell
Get-Command python,py,ffmpeg,ffprobe -ErrorAction SilentlyContinue | Select-Object Name,Source
python --version
python -m pip show Pillow zhconv
Get-Process -Name 'llama-server','python','pythonw' -ErrorAction SilentlyContinue | Select-Object ProcessName,Id,Path
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 8853,8080,8188,8190,18080 } | Select-Object LocalAddress,LocalPort,OwningProcess
```

若项目已有 `.venv`，应另外检查该解释器的版本与依赖，而不是把系统 Python 的结果当成它的结果：

```powershell
Test-Path -LiteralPath '.\.venv\Scripts\python.exe'
```

仅当上述路径存在时，使用 `.\.venv\Scripts\python.exe --version` 和 `.\.venv\Scripts\python.exe -m pip show Pillow zhconv`。缺少 `pip`、FFmpeg 或命令不能证明磁盘上完全没有它们；继续在用户指定位置定向查找，不自动安装。

### 检查已配置路径，不导入模型客户端

下面只输出路径项是否存在，不输出实际路径或凭据。无 `config.json` 时将按空配置检查，示例占位路径不算有效环境。

```powershell
$config = [pscustomobject]@{}
if (Test-Path -LiteralPath '.\config.json') {
    $config = Get-Content -LiteralPath '.\config.json' -Raw -Encoding UTF8 | ConvertFrom-Json
}
$mapping = [ordered]@{
    llama_server_exe = 'V41_LLAMA_EXE'
    model_gguf = 'V41_MODEL_GGUF'
    mmproj_gguf = 'V41_MMPROJ_GGUF'
    llama_log = 'V41_LLAMA_LOG'
    llama_err_log = 'V41_LLAMA_ERR_LOG'
    comfy_dir = 'V41_COMFY_DIR'
    comfy_output = 'V41_COMFY_OUTPUT'
    h3_root = 'V41_H3_ROOT'
    legacy_root = 'V41_LEGACY_ROOT'
}
foreach ($entry in $mapping.GetEnumerator()) {
    $value = [Environment]::GetEnvironmentVariable($entry.Value)
    $source = 'environment'
    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = [string]$config.($entry.Key)
        $source = 'config-or-empty'
    }
    $exists = $false
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        $exists = Test-Path -LiteralPath $value.Trim()
    }
    [pscustomobject]@{ Field=$entry.Key; Source=$source; Configured=(-not [string]::IsNullOrWhiteSpace($value)); Exists=$exists }
}
```

日志文件可以尚不存在；`legacy_root` 允许留空，不能将这两类情况一律判为故障。

只检查需要的 `V41_*` 变量，避免输出全部环境变量。查到目录后用 `Test-Path -LiteralPath` 核对解释器、`main.py`、实际工作流要求的模型和节点文件，不要递归扫描整个磁盘。

### 只探测已经运行的本机服务

先用配置和监听端口确认地址、实例与所属进程。下例是默认地址；只运行与目标功能相符的行，不启动缺席的服务，不据此触发任务：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8853/api/tasks' -TimeoutSec 5
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/v1/models' -TimeoutSec 5
(Invoke-RestMethod -Uri 'http://127.0.0.1:8080/props' -TimeoutSec 5).default_generation_settings.n_ctx
Invoke-RestMethod -Uri 'http://127.0.0.1:8188/system_stats' -TimeoutSec 5
Invoke-RestMethod -Uri 'http://127.0.0.1:8188/queue' -TimeoutSec 5
Invoke-RestMethod -Uri 'http://127.0.0.1:8190/system_stats' -TimeoutSec 5
Invoke-RestMethod -Uri 'http://127.0.0.1:8190/queue' -TimeoutSec 5
```

任务和系统信息可能包含私人路径或输入，仅在本地查看，不原样上传。连接失败只记录“该地址当前不可达”，不要自动启动、杀进程、改防火墙或判定模型文件不存在。任务列表不是所有待恢复任务的证明；安全重启还需核对 `data/generation_jobs` 等本地任务记录，不修改这些业务数据。

## 5. 不调用真实模型的模拟测试

以下已有测试使用模拟请求或进程，适合在依赖齐备后验证客户端约定，不代表真实权重、显存和模型质量已经通过。测试可能写入测试临时目录，不属于上一节的纯只读检查；不要替用户自动清理临时目录。

```powershell
$env:V41_NO_MODEL = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m unittest tests.test_qwen_context_budget tests.test_story_video_qwen_port tests.test_h3_request_recovery
```

用实际选定的 Python 替换 `python`。建议在专门的终端会话执行，结束后关闭会话，避免测试变量影响正常使用。`V41_NO_MODEL=1` 是本项目的模型调用保护，不是对任意外部脚本或 HTTP 请求的系统级封锁。

## 6. 获准后再做最小真实验收

1. 先报告将修改的配置、备份位置、需启动的服务、可能释放或占用的显存、预计调用次数及停止方式，取得用户同意。只改必要项，不覆盖已有环境或凭据。
2. 核对活动及待恢复任务；如需重启，先安全停止旧进程，不启动重复实例。实际使用前退出模拟测试终端，确认未遗留 `V41_NO_MODEL=1`。
3. **每次本地模型调用前重新检测 PUBG**，不能沿用上次结果，也不能自动关闭游戏：

```powershell
$pubg = Get-Process -Name 'TslGame','TslGame_BE','PUBG' -ErrorAction SilentlyContinue
if ($pubg) { 'PUBG_RUNNING' } else { 'PUBG_NOT_RUNNING' }
```

返回 `PUBG_RUNNING` 时停止并询问是否允许继续；检测出错或无法确认时也停止，不当成“未运行”。

4. 只对本次目标做最小、普通内容的真实请求：例如一条短文本、一张低成本图片，或工作流支持范围内的最短视频。不要默认生成完整故事或串行调用全部模型；分辨率、时长和采样参数应在实际工作流允许范围内设置。
5. 检查真实请求、错误响应和输出文件能否正常打开，确认实际走的是目标服务及模型。HTTP 200、文件存在、模拟测试通过都不能单独证明完整接入成功。
6. 失败时保留错误证据并说明原因，不盲目重试、扩大上下文、降低校验、换模型或继续下载。达到验收标准后停止。

## 7. 接入记录模板

将记录保存在用户批准的本地位置；若需要公开，先脱敏。不要在模板中填写密钥或私人输入全文。

```text
目标功能：
本次授权范围：调查 / 修改配置 / 安装指定依赖 / 启动指定服务 / 最小真实测试
项目版本或提交：
使用的 Python 及版本：
发现依据：配置 / 指定目录 / PATH / 已运行进程
有效路径项及来源：已配置、存在性、环境变量是否覆盖（公开版隐去私人路径）
服务地址与所属实例：
协议核对：健康接口、模板/JSON、精确额度接口或 ComfyUI 节点
模型与投影 / 工作流文件 / 节点：实际核对结果
端口和资源调度：含自动启动固定端口、独立 18080 链路（若使用）
已修改文件及逐项变更：
修改前备份位置：
模拟测试命令、时间与结果：
真实测试授权、PUBG 检查结果和调用次数：
真实请求及输出证据：本地记录位置，不含凭据
结论：已接通 / 部分接通 / 缺失 / 不兼容 / 未验证
未验证能力、限制与下一步：
回滚：恢复哪些备份；是否需在无任务时重启哪个服务
```

## 8. 发布与回滚

- 不将实际 `config.json`、`.env`、密钥、令牌、私人请求日志、项目数据、生成文件、模型缓存、权重和本机备份加入 Git 或公共分发包。只分发脱敏示例与必要源码，提交前检查暂存区，不只依赖 `.gitignore`。
- 用户已有模型环境属于独立资产，工作台升级不应覆盖它。需要适配工作流时先备份并记录差异，不修改已完成项目的审计数据。
- 回滚只恢复本次改动的配置或工作流；先确认无活动任务，再按需重启相应服务。不要清空数据目录或删除模型来“恢复默认”。删除确有必要时遵守用户的 Windows 回收站规则。
