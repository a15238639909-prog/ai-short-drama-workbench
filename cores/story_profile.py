# -*- coding: utf-8 -*-
"""故事档案：把「这个项目里谁是谁、有哪些场景/序列/事件/资产」从数据里读出来。

存在的理由：以前每个一次性脚本都各自硬编码一份
    ID_MAP = {"CHAR_A_旅行者": "林舟", "SCENE_01_STREET": "欧洲老城街道", ...}
    SPEAKERS = ["林舟", "艾莉莎"]
换一个故事就要改三个文件，改漏一个就出错。现在统一从 final_review 目录的
既有 JSON 推导，任何故事都能直接用。

用法：
    from cores.story_profile import StoryProfile
    profile = StoryProfile.load(review_dir)
    profile.speakers          # ["林舟", "艾莉莎"]
    profile.role_names        # {"A": "林舟", "B": "艾莉莎"}
    profile.role_subjects     # {"A": "<Subject 1>", "B": "<Subject 2>"}
    profile.id_map            # 全部内部 ID → 自然称呼
"""
import json
import re
from pathlib import Path

__all__ = ["StoryProfile"]

# CHAR_A_旅行者 / CHAR_B_欧洲女孩 → 角色代号 A / B
_ROLE_CODE_RE = re.compile(r"^CHAR_([A-Z0-9]+)(?:_|$)")
# 名字里的括号别名：艾莉莎（Elisa） → 艾莉莎
_ALIAS_RE = re.compile(r"[（(].*?[）)]")


