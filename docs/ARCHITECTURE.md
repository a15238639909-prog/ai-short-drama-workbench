# 框架契约：一个故事 = 一个项目

> 这份文档定的是**结构约束**，不是某一版实现。写代码前先看这里；违反下面四条的实现不允许进主线。

## 0. 一句话

一个故事只有一份事实；视频、漫画、小说、画册是这份事实的四种**产出方式**，不是四个独立工具。

旧 8848 的问题是：写小说、做漫画、视频导演台各自维护自己的人物和剧情，同一个角色在三个模块里是三个人。新框架不允许这样。

## 1. 四条硬约束

1. **上游唯一**：故事事实、人物、场景、状态账本、视觉资产只有一份，存在项目目录里，四条成品线共读。
2. **下游只展开不改写**：成品线可以决定怎么拍/怎么画/怎么写，不能新增剧情、改人物状态、改场景事实。
3. **一条线只有一个实现**：视频只有一个视频链，漫画只有一个漫画链。发现两套并行，合并或删掉一套，不允许"旧的不动，在旁边再写一套"。
4. **一处判定**：验收规则只写在 `cores/text_checks.py`，报告可以输出到多个位置，但结论必须来自同一次计算。

## 2. 分层

```
用户  →  web/            前端：项目选择 + 12 个页面
         server.py       服务层：HTTP 路由，只做参数校验和任务派发
         cores/          领域层：故事事实、状态、资产、上下文、验收（不碰 HTTP，不碰模型）
         production/     成品层：视频 / 漫画 / 小说 / 画册，各自的专业流程
         models/         模型层：Qwen / Krea2 / H3 客户端 + GPU 互斥
         data/           项目数据
```

长任务一律走 `cores/runtime_core.py` 的 TaskCenter（queued / running / waiting_user / paused / interrupted / failed / completed / cancelled），不允许在路由里直接跑几分钟的活。

## 3. 项目数据目录

一个故事一个 `story_id`，数据按 id 分散在这些位置（现状，尚未收拢到单一目录）：

| 内容 | 位置 | 谁写 | 谁读 |
|---|---|---|---|
| 故事主体（一句话 / 母计划 / 阶段） | `data/stories/<ID>.json` | story_core | 全部 |
| 人物 / 场景 / 视觉资产 | `data/assets/<ID>/{characters,scenes,visuals}` | asset_core | 全部 |
| 分话剧本与事件 | `data/narrative/<ID>/episode_*.json` | narrative_core | 全部 |
| 状态账本 | `data/states/<ID>/` | state_core | 全部 |
| 成品记录 | `data/productions/<KIND>_<ts>.json` | production/* | 前端产出页 |
| 成品文件 | `productions/<kind>/...` | production/* | 用户 |
| 任务 | `data/tasks/task_*.json` | runtime_core | 任务页 |
| 自定义内容规则 | `data/content_profiles/` | profile_core | 生成链 |

**上游 = 前四行，四条成品线共读；下游 = 后三行，各线各自负责。**

## 4. 成品线

| 线 | 入口 | 实现 | 共读上游 |
|---|---|---|---|
| 视频 | `/api/produce` kind=video_formal | `production/video_plan.py` + `video_core.py` | ✅ |
| 漫画 | kind=comic | `production/comic_core.py` | ✅ |
| 小说 | kind=novel | `production/novel_core.py` | ✅ |
| 画册 | kind=album | `production/album_core.py` | ✅ |

## 5. 文字链在哪一层

文字链（剧本解析 → 导演 → 分镜 → 官方 H3 提示词 → 内容级验收）是**领域层能力**，不属于某一条成品线：

| 能力 | 模块 |
|---|---|
| 剧本解析：谁在说话、哪句是台词、哪句是动作 | `cores/script_text.py` |
| 项目档案：角色代号、Subject 标签、内部 ID 映射 | `cores/story_profile.py` |
| 提示词卫生：内部 ID 清理、否定式转正向 | `cores/prompt_hygiene.py` |
| H3 官方 Ref2VA 六段式、运镜词表、时间轴 | `cores/h3_format.py` |
| 内容级验收（19 项） | `cores/text_checks.py` |

`pipeline/` 目录是这些能力的**离线跑法**，用于单独调试一条故事的文字链；它不是第二套实现，规则一律来自 `cores/`。

## 6. 当前欠账（按优先级）

1. **视频线有三套并行**：`video_plan/video_core`（接了共享上游，前端在用）、`production/v423_core.py`（不 import 任何 cores，独立）、`pipeline/`（离线，跑在 `reports/v423_text_final/` 上）。违反约束 3。→ 收敛成一条。
2. **文字链数据不在项目里**：`pipeline/` 读写 `reports/v423_text_final/final_review/`，与 `data/<ID>/` 无关，因此前端点不到、验收结果在界面上看不到。→ 迁到项目目录。
3. **前端缺全局状态**：老 8848 有「Qwen 开/关、ComfyUI 开/关、一键释放显存、运行日志面板」，新前端没有。后端 `models/gpu_manager.py`、`runtime_core` 的游戏模式已经有了，缺的是界面。
4. **资产生命周期未闭环**：Draft → Preview → Adopt → Frozen 里，同一实体多版本并存时没有强制单一 Active（场景图 v1/v2/v3 目前九张并存）。

## 7. 加新东西之前先问

- 这份事实上游已经有了吗？有就读，不要重新生成一份。
- 这条成品线已经有实现了吗？有就改它，不要在旁边新写一个。
- 这个判定 `cores/text_checks.py` 里有吗？没有就加在那里，不要在脚本里就地写断言。
- 这个长任务进 TaskCenter 了吗？没有就接进去，不要在路由里直接跑。
