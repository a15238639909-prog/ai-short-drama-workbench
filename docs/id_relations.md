# id_relations.md — 全局 ID 与依赖图（Phase 0 设计稿）

## 1. ID 规则
- 稳定 ID：STORY_001 / CHAR_LINEN / WARDROBE_AILIN_TRAVEL_V1 / SCENE_RAIN_VILLAGE / EVENT_00103 / UNIT_00023 / STATE_00042 / SCRIPT_00004 / COMIC_PAGE_006 / VIDEO_SEQ_004 / NOVEL_CHAPTER_003 / ASSET_VISUAL_AILIN_V2 / MANIFEST_00017
- 名字是显示文本；改角色名不改变内部 ID；同一人物/场景/事件在小说/漫画/视频/画册中不得复制多份。

## 2. 统一链路
```
Story Truth
  -> Master Plan
  -> Event Ledger
  -> Narrative Units
  -> State Graph
  -> Asset Contracts (Character/Wardrobe/Scene)
  -> Production Adapters (Novel/Comic/Video/Album)
```
所有 Production 只保存引用与媒介私有数据。

## 3. 依赖图示例
### 漫画第 6 页
```
COMIC_PAGE_006 depends_on:
  STORY_001
  SCRIPT_004
  UNIT_023
  CHAR_LINEN identity v1
  CHAR_AILIN identity v2
  WARDROBE_AILIN_TRAVEL v1
  SCENE_RAIN_VILLAGE v1
  STYLE_COMIC_REALISTIC v3
  STATE_00040（本页开始时状态）
```
### 视频 SEQ04
```
VIDEO_SEQ_004 depends_on:
  UNIT_023
  DirectorPlan v2
  CHAR_LINEN approved visual v1
  CHAR_AILIN approved visual v2
  SCENE approved visual v1
  Video Bible v1
  STATE_00041（段开始状态）
```
### 小说第 27 章
```
NOVEL_CHAPTER_027 depends_on:
  STORY_001
  EPISODE_027
  UNIT 集合
  CHAR 状态（当前章相关）
  最近摘要 + 伏笔（由 Context Engine 组装，不取全文）
```

## 4. 最小重算
- 只改对白：只影响排字。
- 改 Narrative Unit 的 Event：影响对应小说 Scene/漫画页/视频 Sequence；提示影响范围，不自动破坏历史成品。
- 改人物基础身份：影响未来 Krea Prompt、H3 Reference Pack、未来画册；历史正式成品保留。
- 依赖图中 asset 版本变化 → 未来产出使用新版本，历史 Manifest 记录旧版本。

## 5. 引用检查（Level 1 程序 QA）
- 每个 Production 的 unit/event/character/scene/state 引用必须存在。
- Event coverage：Core Event 必须被 Unit 承担；漫画每核心事件必须有页承担；视频每核心事件必须有 Sequence 承担；小说每核心事件必须在章节真正发生。
- 不允许出现无来源的新 Core Event；不允许提前/重复执行。