def _read(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def _first_json(directory, default=None):
    d = Path(directory)
    if not d.is_dir():
        return default
    for f in sorted(d.glob("*.json")):
        return json.loads(f.read_text(encoding="utf-8"))
    return default


class StoryProfile(object):
    def __init__(self, root, characters, scenes, sequences, events, assets, policy):
        self.root = Path(root)
        self.characters = characters      # [{code, id, name, age, ...}]
        self.scenes = scenes              # [{scene_id, name}]
        self.sequences = sequences        # [{sequence_id, scene, target_duration, events_covered, ...}]
        self.events = events              # [event_id, ...] 按剧情顺序
        self.assets = assets              # {asset_id: 自然称呼}
        self.policy = policy              # {"music": "none"|"allowed", "max_pictures": 3}

    # ---------- 派生视图 ----------
    @property
    def speakers(self):
        """剧本里会出现的说话人名字（去掉括号别名）。"""
        return [c["name"] for c in self.characters]

    @property
    def role_names(self):
        return {c["code"]: c["name"] for c in self.characters}

    @property
    def role_subjects(self):
        """角色代号 → 官方 <Subject N> 标签，编号按角色表顺序稳定分配。"""
        return {c["code"]: "<Subject %d>" % (i + 1) for i, c in enumerate(self.characters)}

    @property
    def scene_subject_tag(self):
        """场景占用角色之后的下一个 Subject 编号。"""
        return "<Subject %d>" % (len(self.characters) + 1)

    @property
    def role_codes(self):
        return [c["code"] for c in self.characters]

    def character_by_code(self, code):
        for c in self.characters:
            if c["code"] == code:
                return c
        return None

    def scene_name(self, scene_id):
        for s in self.scenes:
            if s["scene_id"] == scene_id:
                return s["name"]
        return scene_id

    def sequence_scene(self, sequence_id):
        for s in self.sequences:
            if s["sequence_id"] == sequence_id:
                return s.get("scene")
        return None

    @property
    def id_map(self):
        """全部内部 ID → 对生成模型可读的自然称呼。"""
        m = {}
        for c in self.characters:
            m[c["id"]] = c["name"]
        for s in self.scenes:
            m[s["scene_id"]] = s["name"]
        for s in self.sequences:
            sid = s["sequence_id"]
            m[sid] = "%s段" % self.scene_name(s.get("scene") or sid)
            for e in s.get("events_covered", []):
                m.setdefault(e, "%s中的事件" % self.scene_name(s.get("scene") or sid))
        for eid, label in self._event_labels.items():
            m[eid] = label
        m.update(self.assets)
        return m

    # ---------- 从 data/ 项目数据构建 ----------
    @classmethod
    def from_story(cls, story_id):
        """唯一职责：把 data/ 里一个项目的数据推导成 StoryProfile，不读离线报告目录。"""
        from . import story_core, narrative_core, asset_core, store
        story = story_core.get_story(story_id)
        if not story:
            raise ValueError("故事不存在：" + str(story_id))
        ep = narrative_core.load_episode(story_id, story.get("current_episode") or 1)
        chars = asset_core.list_assets(story_id, "characters")
        scens = asset_core.list_assets(story_id, "scenes")
        visuals = asset_core.list_assets(story_id, "visuals")

        characters = []
        for i, c in enumerate(chars):
            cid = c.get("character_id", "")
            mo = _ROLE_CODE_RE.match(cid)
            code = mo.group(1) if mo else chr(ord("A") + i)
            characters.append({
                "code": code,
                "id": cid,
                "name": _ALIAS_RE.sub("", c.get("name", "")).strip(),
                "full_name": c.get("name", ""),
                "age": str(c.get("age", "")).strip(),
                "sex": c.get("sex", ""),
                "permanent_facts": c.get("permanent_facts", []),
            })

        scenes = [{"scene_id": s.get("scene_id"), "name": s.get("name", s.get("scene_id"))}
                  for s in scens]

        units = (ep or {}).get("units") or []
        sequences = []
        for i, u in enumerate(units, 1):
            sequences.append({
                "sequence_id": "SEQ_" + (u.get("unit_id") or ("UNIT_%03d" % i)),
                "scene": scenes[0]["scene_id"] if scenes else "",
                "target_duration": 15,
                "events_covered": u.get("must_include_event_ids") or [],
            })

        events = [e.get("event_id") for e in ((ep or {}).get("events") or [])]
        event_labels = {e.get("event_id"): (e.get("title") or e.get("desc") or "")[:24]
                        for e in ((ep or {}).get("events") or [])}

        assets = {}
        for v in visuals:
            owner = v.get("owner_id")
            label = "该资产"
            for c in characters:
                if c["id"] == owner:
                    label = c["name"] + " Master"
                    break
            if label == "该资产":
                for s in scenes:
                    if s["scene_id"] == owner:
                        label = s["name"] + " Master"
                        break
            assets[v.get("visual_id", "")] = label

        # 项目级默认策略：无背景音乐、每段参考图不超过 3 张（V4.3 红线）
        policy = {"music": "none", "max_pictures": 3}
        obj = cls(store.DATA / "stories" / (story_id + ".json"), characters, scenes,
                  sequences, events, assets, policy)
        obj._event_labels = event_labels
        obj.story = story
        obj.episode = ep
        return obj

    # ---------- 载入 ----------
    @classmethod
    def load(cls, review_dir):
        root = Path(review_dir)

        chars_raw = (_read(root / "05_characters/characters.json", {})
                     or _first_json(root / "05_characters", {}) or {})
        characters = []
        for i, c in enumerate(chars_raw.get("characters", [])):
            cid = c.get("character_id", "")
            mo = _ROLE_CODE_RE.match(cid)
            code = mo.group(1) if mo else chr(ord("A") + i)
            characters.append({
                "code": code,
                "id": cid,
                "name": _ALIAS_RE.sub("", c.get("name", "")).strip(),
                "full_name": c.get("name", ""),
                "age": str(c.get("age", "")).strip(),
                "sex": c.get("sex", ""),
                "permanent_facts": c.get("permanent_facts", []),
            })

        scenes_raw = (_read(root / "10_scenes/scenes.json", {})
                      or _first_json(root / "10_scenes", {}) or {})
        scenes = [{"scene_id": s.get("scene_id"), "name": s.get("name", s.get("scene_id"))}
                  for s in scenes_raw.get("scenes", [])]

        seq_raw = _read(root / "16_sequence_plan.json", {}) or {}
        sequences = seq_raw.get("sequences", [])

        event_raw = _read(root / "15_scene_event_plan.json", {}) or {}
        events, labels = [], {}
        for sc in event_raw.get("scenes", event_raw.get("events", [])):
            if isinstance(sc, dict) and sc.get("events"):
                for e in sc["events"]:
                    eid = e.get("event_id") if isinstance(e, dict) else e
                    events.append(eid)
                    if isinstance(e, dict):
                        labels[eid] = e.get("summary") or e.get("what") or eid
            elif isinstance(sc, dict) and sc.get("event_id"):
                events.append(sc["event_id"])
                labels[sc["event_id"]] = sc.get("summary") or sc.get("what") or sc["event_id"]
        if not events:
            for s in sequences:
                events += list(s.get("events_covered", []))

        assets = {}
        man = _read(root / "23_krea_asset_manifest.json", {}) or {}
        for a in man.get("assets", []):
            aid = a.get("asset_id", "")
            label = a.get("subject") or a.get("name") or ""
            if not label:
                label = "该资产"
            assets[aid] = label if label.endswith("Master") else "%s Master" % label

        audio = _read(root / "31_audio_plan.json", {}) or {}
        music_values = {str(s.get("music", "")).strip().upper() for s in audio.get("sequences", [])}
        policy = {
            "music": "none" if music_values and music_values <= {"N/A", ""} else "allowed",
            "max_pictures": 3,
        }

        obj = cls(root, characters, scenes, sequences, events, assets, policy)
        obj._event_labels = labels
        return obj

    _event_labels = {}
