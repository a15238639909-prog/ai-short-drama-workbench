# -*- coding: utf-8 -*-
"""director_core.py — V4.2 Qwen Director Brain + 8 类 Benchmark（先过导演脑，再烧 GPU）。"""
import json, re, time

NL = chr(10)
from pathlib import Path
from cores import store

DIRECTOR_SYSTEM = """你是导演兼编剧。按固定岗位链处理一段场景：Story Architect → Screenwriter → Director Intent → Performance → Blocking → Camera → Editing → Sound → Shot Plan。
先写“故事/剧本层”（自然可读，禁止混入特写/广角/推镜等摄影词），再写导演意图，最后给 Shot Plan。
成熟黑暗叙事要求：允许性感张力（低频、有场景理由、推动人物或剧情，禁止每场重复同一手法）、恐怖信息差、暴力因果与后果；不得撰写露骨性行为或性器官细节。
Sensual Tension Budget（性感张力预算，硬性节奏控制）：
1. 全场景性感镜头占比不得超过 40%（9 镜时最多 3 镜带性感暗示）。
2. 任何两个性感镜头之间必须至少隔 1 个纯叙事镜头；禁止连续两镜使用同一种暗示。
3. 每处性感暗示必须有场景理由（天气/动作/服装/空间/关系/视线/危险/剧情），并在镜头叙事目的里写明。
4. 高强度关系转折（吻/拥抱/贴身/解衣等）前必须有至少 2 个镜头的铺垫，禁止开场即高强度。
5. 暗示之后镜头必须继续推进人物或剧情，不得变成连续无意义插图。
节奏示例（9 镜）：S01 环境/叙事 → S02 叙事 → S03 叙事 → S04 唯一一次低强度暗示（如指尖触碰/距离拉近）→ S05 叙事/反应 → S06 叙事 → S07 第二次暗示（换一种手法，如低语+视线）→ S08 叙事/关系变化 → S09 后果。
只输出一个合法 JSON：
{
  "story_script": "自然可读的剧本层（地点/时间/事件/关键对白，不含摄影词）",
  "director_intent": {"why": "这一场为什么存在", "audience_in": "进入前知道", "audience_out": "离开后多知道",
                       "who_wants": "谁想得到什么", "who_hides": "谁在阻止/隐藏", "power_shift": "主动权变化",
                       "emotion_from_to": "情绪从哪到哪", "decisive_moment": "最重要瞬间",
                       "hidden_until": "哪些信息必须隐藏到后面", "consequence": "本场产生的不可逆或长期后果"},
  "performance": ["可演行为（动作前身体状态/视线/停顿/手部动作/对反应/伤势保护等，不写表情标签）"],
  "blocking": {"start": "人物起始位置", "distances": "彼此距离", "facing": "朝向", "paths": "移动路径",
                "exits": "入口/出口", "key_objects": "关键物体位置", "end": "结束位置"},
  "shots": [
    {"shot_id": "S01", "duration": 4, "location": "这个镜头发生在哪个场景（原样写给定的场景名，不许自己造）", "narrative_purpose": "为什么这个镜头存在",
     "audience_information": "观众得到的信息", "event_ids": [],
     "framing": "景别/构图", "camera_position": "机位", "camera_angle": "角度",
     "lens_character": "镜头性格", "camera_movement": "运动（类型+幅度+速度）",
     "blocking": "镜头内调度", "performance": "镜头内表演", "action": "画面行动",
     "eye_line": "视线方向", "visual_focus": "视觉焦点", "dialogue": "对白或空",
     "sound": "声音", "continuity_in": "承接上一镜头", "continuity_out": "交下一镜头",
     "cut_reason": "为什么现在切（必须填写）"}
  ]
}
要求：**每个 Shot 都必须填 location，只能是我给你的场景名之一**——下游要按地点把镜头切成一段一段的视频，一段视频里只能有一个地点；缺了它，切出来的一段会在街头和卧室之间来回跳。故事里若需要一个我没给场景卡的地方（例如咖啡馆），归到最接近的已有场景，不要凭空造新地点名。Shot 的 cut_reason 必须有具体叙事理由；每镜头承担新信息/反应/空间/情绪/节奏/伏笔/转场之一；换拍法不改 Event。"""

BENCHMARKS = [
    ("01_double_conversation", "双人安静对话：雨夜古堡书房，艾达与卡恩试探对方底线，潜台词丰富，不过度切镜。"),
    ("02_secret_reveal", "秘密揭露：卡恩发现艾达一直在用谎言维持灰腐病的假象，信息何时给观众是关键。"),
    ("03_chase_combat", "高强度追逐/战斗：感染者冲入回廊，卡恩断臂持剑掩护艾达撤离，准备→接触→结果。"),
    ("04_power_negotiation", "三人权力谈判：艾达、卡恩与庄园管家三方就瘟疫疫苗线索谈判，站位/视线/权力变化。"),
    ("05_environment_explore", "复杂环境探索：艾达独自深入古堡地下墓穴寻找旧祭坛，空间建立与路径清晰。"),
    ("06_horror_unknown", "恐怖未知危险：灰腐病感染体在黑暗中逼近，信息差、声音、等待、揭示。"),
    ("07_dark_consequence", "黑暗后果戏：卡恩的断臂在战斗中再度受创，艾达不得不亲手结束一名被感染的旧仆从，情绪与剧情后果。"),
    ("08_sensual_tension", "成熟人物性感张力：宴会散场后两人独处，低频暗示、欲望积累、关系升级，但保持非露骨。"),
]

