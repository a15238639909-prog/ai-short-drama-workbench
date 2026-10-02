# data_schema.md — V4.1 统一数据 schema（Phase 0 设计稿）

原则：单一事实源；名字是显示文本，ID 是内部引用；改名字不改 ID；同一人物/场景/事件在全部媒介中只存在一份。

## 1. Story（Story Core）
```
story_id          e.g. STORY_001
title
one_line
mode              short | long
world_rules[]     世界硬规则（长期事实，不随剧情漂移）
master_plan       MasterPlan
character_ids[]   -> CharacterContract
scene_ids[]       -> SceneContract
created / updated / archived
```

## 2. MasterPlan
```
core_conflict
protagonist_goal
relationships[]     主要人物关系
world_rules[]       与 Story.world_rules 一致
phases[]            {name, goal, key_turns[]}
key_twists[]
important_reveals[]
climax_direction
ending_direction
must_pay_off[]      必须回收的重要伏笔
```

## 3. EpisodeBrief（第 N 话/章/视频段具体发生什么）
```
brief_id           e.g. EPISODE_001
story_id
sequence_id        所属篇章
episode_no
title
synopsis           本话/本章事件范围（不写镜头/格/文学正文）
event_ids[]        本段必须发生的 Event
scope
```

## 4. Event（Event Ledger）
```
event_id           e.g. EVENT_00103
story_id
importance         core | support
title / desc
prerequisites[]    Event IDs
participants[]     Character IDs
location           Scene ID
required_state_before   State ref 或描述
state_changes[]    结构化状态变化
first_allowed_unit Narrative Unit ID（不提前）
must_happen_before[] Event IDs
status             planned | happened | retconned
canon_source       adopted 记录 ID
```

## 5. NarrativeUnit（跨媒介共享最小剧情单元）
```
unit_id            e.g. UNIT_00023
story_id
brief_id
purpose            这一单元在故事里负责什么
reader_or_audience_takeaway
start_state_refs[]
character_goals{}   {CHAR_ID: goal}
obstacle
must_include_event_ids[]  -> Event IDs
key_moment
state_change        谁/什么发生什么变化
result
caused_by           承接哪个 Unit/Result
leads_to            引出哪个 Unit
end_state_refs[]
importance
```

## 6. StateSnapshot（State Core，只由 Adopt/State Commit 产生）
```
state_id           e.g. STATE_00042
story_id
version
source_event_id / source_unit_id
canonical          bool（采用后才为 true）
world_state{}
character_states{ CHAR_ID: {
    location, body_condition, injuries[], wardrobe_id, temporary_appearance,
    held_objects[], abilities[], knowledge[], unknowns[],
    relationships{}, current_goal, last_event_id } }
scene_states{ SCENE_ID: {
    time, weather, damage, active_objects[], character_positions{},
    temporary_lighting_state, last_event_id } }
plot_state{ resolved_events[], open_threads[], foreshadowing[], promises[], secrets[], deadlines[] }
```

## 7. CharacterContract（Asset Core 文字契约，Krea 一致性核心）
```
character_id       e.g. CHAR_LINEN
story_id
identity_anchor { gender_age, bone_structure, face_features, head_ratio,
                   hair_outline, body_structure, fixed_markers[] }
behavior_anchor { default_mood, posture, personality_visible }
default_wardrobe_id
world_compatibility
frozen             bool（采用后冻结，禁止同义改写）
version
```

## 8. WardrobeContract
```
wardrobe_id        e.g. WARDROBE_AILIN_TRAVEL_V1
character_id
top / bottom / outer / shoes / belt
material[] / colors[]
fixed_decor[]
base_wear
（湿透/破损/沾泥/血迹属于 State，不改 Master）
```

## 9. SceneContract
```
scene_id           e.g. SCENE_RAIN_VILLAGE
story_id
spatial_topology
regions{ left/right/far }
materials[]
fixed_landmarks[]
base_light_color
version / frozen
```

## 10. ApprovedVisual
```
visual_id          e.g. ASSET_VISUAL_AILIN_V2
kind               character_master | face | full_body | wardrobe | scene_master | scene_key | video_keyframe | album_ref
character_id / scene_id
visual_version
status             draft | starred | adopted
path / file
created
manifest_id
```

## 11. Production（只保存引用 + 媒介私有数据，不复制故事事实）
### NovelChapter
```
chapter_id NOVEL_CHAPTER_003; story_id; unit_ids[]; title; brief
content; state_before_id; state_after_id; summary; status draft|adopted
```
### ComicPage
```
page_id COMIC_PAGE_006; story_id; unit_id; brief_id
page_purpose / reader_takeaway / state_change / key_moment
event_ids[]; caused_by; page_result; leads_to
page_plan / panel_plan[]
prompt; image; lettered_image; pdf_ref; status draft|adopted
```
### VideoSequence
```
seq_id VIDEO_SEQ_004; story_id; unit_id
director_plan; seconds (<=15)
reference_pack_id; mode (ref2va/i2v/fl2v/t2v)
takes[] {take_id, path, status}; adopted_take
timeline_id; output; status
```
### AlbumProject
```
album_id; story_id; kind; plan; sample; images[]; pdf; status
```

## 12. ReferencePack（正式视频每段自动生成）
```
reference_pack_id
seq_id
REF_ROLE_CHARACTER_A -> CHAR_LINEN approved image
REF_ROLE_CHARACTER_B -> CHAR_AILIN approved image
REF_ROLE_SCENE -> SCENE_RAIN_VILLAGE approved image
REF_ROLE_CONTINUITY -> 上一段必要末状态/关键帧
manifest
```

## 13. Generation Manifest
```
output_id; story_id; production_type
source_narrative_ids[]; event_ids[]; state_snapshot_ids[]
character_contract_versions{}; wardrobe_versions{}; scene_versions{}
style_profile; director_version; prompt; model; model_mode
reference_pack_ids[]; parameters{}; created_at; output_path
```

## 14. Canon 状态流
draft -> candidate -> starred -> adopted -> canon（写回 Story/State/ApprovedVisual）
AI 输出在采用前一律不改变 Story Truth；State Commit 是唯一状态写入入口。
