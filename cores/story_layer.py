# -*- coding: utf-8 -*-
"""故事层：设定与剧本之间的枢纽，也是**唯一的故事事实源**。

五栏的关系：

    ⚙️ 设定      这个项目长什么样 + 整部作品计划（笼统说要讲什么）—— 生成的**输入**
    📖 故事      按计划真正写出来的完整故事 + 分集 + 事件账本 —— 生成的**产物**
    🎬 剧本分镜  某一集的剧本与分镜
    🗂 资源      保存下来的人设图/场景图/人设文字
    🎞 成品      视频 / 漫画 / 小说

板块是分开看的，底下是一条链：设定 → 故事 → 剧本 → 成品，资源横跨其间。
**分集是故事层的决策，不是剧本层的**：先定这故事分几集、每集讲到哪，才谈得上给某一集写剧本。

视频、漫画、小说三条线都从这里读故事，谁都不能各写一份。
下游只能展开这里的事件，不能新增剧情（`cores/text_checks.check_event_order` 会查）。
"""
from cores import store, story_core

__all__ = ["get_story_body", "save_story_body", "list_episodes", "save_episodes",
           "split_into_episodes", "event_ledger", "story_status"]

_KEY = "story_body"     # 完整故事正文
_EPS = "episodes"       # 分集
_EVENTS = "events"      # 事件账本


def _load(story_id):
    s = story_core.get_story(story_id)
    if not s:
        raise ValueError("故事不存在：" + str(story_id))
    return s


def _save(s):
    store.save_json(store.DATA / "stories" / (s["story_id"] + ".json"), s)
    return s


# ---------- 完整故事正文 ----------

def get_story_body(story_id):
    """完整故事正文。没有就返回空串——空态由界面提示"先去设定栏生成"。"""
    return str(_load(story_id).get(_KEY) or "")


def save_story_body(story_id, text):
    """保存正文。用户手改的正文是权威，后续自动生成不许覆盖它。"""
    s = _load(story_id)
    s[_KEY] = str(text or "")
    s.setdefault("story_body_edited", False)
    return _save(s)


def adopt_generated_body(story_id, text):
    """把生成出来的正文收进故事层。

    两道防线，都是踩过的坑：
      1. 用户手改过 → 不覆盖
      2. **新的比现有的短很多 → 不覆盖**。一键生成产出的是 200 字梗概，
         而完整正文有两千多字；梗概一写回去，辛苦生成的完整故事就没了。
         实测里 2302 字被 181 字的梗概冲掉过。
    """
    s = _load(story_id)
    new = str(text or "")
    old = str(s.get(_KEY) or "")
    if s.get("story_body_edited"):
        return {"ok": False, "note": "正文你已经手改过，自动生成不覆盖；要换请先清空再生成"}
    if old and len(new) < len(old) * 0.6:
        return {"ok": False,
                "note": "现有正文 %d 字，新的只有 %d 字，看着像梗概而不是完整故事，"
                        "没有覆盖。要换请先清空正文再生成" % (len(old), len(new))}
    s[_KEY] = new
    _save(s)
    return {"ok": True, "note": "完整故事已写入故事栏（%d 字）" % len(new)}


# ---------- 分集 ----------

def list_episodes(story_id):
    """分集列表：[{no, title, summary, status}]。status: 未开始/剧本完成/分镜完成。"""
    return list(_load(story_id).get(_EPS) or [])


def save_episodes(story_id, episodes):
    s = _load(story_id)
    out = []
    for i, e in enumerate(episodes or [], 1):
        out.append({"no": int(e.get("no") or i),
                    "title": str(e.get("title") or ("第%d集" % i)),
                    "summary": str(e.get("summary") or ""),
                    "status": e.get("status") or "未开始"})
    s[_EPS] = out
    _save(s)
    return out


def split_into_episodes(story_id, count=None):
    """把完整故事按自然段切成若干集的**草稿梗概**，不调模型。

    切法是保守的：只按段落均分并取每段首句当梗概，用户改完再保存。
    数量不指定时按正文长度估：每集约 600 字。
    """
    body = get_story_body(story_id)
    paras = [p.strip() for p in body.split("\n") if p.strip()]
    if not paras:
        return []
    n = int(count) if count else max(1, min(12, round(len(body) / 600) or 1))
    n = max(1, min(n, len(paras)))
    size = max(1, len(paras) // n)
    eps = []
    for i in range(n):
        chunk = paras[i * size:(i + 1) * size] if i < n - 1 else paras[i * size:]
        text = "".join(chunk)
        first = text.split("。")[0][:40]
        eps.append({"no": i + 1, "title": "第%d集" % (i + 1),
                    "summary": first + ("。" if first else ""), "status": "未开始"})
    return eps


# ---------- 事件账本 ----------

def event_ledger(story_id):
    """事件账本：按顺序，下游只能展开不能新增。"""
    return list(_load(story_id).get(_EVENTS) or [])


def save_event_ledger(story_id, events):
    s = _load(story_id)
    out = []
    for i, e in enumerate(events or [], 1):
        out.append({"event_id": e.get("event_id") or ("EVENT_%03d" % i),
                    "summary": str(e.get("summary") or ""),
                    "episode_no": e.get("episode_no")})
    s[_EVENTS] = out
    _save(s)
    return out


# ---------- 状态汇总（给界面显示这一栏够不够往下走） ----------

def story_status(story_id):
    """这一栏还缺什么，直接给人话。界面据此显示下一步该干嘛。"""
    s = _load(story_id)
    body = str(s.get(_KEY) or "")
    eps = list(s.get(_EPS) or [])
    events = list(s.get(_EVENTS) or [])
    missing = []
    if not body:
        missing.append("还没有完整故事——先去 ⚙️ 设定 点「按以上选择生成全部设定」")
    if body and not eps:
        missing.append("还没有分集——点「按故事分集」先出草稿，改完保存")
    if eps and not events:
        missing.append("还没有事件账本（不影响往下走，但视频连续性会少一层保障）")
    return {"has_body": bool(body), "body_chars": len(body),
            "episodes": len(eps), "events": len(events),
            "ready_for_script": bool(body and eps),
            "missing": missing}