# JSON 提取统一走 cores.model_json（自动修 + 重试）。
# 原来这里是"解析失败就 raise"，temperature 高一点模型偶尔多打一个逗号，
# 整条链就断在半路——用户的规矩是「拦截是设计失败」。
def _extract_json(text):
    from cores.model_json import extract
    d = extract(text)
    if d is None:
        raise ValueError("导演输出不是 JSON")
    return d


def run_director_scene(premise, characters="艾达（贵族之女）、卡恩（断臂猎人）、旧仆从",
                       scenes="", temperature=0.7):
    from models import qwen_client
    user = ("人物：%s" + NL + "可用场景（location 只能从这些里面选）：%s" + NL +
            "场景任务：%s" + NL +
            "请按导演链输出完整 JSON（含故事/剧本层、导演意图、表演、调度、Shot Plan）。"
            ) % (characters, scenes or "（未提供，location 一律写「未指定」）", premise)
    from cores.model_json import chat_json

    allowed = {x.strip() for x in str(scenes or "").replace("、", ",").split(",") if x.strip()}

    def _need(x):
        if not str(x.get("story_script") or "").strip():
            raise ValueError("缺 story_script（剧本正文）")
        if not isinstance(x.get("shots"), list) or not x["shots"]:
            raise ValueError("缺 shots（分镜表）")
        # 没有 location，下游按时长硬切就会让一段视频里同时出现街头和卧室——实测踩过
        miss = [sh.get("shot_id") for sh in x["shots"] if not str(sh.get("location") or "").strip()]
        if miss:
            raise ValueError("这些镜头没写 location：%s。每个镜头都要写明发生在哪个场景"
                             % "、".join(str(m) for m in miss[:5]))
        # 光"填了"不够，还得是**给定场景之一**。填了个不存在的地点，
        # 下游找不到对应的场景定义和参考图，模型只能自己编一个地方——实测踩过。
        if allowed:
            bad = sorted({str(sh.get("location")).strip() for sh in x["shots"]
                          if str(sh.get("location")).strip() not in allowed})
            if bad:
                raise ValueError("这些 location 不在我给的场景里：%s。只能用这几个：%s"
                                 % ("、".join(bad[:4]), "、".join(sorted(allowed))))

    d, raw = chat_json(
        DIRECTOR_SYSTEM, user,
        lambda sysmsg, usr, **kw: qwen_client.chat_for("director", sysmsg, usr, **kw),
        validate=_need, tries=3, temperature=temperature)
    # 结构检查：cut_reason 必填；故事层不含摄影词
    camera_terms = ["特写", "广角", "推镜", "摇镜", "机位", "景别", "构图", "镜头", "运镜"]
    story_ok = not any(t in d.get("story_script", "") for t in camera_terms)
    for sh in d["shots"]:
        if not str(sh.get("cut_reason") or "").strip():
            raise ValueError("Shot 缺少 cut_reason")
    return d, {"story_layer_clean": story_ok, "shots": len(d["shots"]),
               "cut_reasons_ok": all(str(sh.get("cut_reason") or "").strip() for sh in d["shots"])}

def run_benchmarks(outdir=None, character_line=None):
    from models import qwen_client
    outdir = outdir or (Path(__file__).resolve().parent.parent / "reports" / "Director_Benchmarks")
    outdir.mkdir(parents=True, exist_ok=True)
    results = []
    for key, premise in BENCHMARKS:
        try:
            d, check = run_director_scene(premise, character_line or "艾达（贵族之女）、卡恩（断臂猎人）、旧仆从")
            bdir = outdir / key
            bdir.mkdir(parents=True, exist_ok=True)
            (bdir / "01_故事剧本.md").write_text("# 故事/剧本层\n\n" + d["story_script"], encoding="utf-8")
            (bdir / "02_导演方案.md").write_text("# 导演方案\n\n" + json.dumps(d["director_intent"], ensure_ascii=False, indent=2), encoding="utf-8")
            (bdir / "03_分镜表.md").write_text("# Shot Plan\n\n" + json.dumps(d["shots"], ensure_ascii=False, indent=2), encoding="utf-8")
            check_json = {"key": key, "pass": check["story_layer_clean"] and check["cut_reasons_ok"],
                          "story_layer_clean": check["story_layer_clean"], "shots": check["shots"],
                          "cut_reasons_ok": check["cut_reasons_ok"]}
            (bdir / "04_结构化检查.json").write_text(json.dumps(check_json, ensure_ascii=False, indent=2), encoding="utf-8")
            results.append({"key": key, "pass": check_json["pass"], "shots": check["shots"]})
            print("BENCH", key, "PASS" if check_json["pass"] else "FAIL", "shots=", check["shots"])
        except Exception as e:
            results.append({"key": key, "pass": False, "error": str(e)[:200]})
            print("BENCH", key, "ERROR", str(e)[:160])
    gate = all(r.get("pass") for r in results)
    (outdir / "_gate_result.json").write_text(json.dumps({"gate_pass": gate, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    return gate, results
