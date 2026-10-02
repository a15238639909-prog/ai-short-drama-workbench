# -*- coding: utf-8 -*-
"""P366 口述剧情模式 + 节拍切段：零模型测试（V41_NO_MODEL=1）。"""
import io, os, sys
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("V41_NO_MODEL", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cores import oral_story as os_, authoring as au

_ok = _n = 0


def T(name, cond, extra=""):
    global _ok, _n
    _n += 1
    _ok += bool(cond)
    print(("  ✔ " if cond else "  ✘ ") + name + ("" if cond else "　" + str(extra)[:200]))


BRIEF = ("一个少年家境贫寒。一次偶然的机会，他救了一个老者。老者其实是一个门派里比较厉害的人物，被人偷袭，身负重伤，被少年救了下来。"
         "少年把他藏起来，好好养伤。养完伤之后，老者说他想报答少年：第一，给他一些钱财，让他吃穿不愁；第二，什么也不给，但可以让他进入他们的门派修炼。"
         "少年觉得吃穿不愁还是会受欺负，那还是去修炼吧。然后他就上门派去修炼了。")

print("【事件卡】")
ev = os_.event_card(BRIEF)
T("口述断成 5～9 件事", 5 <= len(ev) <= 9, [e["text"][:20] for e in ev])
T("编号从 1 起连续", [e["n"] for e in ev] == list(range(1, len(ev) + 1)))
T("去掉「然后」口头语", not any(e["text"].startswith("然后") for e in ev), [e["text"][:10] for e in ev])
T("原话不改写：每件事都是口述的子串", all(e["text"].rstrip("。") in BRIEF for e in ev))
ex = os_.writer_extras(ev, "紧凑")
T("篇幅按事件数×节奏", "篇幅" in ex and "字" in ex["篇幅"] and int(ex["篇幅"].split("～")[0].replace("约", "")) < 180 * len(ev))
T("止于最后一件事", ("事件 %d 写完就结束" % len(ev)) in ex["本话止于"])

print("【程序核对（不调模型）】")
prose = "\n\n".join(["林岩上山砍柴，在草丛里发现一个受伤的老者。", "他把老者背回破屋，藏起来养伤，喂了三日的药。",
                     "老者伤愈，说自己是青云门的人。他拿出五十两银子和一块令牌，让林岩选。", "林岩说钱花完还是受欺负，选修炼。",
                     "林岩收拾行囊，跟着老者上路。", "到了山门，弟子登记他为外门弟子，明日到演武场。", "第二天演武场上，他领了道袍。"])
mapping = {1: [1], 2: [2], 3: [3], 4: [4], 5: [5]}
ev5 = [{"n": i + 1, "text": t} for i, t in enumerate(["少年救了老者", "藏起来养伤", "报答两条路", "选修炼", "上门派去了"])]
rep = os_.check_prose(prose, ev5, BRIEF, mapping, [{"name": "林岩", "role": "少年"}, {"name": "弟子", "role": "其他"}], [6, 7])
T("多出的数字抓到：五十两、三日", "五十两" in rep["numbers"] and "三日" in rep["numbers"], rep["numbers"])
T("口述里有的数不算：事件卡「一些钱财」没数字，不误报", all(x not in ("一些",) for x in rep["numbers"]))
T("越界段记下", rep["beyond"] == [6, 7])
T("顺序对", rep["order_ok"])
rep_m = os_.check_prose(prose, ev5, BRIEF, {1: [1], 2: [2], 3: [3], 4: [], 5: [5]}, [], [])
T("缺事件抓到", rep_m["missing"] == [4] and rep_m["verdict"] == "fail")
# 砍越界：fidelity_pass 里 q 只在映射/修订时用；这里用假 q 返回固定映射
def _fake_q(sysm, user, mt=0, temperature=0):
    return '{"map": {"1": [1], "2": [2], "3": [3], "4": [4], "5": [5]}, "people": [{"name": "林岩", "role": "少年"}], "beyond": [6, 7]}'
p2, r2 = os_.fidelity_pass(prose, ev5, _fake_q, brief=BRIEF)
T("越界两段被砍掉（第 6、7 段）", "演武场" not in p2 and "跟着老者上路" in p2, p2[-60:])
T("砍掉的段号记进报告", r2.get("cut_paragraphs") == [6, 7], r2.get("cut_paragraphs"))
T("数字问题保留在报告里（修订要模型，这里假 q 不修）", "五十两" in r2["numbers"] and r2["verdict"] == "fail")

print("【节拍表】")
scenes, chars = ["苍翠山道", "猎屋内", "小屋门口"], ["张小凡", "青云掌门"]
raw = "\n".join([
    "拍1｜苍翠山道｜张小凡｜拨开草丛发现倒地的老者｜无｜无｜全景",
    "拍2｜苍翠山道｜张小凡｜蹲下探老者鼻息｜睁开眼抓住他手腕｜青云掌门：多谢。｜特写",
    "拍3｜苍翠山道｜张小凡｜背起老者往山下走｜无｜无｜中景",
    "拍4｜猎屋内｜张小凡｜拧干布巾擦老者肩上的血｜皱眉｜无｜中景",
    "拍5｜猎屋内｜青云掌门｜坐起说明身份｜张小凡直起身｜青云掌门：我是青云门的人，遭人暗算才落到这般田地，你救了我，报答你有两样，一是银钱吃穿不愁，二是带你进门修炼。｜特写",
    "拍6｜猎屋内｜环境｜晨光照进屋内｜无｜无｜全景",
    "拍7｜小屋门口｜张小凡｜背着包袱跟老者走出门｜无｜无｜全景",
])
beats = os_.parse_beats(raw, scenes, chars)
T("解析 6 拍（环境拍被扔掉）", len(beats) == 6, len(beats))
sl = os_.normalize_beats(beats, chars, "适中")
acts = [s["text"].splitlines()[1] for s in sl]
T("背起拆成两拍：准备 + 已在背上", any("还没背起来" in a for a in acts) and any("背在背上" in a for a in acts), acts)
T("长台词按句拆成多拍，句子不在逗号处断", all(not s["text"].splitlines()[-1].rstrip().endswith("，") for s in sl if "青云掌门：" in s["text"]) and sum("青云掌门：" in s["text"] for s in sl) >= 2, [s["text"].splitlines()[-1] for s in sl if "青云掌门：" in s["text"]])
T("每拍 4～12 秒（P392/P404：按内容和档位算）", all(4 <= s["seconds"] <= 12 for s in sl), [s["seconds"] for s in sl])
T("第一拍和换地方的拍标 establish + cut", sl[0]["establish"] and sl[0]["cut"] and any(s["establish"] for s in sl[1:] if s["scene_hint"] == "猎屋内"))
T("同地同人同景别的连续拍不硬切", any(not s["cut"] for s in sl[1:]), [s["cut"] for s in sl])
T("拍文本带地点行和动作句，台词单独一行", all(s["text"].startswith("── ") for s in sl) and all(("：" in s["text"].splitlines()[-1]) == bool("多谢" in s["text"] or "青云掌门：" in s["text"]) for s in sl))
T("反应句补主语", any(s["text"].splitlines()[1].startswith("张小凡蹲下探老者鼻息。青云掌门睁开眼") for s in sl), acts[:3])
T("scene_hint = 地点", {s["scene_hint"] for s in sl} <= set(scenes))

print("【台词编号 / 并拍 / 别名 / 背包袱】")
pics = "阿青拨开草丛。\n阿青：老人家？\n老者睁眼。\n老者：多谢。\n阿青背起包袱走向官道。"
script, D = os_.numbered_script(pics)
T("台词编号成 D1/D2", len(D) == 2 and "【D1】阿青：老人家？" in script and D[1] == ("老者", "多谢。"))
al = os_.aliases_of(["阿青", "老者（云崖门长辈）"])
T("别名：短名映射到全名", al.get("老者") == "老者（云崖门长辈）" and os_.canon("老者睁眼", al) == "老者（云崖门长辈）")
raw2 = "拍1｜后山｜阿青｜拨开草丛发现老者｜无｜D1｜全景\n拍2｜后山｜老者（云崖门长辈）｜睁眼｜无｜无｜特写\n拍3｜北边的官道｜阿青｜背起包袱走向官道｜无｜无｜全景"
b2 = os_.parse_beats(raw2, ["后山", "北边的官道"], ["阿青", "老者（云崖门长辈）"], D, al)
b2 = os_.ensure_dialogue(b2, D, al)
T("没用到的 D2 补成单独一拍，插在说话人（老者）第一次动作那拍之后", [b.get("_d") for b in b2] == [1, None, 2, None] and b2[2]["say"] == "老者：多谢。" and b2[2]["who"] == "老者（云崖门长辈）", [(b.get("_d"), b["say"]) for b in b2])
sl2 = os_.normalize_beats(b2, ["阿青", "老者（云崖门长辈）"], "紧凑")
T("背起包袱不拆两拍", len(sl2) == 4 and not any("还没背起来" in x["text"] for x in sl2), [x["text"].splitlines()[1] for x in sl2])
T("拍文本里人名用短名", all("云崖门长辈" not in x["text"] for x in sl2))
T("台词行说话人是短名", any(x["text"].endswith("老者：多谢。") for x in sl2))
b3 = [{"place": "屋", "who": "阿青", "act": "掰饼", "react": "", "say": "", "shot": "中景", "_d": None},
      {"place": "屋", "who": "阿青", "act": "舔手", "react": "", "say": "", "shot": "中景", "_d": None},
      {"place": "屋", "who": "阿青", "act": "推门", "react": "", "say": "", "shot": "全景", "_d": None},
      {"place": "山", "who": "阿青", "act": "找草", "react": "", "say": "", "shot": "中景", "_d": None}]
b3 = os_.merge_small(b3, 2)
T("超上限时并同地同人无台词的相邻拍", len(b3) == 2 and b3[0]["act"] == "掰饼，舔手，推门", [b["act"] for b in b3])

print("【口述拍法（模式二）】")
raw_s = "拍1｜森林｜无｜森林全景，晨雾｜无｜无｜全景\n拍2｜森林｜少年｜背着竹筒入画，回头往家走｜无｜无｜中景\n拍3｜森林｜少年｜过去把老人搀扶起来｜老人睁眼｜少年：老师傅你怎么了？｜中景\n拍4｜房间｜无｜烛火在桌角跳动｜无｜无｜特写"
bs = os_.parse_beats_loose(raw_s, ["森林", "房间"], ["少年", "老人"])
T("口述拆拍：空镜谁为空、台词按原话", len(bs) == 4 and bs[0]["who"] == "" and bs[2]["say"] == "少年：老师傅你怎么了？", [(b["who"], b["say"]) for b in bs])
sls = os_.normalize_beats(bs, ["少年", "老人"], "适中")
T("口述拆拍：换地方硬切、搀扶拆两拍", sls[-1]["cut"] and sls[-1]["establish"] and any("还没扶起来" in x["text"] for x in sls), [x["text"].splitlines()[1] for x in sls])
T("story_mode=shots 走节拍", au.beat_mode({"story_mode": "shots"}))
# P382：地点泛称对卡、反应栏只有名字当无、做什么抄台词改成说话
raw_p = "拍1｜森林｜阿青｜背着竹筒入画｜无｜无｜全景\n拍2｜森林｜阿青｜过去把老者搀扶起来｜老者｜阿青：老师傅，你怎么了？｜中景\n拍3｜房间｜阿青｜说：没关系，你在我这好好养伤｜老者｜阿青：没关系，你在我这好好养伤。｜中景\n拍4｜房间｜老者｜表情特写｜阿青皱眉｜老者：非常感谢｜特写"
bp = os_.parse_beats_loose(raw_p, ["后山", "破屋"], ["阿青", "老者"])
T("反应栏只有名字→无；有内容的保留", bp[1]["react"] == "" and bp[3]["react"] == "阿青皱眉", [b["react"] for b in bp])
T("做什么抄了台词→说话", bp[2]["act"] == "说话", bp[2]["act"])
_calls = []
def _pick_q(sysm, user, mt=0, temperature=0):
    _calls.append(user); return "后山" if "森林" in user else "破屋"
cards = [{"name": "后山", "contract_text": "室外后山。远景是雾中远山。"}, {"name": "破屋", "contract_text": "室内破屋。中景是灶台。"}]
bp = os_.resolve_places(bp, cards, _pick_q)
T("森林/房间 → 后山/破屋（类别唯一，不用问模型）", [b["place"] for b in bp] == ["后山", "后山", "破屋", "破屋"] and len(_calls) == 0, ([b["place"] for b in bp], len(_calls)))
T("模型答不上就沿用上一拍", [b["place"] for b in os_.resolve_places([{"place": "后山"}, {"place": "河边"}], cards, lambda *a, **k: "不知道")] == ["后山", "后山"])
T("指令词里带卡名和描述", "后山（室外后山，远景是雾中远山）" in os_.dictation_instruction(cards, ["阿青"]))
# P384：称呼别名 / 地点类别 / 空镜
_cc = [{"name": "阿青", "sex": "男", "age": "17"}, {"name": "老者", "sex": "男", "age": "60"}]
_ra = os_.role_aliases(_cc)
T("少年/老师傅/老人 按年龄性别对到卡", _ra.get("少年") == "阿青" and _ra.get("老师傅") == "老者" and _ra.get("老人") == "老者" and "老者" not in _ra, _ra)
T("两张卡都符合的称呼不算", "孩子" not in os_.role_aliases([{"name": "甲", "age": "12"}, {"name": "乙", "age": "15"}]))
T("指令词列「阿青（男，17岁）」", "阿青（男，17岁）" in os_.dictation_instruction(cards, _cc))
T("地点类别：草棚是棚、后山是山林、房间是屋", os_.place_class("山脚的废草棚") == "棚" and os_.place_class("后山") == "山林" and os_.place_class("房间") == "屋" and os_.place_class("森林") == "山林")
_c4 = [{"name": "北边的官道", "contract_text": "室外官道"}, {"name": "后山", "contract_text": "室外后山"}, {"name": "山脚的废草棚", "contract_text": "室外废草棚。远景是晨雾中的树林"}, {"name": "破屋", "contract_text": "室内破屋"}]
_bp4 = os_.resolve_places([{"place": "森林"}, {"place": "森林"}, {"place": "房间"}], _c4, lambda *a, **k: "山脚的废草棚")
T("森林→后山（类别唯一，不问模型）、房间→破屋", [b["place"] for b in _bp4] == ["后山", "后山", "破屋"], [b["place"] for b in _bp4])
_raw4 = "拍1｜森林｜无｜画面（晨雾中的树林远景）｜无｜无｜全景\n拍2｜森林｜少年｜背着竹筒入画｜无｜无｜全景\n拍3｜森林｜少年｜问｜老人｜少年：老师傅，你怎么了？｜中景"
_al4 = os_.aliases_of(["阿青", "老者"]); _al4.update(os_.role_aliases(_cc))
_b4 = os_.parse_beats_loose(_raw4, ["后山"], ["阿青", "老者"], _al4)
T("谁＝少年 → 阿青；台词说话人也换成卡名；空镜谁为空", _b4[0]["who"] == "" and _b4[1]["who"] == "阿青" and _b4[2]["say"] == "阿青：老师傅，你怎么了？", [(b["who"], b["say"]) for b in _b4])
_sl4 = os_.normalize_beats(_b4, ["阿青", "老者"], "适中")
T("空镜拍文本没有人名、who 为空", _sl4[0]["who"] == "" and "阿青" not in _sl4[0]["text"] and _sl4[1]["text"].splitlines()[1].startswith("阿青背着竹筒入画"), _sl4[0]["text"])

print("【P404 节奏】")
_pb = [{"place": "酒馆", "who": "林岩", "act": "端起酒壶给对面倒酒", "react": "", "say": "林岩：喝一口暖暖身子。", "shot": "中景", "_d": None},
       {"place": "酒馆", "who": "老者", "act": "接过酒杯", "react": "", "say": "老者：多谢。", "shot": "中景", "_d": None},
       {"place": "酒馆", "who": "林岩", "act": "说话", "react": "", "say": "林岩：你伤得不轻。", "shot": "特写", "_d": None},
       {"place": "后山", "who": "林岩", "act": "冲上去一把拉住老者往后退", "react": "", "say": "", "shot": "中景", "_d": None},
       {"place": "后山", "who": "林岩", "act": "站在原地", "react": "", "say": "", "shot": "中景", "_d": None},
       {"place": "后山", "who": "林岩", "act": "躲开落下的石块", "react": "", "say": "", "shot": "中景", "_d": None},
       {"place": "后山", "who": "老者", "act": "说话", "react": "", "say": "老者：快走！", "shot": "中景", "_d": None},
       {"place": "后山", "who": "林岩", "act": "慢慢来，蹲下看老者的伤口", "react": "", "say": "", "shot": "中景", "_d": None}]
T("判档：有台词没硬动作→慢，冲/拉/躲→快，口述「慢慢来」最高", os_.beat_pace(_pb[0]) == "慢" and os_.beat_pace(_pb[3]) == "快" and os_.beat_pace(_pb[7]) == "慢", [os_.beat_pace(b) for b in _pb])
_ps = os_.normalize_beats(_pb, ["林岩", "老者"], "适中")
_txt = [x["text"].replace("\n", " / ") for x in _ps]
T("慢档：酒馆三拍并成一段，三句台词，8～12 秒，不切", len([x for x in _ps if x["scene_hint"] == "酒馆"]) == 1 and _ps[0]["text"].count("：") >= 3 and 8 <= _ps[0]["seconds"] <= 12, (_txt[0], _ps[0]["seconds"]))
_fast = [x for x in _ps if x["scene_hint"] == "后山" and x["pace"] == "快"]
T("快档：填充拍「站在原地」删掉、短台词并进动作拍、4～6 秒、硬切", all(4 <= x["seconds"] <= 6 and x["cut"] for x in _fast) and not any("站在原地" in x["text"] for x in _ps) and any("老者：快走！" in x["text"] for x in _fast), _txt)
T("快档连续同景别同人自动换景别", len(_fast) >= 2 and _fast[0]["shot"] != _fast[1]["shot"], [x["shot"] for x in _fast])
T("口述提示词从拍文本里去掉", not any("慢慢来" in x["text"] for x in _ps) and any("蹲下看老者的伤口" in x["text"] for x in _ps), _txt)
T("切片冻结带 pace", "pace" in au._slicing_record("x", _ps)["slices"][0])
T("快档守卫删保持/静止句", "保持姿势" not in au.scrub_clauses("0—3秒：他挥拳砸向门板，保持姿势不动，木屑飞溅。", au._HOLD_WORDS))
T("慢档守卫删这一拍没写的走位、写了的留", au.scrub_clauses("0—3秒：她递过酒杯，转身走向门口。", au._MOVE_WORDS, keep_if_in="她递过酒杯") == "0—3秒：她递过酒杯，" and "走向门口" in au.scrub_clauses("0—3秒：她递过酒杯，转身走向门口。", au._MOVE_WORDS, keep_if_in="她走向门口"))
_wx = os_.writer_extras([{"n": 1, "text": "x"}], "舒缓")
T("写手规则带讲法", any("文戏为主" in str(v) for v in _wx.values()) and "篇幅" in _wx)

print("【链路开关】")
T("单话项目默认节拍模式", au.beat_mode({"story_mode": "single"}) and not au.beat_mode({"story_mode": "split"}))
T("slice_mode 可强制", au.beat_mode({"story_mode": "split", "slice_mode": "beat"}) and not au.beat_mode({"story_mode": "single", "slice_mode": "time"}))
T("冻结记录带节拍字段", set(("shot", "establish", "cut", "who")) <= set(au._slicing_record("x", sl)["slices"][0].keys()))
_ins = au._h3_timeline_body.__code__.co_consts
T("H3 指令词里有「本段是一拍」", any(isinstance(c, str) and "本段是一拍" in c for c in _ins))
_card = {"layout": "灶台前｜阿青掰饼、舔指尖｜在镇场主体正前方一步｜地面是夯土\n门口｜阿青推门离开｜在镇场主体正前方五步｜门槛外是泥地"}
T("节拍段的戏区名单不带「哪场戏」", au.scene_layout_names(_card, beat=True) == ["灶台前｜在镇场主体正前方一步", "门口｜在镇场主体正前方五步"], au.scene_layout_names(_card, beat=True))
T("非节拍段照旧带戏", "掰饼" in au.scene_layout_names(_card)[0])
_b = "【机位】中景固定机位，平视角度，手持微晃。\n\n0—3秒：阿青入画。\n\n这一段结束时：\n· 景别：中景\n· 阿青：门口｜面朝灶台｜手里无｜站立｜画面左侧"
_fx = au.beat_fix_shot(_b, "全景")
T("【机位】和结尾景别改成这一拍的景别", _fx.startswith("【机位】全景固定机位") and "· 景别：全景" in _fx and "中景" not in _fx, _fx[:80])
T("机位行没写景别就补在前面", au.beat_fix_shot("【机位】平视，固定。", "特写").startswith("【机位】特写，平视"))
_e = "【机位】全景，固定。\n\n0—3秒：森林全景，雾在岩石间流动。画面中暂无人物。\n\n3—6秒：阿青背着竹筒从画面左侧入画，步伐稳健。雾更浓了。\n\n这一段结束时：\n· 景别：全景\n· 阿青：岩石前｜面朝右｜竹筒｜站立｜画面左侧\n· 正在进行：雾在流动"
_ed = au.beat_drop_people(_e, ["阿青", "老者"])
T("空镜拍删掉带人的短句和人物行", "阿青" not in _ed and "雾更浓了" in _ed and "· 景别：全景" in _ed and "正在进行" in _ed, _ed)
T("空镜拍整块删空时留一句环境", "没有人" in au.beat_drop_people("0—3秒：阿青走进来。", ["阿青"]))
from cores import seam_v2 as _s2
_sb = _s2.seam_brief({"beat": True, "prev_script": ["阿青已经把老者扶起来站稳，手仍托着"], "done": []}, ["阿青", "老者"])
T("节拍段接缝：已做完不是禁词", "不许再演" not in _sb and "托着" in _sb and "已经做完的事" in _sb, _sb[:200])
# P386
_cc2 = [{"name": "阿青"}, {"name": "老者"}]
_fa, _ren = au.fix_body_aliases("阿青（S1）面朝老者（S2），半躺在阿青（S1）怀中。阿离（S1）抬头。", _cc2)
T("「面朝老者（S2）」不当陌生名字；真化名照改", "面朝老者" in _fa and "躺在阿青" in _fa and "阿离" not in _fa, (_fa, _ren))
_pb = "【机位】全景。\n\n0—3秒：阿青背着竹筒从右侧入画，走到岩石前，身体重心下沉，准备坐下。\n\n3—6秒：阿青在岩石凹陷处坐下，目光平视远山。竹筒背在身后。\n\n这一段结束时：\n· 景别：全景\n· 阿青：岩石前｜面朝远山｜竹筒｜坐直｜画面左侧\n· 正在进行：阿青凝视远山"
_pf = au.beat_drop_alien_poses(_pb, "── 后山 ──\n阿青背着竹筒入画。", "", ["阿青"])
T("编的坐下删掉、结尾姿势改回站立", "坐下" not in _pf and "竹筒背在身后" in _pf and "｜站立｜" in _pf, _pf)
_pf2 = au.beat_drop_alien_poses(_pb, "── 后山 ──\n阿青坐下歇脚。", "", ["阿青"])
T("这一拍写了坐下就不动", _pf2 == _pb)
T("上一段躺着的人本段躺着不算编", "躺在" in au.beat_drop_alien_poses("0—3秒：老者躺在地上。", "老者昏迷。", "· 老者：地上｜面朝天｜无｜仰躺｜画面右侧", ["老者"]))
_sc = au.beat_scrub("【机位】中景。\n\n阿青说：<Subject 1> (S1) says:<d>[Chinese]你好。</d>\n\n0—3秒：阿青开口。\n\n3—6秒：阿青看着。", ["阿青说：<Subject 1> (S1) says:<d>[Chinese]你好。</d>"], "室外后山")
T("块前的台词行挪进第一块", _sc.index("0—3秒") < _sc.index("says:<d>") < _sc.index("3—6秒"), _sc)
T("室外删窗户/天花板句", "天花板" not in au.beat_scrub("0—3秒：老者脸朝天花板方向，头微侧。", [], "室外后山。远景是雾中远山。"))
_ep = au.beat_drop_people("0—3秒：烛火在桌角跳动。面朝门口方向，正跨入门槛；四肢下垂。", ["阿青"])
T("空镜删没名字的身体句", "跨入" not in _ep and "烛火在桌角跳动" in _ep, _ep)
# P387
_ps = "· 阿青：岩石前｜面朝老者｜柴刀｜站立｜画面右侧\n· 老者：岩石凹陷处｜面朝天｜无｜平躺｜画面左侧"
_gs = au.beat_pose_state("── 后山 ──\n阿青已经把老者扶起来站稳，手仍托着。", _ps, ["阿青", "老者"])
T("「把老者扶起来站稳」→ 守卫状态里老者＝站立", au.tl_pose_of(_gs, "老者") == "站" and au.tl_pose_of(_gs, "阿青") == "站", _gs)
_gs2 = au.beat_pose_state("── 后山 ──\n阿青已经把老者背在背上，直起身往前走。", _ps, ["阿青", "老者"])
T("「背在背上」→ 老者不再判躺", au.tl_pose_of(_gs2, "老者") == "" and "被背着" in _gs2, _gs2)
T("没写姿势变化就不改", au.beat_pose_state("── 后山 ──\n阿青问对方。", _ps, ["阿青", "老者"]) == _ps)
_body7 = "【机位】中景。\n\n0—3秒：老者站在阿青身前，双腿微颤。阿青双手托着。\n\n这一段结束时：\n· 景别：中景"
T("扶起来站稳的拍里「老者站」不再被改成躺", au.tl_pose_conflicts(_body7, ["阿青", "老者"], _gs) == [] and au.tl_pose_conflicts(_body7, ["阿青", "老者"], _ps) != [])
_sb2 = _s2.seam_brief({"beat": True, "next_head": "阿青背着竹筒入画"}, ["阿青"])
T("节拍段接缝不给下一拍开头、不写接力帧", "接力帧" not in _sb2 and "竹筒" not in _sb2 and "最后一个动作" in _sb2, _sb2)
T("空镜删步伐/竹筒句", "竹筒" not in au.beat_drop_people("0—3秒：雾在流动。步伐稳健地走向岩石群，竹筒斜背于身后。", ["阿青"]))
# P389：占位拍并回、多人谁、景别词开头、反应栏括号
_raw9 = ("拍1｜林间古桥｜莉娅｜入画，跑到桥中间停下｜无｜无｜中景\n"
         "拍2｜林间古桥｜莉娅｜做什么（无具体动作描述，接上一拍）｜艾登（喘气）｜莉娅：快点！｜中景\n"
         "拍3｜林间古桥｜莉娅、艾登｜两人一起转身，望向树林｜无｜无｜全景\n"
         "拍4｜遗迹大门｜莉娅｜特写莉娅的脸，眼睛亮起来｜无｜无｜中景\n"
         "拍5｜遗迹大门｜艾登｜做什么（无具体动作描述，接上一拍）｜无｜艾登：等等——｜中景\n"
         "拍6｜遗迹石厅｜无｜转场，画面（阳光照在石台上）｜无｜无｜全景")
_b9 = os_.parse_beats_loose(_raw9, ["林间古桥", "遗迹大门", "遗迹石厅"], ["莉娅", "艾登"])
T("占位台词拍并回上一拍", len(_b9) == 5 and _b9[0]["say"] == "莉娅：快点！" and _b9[0]["react"] == "艾登喘气", [(b["act"], b["say"], b["react"]) for b in _b9])
T("并不回去的占位拍→说话", _b9[3]["act"] == "说话" and _b9[3]["say"] == "艾登：等等——", _b9[3])
T("多人谁：做什么前写两个名字", _b9[1]["act"].startswith("莉娅和艾登一起转身") and _b9[1]["who"] == "莉娅", _b9[1]["act"])
T("景别词开头去掉并进景别栏", _b9[2]["shot"] == "特写" and _b9[2]["act"].startswith("莉娅的脸"), _b9[2])
T("转场/画面（）剥掉", _b9[4]["act"] == "阳光照在石台上" and _b9[4]["who"] == "", _b9[4]["act"])
_sl9 = os_.normalize_beats(_b9, ["莉娅", "艾登"], "适中")
T("拍文本不出现「莉娅莉娅」「莉娅两人」", not any("莉娅莉娅" in x["text"] or "莉娅两人" in x["text"] for x in _sl9) and any("莉娅和艾登一起转身" in x["text"] for x in _sl9), [x["text"].splitlines()[1] for x in _sl9])
# P390：谁＝无但有人
_raw10 = ("拍1｜遗迹大门｜艾登｜伸出手摸门上的符文｜无｜无｜中景\n"
          "拍2｜遗迹大门｜无｜特写手和亮起的符文｜无｜无｜特写\n"
          "拍3｜遗迹石厅｜无｜整个石厅震动起来，两人抬头｜无｜无｜全景\n"
          "拍4｜遗迹石厅｜无｜特写摊开手掌，水晶浮在她掌心上方｜无｜无｜特写\n"
          "拍5｜遗迹石厅｜无｜一束阳光照在石台上｜无｜无｜全景")
_b10 = os_.parse_beats_loose(_raw10, ["遗迹大门", "遗迹石厅"], ["莉娅", "艾登"])
T("手的特写归上一拍的人，做什么补「艾登的」", _b10[1]["who"] == "艾登" and _b10[1]["act"].startswith("艾登的手"), _b10[1])
T("「两人」补成两张卡", _b10[2]["who"] and _b10[2].get("_multi") and "和" in _b10[2]["act"], _b10[2])
T("「她掌心」归上一拍的人（不是空镜）", _b10[3]["who"] != "", _b10[3])
T("真空镜仍是空镜", _b10[4]["who"] == "", _b10[4])
_raw11 = ("拍1｜遗迹石厅｜莉娅｜走到石台前，伸出手去够水晶｜无｜无｜中景\n"
          "拍2｜遗迹石厅｜艾登｜说：别碰！｜无｜艾登：别碰！｜中景\n"
          "拍3｜遗迹石厅｜无｜特写手停在水晶上方｜无｜无｜特写\n"
          "拍4｜遗迹石厅｜无｜特写摊开手掌，水晶浮在她掌心上方｜无｜无｜特写")
_b11 = os_.parse_beats_loose(_raw11, ["遗迹石厅"], ["莉娅", "艾登"], None, {"莉娅": "女", "艾登": "男"})
T("手的特写归最近做手上动作的人（莉娅），不是刚说话的艾登", _b11[2]["who"] == "莉娅" and _b11[2]["act"].startswith("莉娅的手"), _b11[2])
T("「她掌心」按性别对到莉娅", _b11[3]["who"] == "莉娅", _b11[3])
# P392：一问一答并一段、秒数按内容、同场景硬切带末帧
_raw12 = ("拍1｜遗迹石厅｜无｜一束阳光照在石台上｜无｜无｜全景\n"
          "拍2｜遗迹石厅｜莉娅｜走到石台前，伸出手去够水晶｜无｜无｜中景\n"
          "拍3｜遗迹石厅｜艾登｜说：别碰！｜无｜艾登：别碰！｜中景\n"
          "拍4｜遗迹石厅｜莉娅｜说：我就看看。｜无｜莉娅：我就看看。｜特写\n"
          "拍5｜遗迹石厅｜艾登｜说：我说了别碰。｜无｜艾登：我说了别碰。｜中景")
_b12 = os_.parse_beats_loose(_raw12, ["遗迹石厅"], ["莉娅", "艾登"], None, {"莉娅": "女", "艾登": "男"})
_s12 = os_.normalize_beats(_b12, ["莉娅", "艾登"], "适中")
T("「别碰」并进伸手那拍（文戏档一段最多三句台词）", len(_s12) == 2 and "艾登：别碰！" in _s12[1]["text"] and "莉娅：我就看看。" in _s12[1]["text"] and "我说了别碰" in _s12[1]["text"], [x["text"] for x in _s12])
T("文戏档并拍后 8～12 秒、同地不切", 8 <= _s12[1]["seconds"] <= 12 and _s12[1]["pace"] == "慢", _s12[1])
T("空镜定场 5 秒", _s12[0]["seconds"] == 5.0, _s12[0]["seconds"])
T("并了台词的动作拍 ≤ 12 秒", _s12[1]["seconds"] <= 12, _s12[1]["seconds"])
_bt = au.build_timeline.__code__.co_consts
T("同场景硬切标 same_scene_cut", any(isinstance(c, str) and c == "same_scene_cut" for c in _bt))
_inl = "0—9秒：莉娅双手抵住门框用力前推。莉娅说：<Subject 1> (S1) says:<d>[Chinese]走吧！</d>艾登站在右侧。艾登说：<Subject 2> (S2) says:<d>[Chinese]等等——</d>\n\n这一段结束时：\n· 景别：中景"
_sp = au.split_say_lines(_inl).split("\n")
T("夹在段落里的台词拆成独立行", "莉娅说：<Subject 1> (S1) says:<d>[Chinese]走吧！</d>" in _sp and "艾登说：<Subject 2> (S2) says:<d>[Chinese]等等——</d>" in _sp and "艾登站在右侧。" in _sp and _sp[0] == "0—9秒：莉娅双手抵住门框用力前推。", _sp)
_imp = "【机位】中景。\n\n0—3秒：莉娅凑近地图。艾登右手食指顺着路线滑动，向莉娅讲解路径。\n\n3—9秒：莉娅抬头看向树林，眼神亮起。艾登转头看向莉娅，等待她的反应。\n\n莉娅说：<Subject 1> (S1) says:<d>[Chinese]那不就是那边吗！</d>\n\n这一段结束时：\n· 景别：中景"
_imp2 = au.scrub_implied_speech(_imp, ["莉娅说：<Subject 1> (S1) says:<d>[Chinese]那不就是那边吗！</d>"])
T("没台词的人的「讲解」句删掉", "讲解" not in _imp2 and "凑近地图" in _imp2 and "顺着路线滑动" in _imp2, _imp2)
T("台词前一句补口型提示、台词紧贴时间块", "莉娅嘴唇开合。\n莉娅说：<Subject 1>" in _imp2, _imp2)
_hs = "【机位】中景。\n\n0—3秒：莉娅凑近地图。艾登食指顺着路线滑动。\n\n3—9秒：莉娅抬头看向树林。艾登转头看向莉娅。莉娅嘴唇开合。\n莉娅说：<Subject 1> (S1) says:<d>[Chinese]那不就是那边吗！</d>\n\n这一段结束时：\n· 景别：中景"
_hs2 = au.hoist_say_lines(_hs).split("\n")
T("节拍段台词提到第一块末尾并带口型提示", _hs2[2].endswith("莉娅嘴唇开合。") and _hs2[3].startswith("莉娅说：<Subject 1>") and "嘴唇开合" not in _hs2[5], _hs2)
_hs3 = "【机位】中景。\n\n0—3秒：莉娅推门。\n\n3—6秒：门开了。\n\n6—9秒：艾登上前。\n莉娅说：<Subject 1> (S1) says:<d>[Chinese]走吧！</d>\n艾登说：<Subject 2> (S2) says:<d>[Chinese]等等——</d>\n\n这一段结束时：\n· 景别：中景"
_hs4 = au.hoist_say_lines(_hs3)
T("两行台词分进第一、二块", _hs4.index("走吧") < _hs4.index("3—6秒") and _hs4.index("3—6秒") < _hs4.index("等等") < _hs4.index("6—9秒"), _hs4)
T("口述拍法解析景别/角度/运动", au.direction_camera("特写，仰拍，固定机位，莉娅在画面左边") == ("特写", "仰拍", "固定机位") and au.direction_camera("镜头跟着她走，中景") == ("中景", "", "跟拍"))
_fd = au.force_direction("【机位】中景，平视，固定机位，画面左侧为莉娅。\n\n0—3秒：莉娅抬头。\n\n这一段结束时：\n· 景别：中景", "特写仰拍，镜头跟着她")
T("口述的景别/角度/运动钉进【机位】行、结尾景别同步", _fd.startswith("【机位】特写，仰拍，跟拍，画面左侧为莉娅。") and "· 景别：特写" in _fd, _fd)
T("口述没写机位词就不动", au.force_direction("【机位】中景。\n\n0—3秒：x", "她笑一下") == "【机位】中景。\n\n0—3秒：x")
T("本来就独立的台词行不动", au.split_say_lines("0—3秒：他开口。\n阿青说：<Subject 1> (S1) says:<d>[Chinese]你好。</d>\n\n3—6秒：他看着。") == "0—3秒：他开口。\n阿青说：<Subject 1> (S1) says:<d>[Chinese]你好。</d>\n\n3—6秒：他看着。")


print("【P407 两档 + 节拍段守卫】")
_pb2 = [{"who": "莉娅", "act": "转身望向树林", "react": "", "say": "", "place": "桥", "shot": "全景"},
        {"who": "莉娅", "act": "说话", "react": "", "say": "莉娅：走吧", "place": "桥", "shot": "中景"}]
T("没台词的普通动作按本话默认：紧凑→快、舒缓→慢", os_.beat_pace(_pb2[0], os_._EP_PACE["紧凑"]) == "快" and os_.beat_pace(_pb2[0], os_._EP_PACE["舒缓"]) == "慢")
T("旧项目「适中」按快", os_._EP_PACE["适中"] == "快")
_ps2 = os_.normalize_beats([dict(b, _d=None) for b in _pb2], ["莉娅", "艾登"], "适中")
T("只有快/慢两档", set(x["pace"] for x in _ps2) <= {"快", "慢"}, [x["pace"] for x in _ps2])
_pb3 = [{"who": "莉娅", "act": "冲上前推门", "react": "", "say": "莉娅：走吧！\n艾登：等等", "place": "门", "shot": "中景"}]
_ps3 = os_.normalize_beats([dict(b, _d=None) for b in _pb3], ["莉娅", "艾登"], "紧凑")
T("快档带两句台词放宽到 8 秒", _ps3[0]["pace"] == "快" and 6 <= _ps3[0]["seconds"] <= 8, _ps3[0])
_o = ["莉娅说：<Subject 1> (S1) says:<d>[Chinese]走吧！</d>", "艾登说：<Subject 2> (S2) says:<d>[Chinese]等等——</d>"]
_hb = "【机位】中景。\n\n0—6秒：莉娅推门。\n艾登说：<Subject 2> (S2) says:<d>[Chinese]等等——</d>\n莉娅说：<Subject 1> (S1) says:<d>[Chinese]走吧！</d>\n\n这一段结束时：\n· 景别：中景"
_hb2 = au.hoist_say_lines(_hb, _o)
T("台词按原拍顺序排（走吧在等等前）", _hb2.index("走吧") < _hb2.index("等等"), _hb2)
_em = "【机位】全景。\n\n0—3秒：阳光照亮石门。画面中，其左侧一步处有一人。三人呈品字形站位。\n\n这一段结束时：\n· 景别：全景\n· 正在进行：三人静止注视\n· 本段已完成：展示溪水"
_em2 = au.beat_empty_tail(au.beat_drop_people(_em, ["莉娅", "艾登"]), "阳光照在石门和草地上")
T("空镜：有一人/三人/站位句删掉，已完成＝这一拍原文", "一人" not in _em2 and "三人" not in _em2 and "本段已完成：阳光照在石门和草地上" in _em2, _em2)
_fb = "0—3秒：莉娅站在画面中央，悬停的手猛地砸向水晶，指尖刚碰到水晶表面，水晶亮起蓝光。碎石砸在地上。"
_fb2 = au.beat_drop_alien_force(_fb, "莉娅的指尖碰到水晶，水晶亮起蓝光")
T("原文没写的「手砸向」删、「猛地」删、落石砸地留", "砸向" not in _fb2 and "猛地" not in _fb2 and "碎石砸在地上" in _fb2, _fb2)
_ea = "【机位】中景。\n\n0—3秒：右脚踩实，双脚站稳。她转过身招手。\n\n这一段结束时：\n· 景别：中景"
_ea2 = au.beat_ensure_act(_ea, "莉娅背着背包跑上桥来，跑到桥中间停下，转过身朝身后招手")
T("原文的「跑」没出现 → 原文动作句放回第一块开头", _ea2.split("\n")[2].startswith("0—3秒：莉娅背着背包跑上桥来"), _ea2)
T("原文动作都在就不动", au.beat_ensure_act("0—3秒：莉娅推门。", "莉娅上前推门") == "0—3秒：莉娅推门。")
_hd = "0—3秒：莉娅后退一步，左手护住腰间的黄铜罗盘。\n\n这一段结束时：\n· 莉娅：石台左侧｜面朝上｜手里黄铜罗盘｜站立｜画面左侧\n· 艾登：石台左侧｜面朝上｜手里拿着摊开的地图｜站立｜画面右侧"
_hd2 = au.beat_fix_hands(_hd, "艾登抱着地图筒 深棕色皮革地图筒 地图", ["莉娅", "艾登"])
T("卡上/原文没有的道具 → 手里无，正文带它的短句删；有的留", "手里无" in _hd2 and "罗盘" not in _hd2 and "手里拿着摊开的地图" in _hd2, _hd2)
_sd = "【机位】中景。\n\n0—3秒：莉娅站在画面左侧前景，艾登站在画面右侧中景。\n\n3—6秒：莉娅站在画面右侧，艾登站在画面左侧。\n\n这一段结束时：\n· 莉娅：桥｜面朝艾登｜手里无｜站立｜画面右侧\n· 艾登：桥｜面朝莉娅｜手里无｜站立｜画面左侧"
_sd2 = au.beat_fix_sides(_sd, ["莉娅", "艾登"])
T("左右按第一块：第二块和结尾状态改回来", "3—6秒：莉娅站在画面左侧，艾登站在画面右侧" in _sd2 and "· 莉娅：桥｜面朝艾登｜手里无｜站立｜画面左侧" in _sd2 and "· 艾登：桥｜面朝莉娅｜手里无｜站立｜画面右侧" in _sd2, _sd2)
_sd3 = au.beat_fix_sides("0—3秒：画面左侧，莉娅站着；艾登站在画面右侧前景。\n\n3—6秒：莉娅站在画面右侧前景。", ["莉娅", "艾登"])
T("「画面左侧，莉娅」这种写法也认", "3—6秒：莉娅站在画面左侧前景" in _sd3, _sd3)
_sj = "【机位】中景。\n\n0—3秒：艾登说完，莉娅看着他。\n\n3—6秒：双手自然垂落在身体两侧。\n\n这一段结束时：\n· 景别：中景"
_sj2 = au.beat_fix_subjects(_sj, ["莉娅", "艾登"], "艾登收回手")
T("缺主语的块补上一块最后提到的人", "3—6秒：莉娅双手自然垂落" in _sj2, _sj2)
T("第一块缺主语补原文里的人", "0—3秒：莉娅双手推门" in au.beat_fix_subjects("0—3秒：双手推门。", ["莉娅", "艾登"], "莉娅上前双手推门"))
_mf = "0—3秒：莉娅向前迈步，双手推门。艾登放下右手，开始转身面向门外，似乎在犹豫。"
_mf2 = au.beat_scrub_moves_fast(_mf, "莉娅上前双手推门", ["莉娅", "艾登"])
T("快档：原文里没走的人（艾登）的走位删，走了的人（莉娅）的同义走位留", "向前迈步" in _mf2 and "转身面向门外" not in _mf2 and "似乎在犹豫" in _mf2, _mf2)
_sp = "【机位】中景。\n\n0—12秒：莉娅走到石台前。她伸手。艾登看着。艾登抱胸。艾登嘴唇开合。\n艾登说：<Subject 2> (S2) says:<d>[Chinese]别碰！</d>\n莉娅说：<Subject 1> (S1) says:<d>[Chinese]我就看看。</d>\n\n这一段结束时：\n· 景别：中景"
_sp2 = au.hoist_say_lines(au.beat_split_long_block(_sp, 12), _o[:0] + ["艾登说：<Subject 2> (S2) says:<d>[Chinese]别碰！</d>", "莉娅说：<Subject 1> (S1) says:<d>[Chinese]我就看看。</d>"])
T("慢档 12 秒一块 → 拆两块，两句台词各归一块", "0—6秒：" in _sp2 and "6—12秒：" in _sp2 and _sp2.index("别碰") < _sp2.index("6—12秒") < _sp2.index("我就看看"), _sp2)
_td = au.beat_tidy("6—8秒：嘴唇开合清晰可见，……”。说完后她看着他。\n· 本段已完成：（艾登放下；艾登抽出；。）\n· 正在进行：莉娅说完“找到了……”后注视")
T("残句清理：……” / ；。 / ，。", "……”" not in _td and "；。" not in _td and "，。" not in _td and "（艾登放下；艾登抽出）" in _td and "找到了" in _td, _td)
T("指令示例句抄进正文 → 删；机位「继承上一段」删", "悬着的手" not in au.beat_scrub_examples("0—3秒：悬着的手砸下来，两人抬头。") and "继承上一段" not in au.beat_scrub_examples("【机位】特写，继承上一段特写视角但裁切，光从左上。"))


print("【P408】")
_nh = au._tl_norm_heads("【机位】全景。\n\n0—2.5秒：两人转身。\n2.5—5秒：望向树林。")
T("小数块头归整数、块前补空行", "0—3秒：" in _nh and "\n\n3—5秒：" in _nh and len(au._tl_blocks(_nh)[0]) == 2, _nh)
_ob = "【机位】特写。\n\n0—2秒：艾登右手按在符文上。莉娅站在左侧，她伸出左手食指点在另一道符文上，眼睛睁大。\n\n这一段结束时：\n· 艾登：门前｜面朝门｜右手按住符文｜站｜画面右侧\n· 莉娅：门前｜面朝门｜左手食指轻触符文｜站｜画面左侧"
_ob2 = au.beat_scrub_offbeat_actions(_ob, ["艾登"], ["莉娅", "艾登"])
T("不在这一拍的人的手上动作删、手里改无；在拍的人不动", "点在另一道符文" not in _ob2 and "眼睛睁大" in _ob2 and "· 莉娅：门前｜面朝门｜手里无｜" in _ob2 and "右手按在符文上" in _ob2, _ob2)
_hp = "0—3秒：莉娅右手轻扶地图筒。\n\n这一段结束时：\n· 莉娅：石台前｜面朝上｜右手轻扶地图筒｜站｜画面左侧\n· 艾登：石台前｜面朝上｜双手持地图筒｜站｜画面右侧\n· 本段已完成：x"
_hp2 = au.beat_fix_hands(_hp, "室内石厅", ["莉娅", "艾登"], per_known={"莉娅": "腰间右侧挂一只黄铜罗盘。背一个旧帆布小背包", "艾登": "斜挎一只深棕色皮革地图筒"}, beat_text="两人抬头看光", prev_state="")
T("道具按人查：艾登的地图筒到了莉娅手里 → 手里无；艾登自己的留", "· 莉娅：石台前｜面朝上｜手里无｜" in _hp2 and "· 艾登：石台前｜面朝上｜双手持地图筒｜" in _hp2, _hp2)
_hp3 = au.beat_fix_hands("这一段结束时：\n· 莉娅：石台前｜面朝上｜手里握着黄铜罗盘｜站｜画面左侧", "", ["莉娅"], per_known={"莉娅": "腰间右侧挂一只黄铜罗盘"}, beat_text="莉娅抬头")
T("卡上挂在腰间的东西不算手里", "手里无" in _hp3, _hp3)
_hp4 = au.beat_fix_hands("这一段结束时：\n· 莉娅：石台前｜面朝上｜手里握着黄铜罗盘｜站｜画面左侧", "", ["莉娅"], per_known={"莉娅": "腰间右侧挂一只黄铜罗盘"}, beat_text="莉娅掏出罗盘看")
T("原文写了拿罗盘就留", "握着黄铜罗盘" in _hp4, _hp4)
_sb = au.beat_split_long_block("【机位】中景。\n\n0—8秒：莉娅推门。门开了。艾登皱眉。艾登嘴唇开合。\n莉娅说：<Subject 1> (S1) says:<d>[Chinese]走吧！</d>\n艾登说：<Subject 2> (S2) says:<d>[Chinese]等等</d>\n\n这一段结束时：\n· 景别：中景", 8)
T("快档一块两句台词也拆", "0—4秒：" in _sb and "4—8秒：" in _sb, _sb)
T("室外场景删书桌句", "书桌" not in au.beat_scrub("0—3秒：艾登站在书桌左侧，莉娅看着他。", [], "室外，石拱桥"))
T("只剩标点的行删、孤「」删", au.beat_tidy("· 景别：全景\n。\n· 本段已完成：x\n0—3秒：艾登嘴唇开合，」说完后放松。") == "· 景别：全景\n· 本段已完成：x\n0—3秒：艾登嘴唇开合，说完后放松。", au.beat_tidy("· 景别：全景\n。\n· 本段已完成：x\n0—3秒：艾登嘴唇开合，」说完后放松。"))

T("室外卡上有「墙体」也照删「屋内」", "屋内" not in au.beat_scrub("0—3秒：莉娅站在屋内，扶着门框。", [], "室外，嵌入巨石墙体的石门，门框垂落枯藤") and "门框" in au.beat_scrub("0—3秒：莉娅站在屋内，扶着门框。", [], "室外，嵌入巨石墙体的石门，门框垂落枯藤"))

_cs = au._clause_subjects("艾登站在右侧，距离左侧的莉娅约两米远，食指触碰符文。莉娅和艾登一起抬头，但莉娅先笑了。", ["莉娅", "艾登"])
T("主语只认句首名字：宾语的莉娅不算", _cs[1][1] == {"艾登"} and _cs[2][1] == {"艾登"} and _cs[3][1] == {"莉娅", "艾登"} and _cs[4][1] == {"莉娅"}, _cs)
_ob3 = au.beat_scrub_offbeat_actions("0—3秒：艾登站在右侧，距离左侧的莉娅约两米远，食指触碰符文。莉娅目光聚焦于艾登掌心的水晶。艾登左手按住地图一角。\n\n这一段结束时：\n· 艾登：桥｜面朝莉娅｜左手按地图｜站｜画面右侧", ["莉娅"], ["莉娅", "艾登"], prev_state="· 艾登：桥｜面朝莉娅｜手里拿着摊开的地图｜站｜画面右侧")
T("在拍的人看别人掌心不删；不在拍的人延续上一段手里的地图不删", "掌心的水晶" in _ob3 and "左手按住地图一角" in _ob3 and "左手按地图｜" in _ob3, _ob3)

_cm = au.beat_scrub_examples("【机位】中景，平视，固定机位。上一段为特写，本段按【本段是一拍】要求，第一句即为「艾登收回手」，不额外给全景。\n\n0—3秒：x")
T("【机位】里的推理话删", _cm.split("\n")[0] == "【机位】中景，平视，固定机位。", _cm)
_eb = au._tl_ensure_block("【机位】全景，正面平视，固定机位。画面中无人，仅展示石厅。主光从缺口射下，照亮石台。\n\n这一段结束时：\n· 景别：全景", 5)
T("没写时间块 → 机位描述做成 0—5 秒一块", "【机位】全景，正面平视，固定机位。\n\n0—5秒：画面中无人，仅展示石厅。主光从缺口射下，照亮石台。" in _eb and len(au._tl_blocks(_eb)[0]) == 1, _eb)
T("有块就不动", au._tl_ensure_block("【机位】x。\n\n0—3秒：y", 5) == "【机位】x。\n\n0—3秒：y")
T("孤「（」删", au.beat_tidy("· 正在进行：（莉娅右手挥动至最高点。") == "· 正在进行：莉娅右手挥动至最高点。")

_ep = au.beat_drop_people("0—2秒：阳光直射石门。发丝被风吹动。\n\n2—5秒：拇指轻轻摩挲筒身皮革边缘，重心落在左腿，嘴角微抿，神情凝重。", ["莉娅", "艾登"])
T("空镜：没主语的身体/随身物句删，删空补环境句", "发丝" not in _ep and "拇指" not in _ep and "2—5秒：画面里没有人" in _ep, _ep)
_gr = au.beat_drop_alien_force("0—3秒：艾登左手抓着莉娅衣袖，手臂紧绷。艾登双手抓住地图筒两端。艾登看着她。", "莉娅上前推门，艾登说等等", ["莉娅", "艾登"])
T("原文没写的抓着别人删；抓住地图筒、看着留", "抓着莉娅" not in _gr and "抓住地图筒" in _gr and "艾登看着她" in _gr, _gr)
T("原文写了「拉住」，「抓住莉娅左臂」是同义不删", "抓住莉娅左臂" in au.beat_drop_alien_force("0—3秒：艾登右手抓住莉娅左臂，两人后退。", "艾登一把拉住莉娅的手臂", ["莉娅", "艾登"]))
_gt = au.beat_fix_hands("这一段结束时：\n· 艾登：门前｜面朝莉娅｜左手抓着莉娅衣袖｜前倾｜画面右侧", "", ["莉娅", "艾登"], per_known={"艾登": "斜挎地图筒"}, beat_text="莉娅推门，艾登说等等")
T("结尾状态抓着别人衣袖而原文没写 → 手里无", "手里无" in _gt, _gt)

_cl = au.beat_tidy("8—12秒：艾登保持站立姿势。莉娅嘴唇开合，兴奋地开口：\n说完后，莉娅保持前倾。\n\n这一段结束时：\n· 景别：中景")
T("台词提前后的「开口：」空头删；块头行、结束时行不动", "开口：" not in _cl and "艾登保持站立姿势。" in _cl and "嘴唇开合" not in _cl.split("\n")[0] and "这一段结束时：" in _cl and "8—12秒：" in _cl, _cl)
T("冒号后面紧跟台词行就留", "开口：" in au.beat_tidy("0—3秒：莉娅开口：\n莉娅说：<Subject 1> (S1) says:<d>[Chinese]走吧</d>"))
_pe = au.beat_drop_premature_entry("0—7秒：莉娅上前双手推门。莉娅站在门内阴影中，侧身等待，一只手还搭在门板上。艾登停在门槛外。", "莉娅上前双手推门，石门缓缓向内打开")
T("原文只推门 → 「站在门内」删，门槛外留", "门内" not in _pe and "艾登停在门槛外" in _pe and "上前双手推门" in _pe, _pe)
T("原文写了走进就不动", "门内" in au.beat_drop_premature_entry("0—2秒：莉娅站在门内。", "莉娅已经走进门里去了"))

T("行尾口型提示后面没台词 → 去掉；有台词留", au.beat_tidy("8—12秒：艾登喘息。莉娅嘴唇开合\n说完后她看着他。") == "8—12秒：艾登喘息。\n说完后她看着他。" and "莉娅嘴唇开合。" in au.beat_tidy("0—3秒：她看着他。莉娅嘴唇开合。\n莉娅说：<Subject 1> (S1) says:<d>[Chinese]走吧</d>"), au.beat_tidy("8—12秒：艾登喘息。莉娅嘴唇开合\n说完后她看着他。"))
T("「正在进行」里的提前进门删", "进入" not in au.beat_drop_premature_entry("这一段结束时：\n· 正在进行：艾登抬手指向门内，准备迈步进入", "莉娅推门"))

print("【P413】")
_b = "30岁的退伍军人张明和几个女团成员，小林，小爱，小可在一条船上遇到海难，几个人漂流在一个荒岛上，男人带着三个20岁女孩荒岛求生，渐渐发生情愫"
_ev = os_.event_card(_b)
T("一长句逗号连到底 → 按逗号分成 ≥3 件，人名碎片并回", len(_ev) >= 3 and "小可在一条船上遇到海难" in _ev[0]["text"] and any("漂流" in e["text"] for e in _ev), [e["text"] for e in _ev])
T("正常断句的口述不变", len(os_.event_card(BRIEF)) == len(ev))
_sx = os_.sex_from_brief(_b, ["张明", "小林", "小爱", "小可"])
T("女团成员后面的三个名字都是女；退伍军人张明是男", _sx.get("小林") == "女" and _sx.get("小爱") == "女" and _sx.get("小可") == "女" and _sx.get("张明") == "男", _sx)
T("没性别词就不猜", os_.sex_from_brief("张三和李四在路上走", ["张三", "李四"]) == {})

T("拍数上限按正文字数：488 字 2 句台词 → 上限 ≥15；900 字 → ≥25", os_.beat_range(3, 2, 488)[1] >= 15 and os_.beat_range(5, 4, 900)[1] >= 25 and os_.beat_range(3, 2, 200) == (6, 10), [os_.beat_range(3, 2, 488), os_.beat_range(5, 4, 900), os_.beat_range(3, 2, 200)])

print("【P415】")
_eb = os_.fix_env_beats([{"who": "张明", "act": "四个人影在沙滩上忙碌，身影交错", "react": "", "say": "", "place": "沙滩", "shot": "全景"},
                         {"who": "张明", "act": "海风穿过树梢，吹得湿衣紧贴皮肤", "react": "", "say": "", "place": "沙滩", "shot": "全景"},
                         {"who": "张明", "act": "抓住船板回头吼", "react": "", "say": "", "place": "海", "shot": "中景"}], ["张明", "小林", "小爱", "小可"])
T("群体句 → 全员；纯环境句 → 空镜；正常拍不动", _eb[0]["who"] == "张明和小林和小爱和小可" and _eb[1]["who"] == "" and _eb[2]["who"] == "张明", [b["who"] for b in _eb])
_rb = os_.normalize_beats([{"who": "张明", "act": "鱼叉刺入缝隙挑出鱼", "react": "鱼挣扎着被挑出", "say": "", "place": "沙滩", "shot": "全景", "_d": None}], ["张明", "小林"], "紧凑")
T("物件开头的反应句不补人名", "小林鱼" not in _rb[0]["text"] and "鱼挣扎" in _rb[0]["text"], _rb[0]["text"])
from cores import asset_core as _ac
T("抄正文的「空间」判为无效", _ac._space_is_prose("半个时辰后，海浪将残板推向沙滩。张明率先跳下。") and not _ac._space_is_prose("室外，热带荒岛沙滩。远景是海面。"))

print("【P416】")
_ev6 = [{"n": 1, "text": "退伍军人张明和女团成员小林、小爱、小可坐的船遇到海难"}, {"n": 2, "text": "四个人漂到荒岛"}]
_pr = "退伍军人张明和女团成员小林、小爱、小可坐的船遇到海难。\n\n四个人漂到荒岛。\n\n“没事吧？”他问。张明走向海边捡起枯枝，削成鱼叉。"
_pr2, _dr = os_.strip_echo_paragraphs(_pr, _ev6)
T("照抄事件卡的段删掉、正常段留", _dr == [1, 2] and _pr2.startswith("“没事吧？”"), (_dr, _pr2[:30]))
T("没抄就不动", os_.strip_echo_paragraphs("张明走向海边。", _ev6)[1] == [])
_eb2 = os_.fix_env_beats([{"who": "小可", "act": "海潮轻拍沙滩，一切归于宁静", "react": "", "say": "", "place": "荒岛", "shot": "全景"},
                          {"who": "小可", "act": "篝火旁，四个身影依偎在一起", "react": "", "say": "", "place": "荒岛", "shot": "全景"}], ["张明", "小林", "小爱", "小可"])
T("海潮句 → 空镜；篝火旁四个身影 → 全员", _eb2[0]["who"] == "" and _eb2[1]["who"] == "张明和小林和小爱和小可", [b["who"] for b in _eb2])
_rb2 = os_.normalize_beats([{"who": "张明", "act": "转身砍树枝搭帐篷", "react": "女孩们看着他忙碌的背影", "say": "", "place": "荒岛", "shot": "特写", "_d": None}], ["张明", "小林"], "紧凑")
T("「女孩们」开头的反应不补人名", "小林女孩们" not in _rb2[0]["text"], _rb2[0]["text"])
_ph = os_.normalize_beats([{"who": "张明", "act": "面朝对方，开口说话", "react": "", "say": "张明：没事吧？", "place": "荒岛", "shot": "特写", "_d": None},
                           {"who": "小林", "act": "面朝对方，开口说话", "react": "", "say": "小林：没事。", "place": "荒岛", "shot": "特写", "_d": None}], ["张明", "小林"], "紧凑")
T("两个占位拍并成「两人面对面说话」", len(_ph) == 1 and "两人面对面说话" in _ph[0]["text"] and "开口说话，小林面朝" not in _ph[0]["text"], _ph[0]["text"])

_ev7 = os_.event_card("张明和小林坐的船遇到海难。\n四个人抓着船板漂到荒岛，衣服被撕得只剩一半。\n三个女孩防备张明，躲在一起。")
T("事件卡文本首尾没有标点", all(not e["text"].startswith(("。", "，")) and not e["text"].endswith("。") for e in _ev7), [e["text"] for e in _ev7])

T("卡名「林小」改回口述的「小林」；在口述里的名字不动", os_.fix_names_from_brief("退伍军人张明和女团成员小林、小爱、小可", ["张明", "林小", "小爱"]) == {"林小": "小林"})

T("地点类别：沙滩=岸、海上=水、礁石堆判不出", os_.place_class("沙滩") == "岸" and os_.place_class("海上") == "水" and os_.place_class("背风的礁石堆") == "")
_sp = os_.split_merged_by_class("海上", "室外，荒岛主滩。远景是海面", ["海上", "沙滩", "背风的礁石堆", "浅滩", "斜坡"])
T("归并只并同类：沙滩/浅滩拆成一张岸卡（浅滩作别名），礁石堆跟主景", _sp == [("沙滩", ["浅滩"])] or _sp == [("沙滩", ["浅滩"]), ("斜坡", [])], _sp)

_wx = os_.writer_extras(ev, "紧凑", prose_words="3000～4000")
T("篇幅按项目设置 3000～4000 抬高、每件事约 N 字（平铺要求，不带先写/再写标题）", "2800" in _wx["篇幅"] and "每件事的篇幅" in _wx and "先写" not in _wx["每件事的篇幅"], _wx["篇幅"])
T("没给篇幅设置时还是事件数×节奏", os_.writer_extras(ev, "紧凑")["篇幅"] == ex["篇幅"])
T("字数范围解析", os_.words_range("3000～4000") == (3000, 4000) and os_.words_range("3500") == (3500, 3500) and os_.words_range("") == (0, 0))

print("【P421】")
_ms = os_.merge_split_quotes("小爱：张哥，\n小爱试探性地喊道。\n小爱：你叫什么名字？\n张明：以前在部队。\n张明：习惯一个人。")
T("逗号断开的同人台词并成一句；句号结尾的不并", "小爱：张哥，你叫什么名字？" in _ms and "张明：以前在部队。\n张明：习惯一个人。" in _ms, _ms)
_fb = os_.fix_env_beats([{"who": "张明", "act": "第一个跳下海水没过胸口", "react": "", "say": "", "place": "海上", "shot": "中景"},
                         {"who": "", "act": "站稳脚跟向女孩们伸出手", "react": "小林。", "say": "", "place": "海上", "shot": "中景"},
                         {"who": "小林", "act": "犹豫看鱼", "react": "张明。", "say": "", "place": "沙滩", "shot": "特写"},
                         {"who": "小可", "act": "看向小爱和小林", "react": "小爱和小林。", "say": "", "place": "沙滩", "shot": "中景"}], ["张明", "小林", "小爱", "小可"])
T("没写谁的动作拍沿用上一拍的人；只填人名的反应栏清空", _fb[1]["who"] == "张明" and _fb[1]["react"] == "" and _fb[2]["react"] == "" and _fb[3]["react"] == "", [(b["who"], b["react"]) for b in _fb])
from cores import asset_core as _ac2
T("空间描述：碎片「远处有树林」无效，室外开头且够长有效", _ac2._space_is_prose("远处有树林") and _ac2._space_is_prose("岛屿不大，植被茂密，远处有树林") and not _ac2._space_is_prose("室外，荒岛沙滩。远景是海面，中景是礁石，前景是细沙。"))
T("收尾标志句", bool(os_._ENDING_MARK.search("日子一天天过去。")) and bool(os_._ENDING_MARK.search("故事，在这一刻，画上句号。")) and not os_._ENDING_MARK.search("张明把鱼递给她。"))

print("【P423】")
_dd = au.dedup_say_lines("0—3秒：x\n小可说：<Subject 4> (S4) says:<d>[Chinese]谢了张哥。</d>\n小林说：<Subject 2> (S2) says:<d>[Chinese]张哥。</d>\n小林说：<Subject 2> (S2) says:<d>[Chinese]张哥。</d>")
T("去重只在同一说话人内：小林的「张哥」留一句", _dd.count("[Chinese]张哥。") == 1 and "谢了张哥" in _dd, _dd)
_ev = au.beat_even_blocks("【机位】特写。\n\n0—1秒：火星落入。\n小林说：<Subject 2> (S2) says:<d>[Chinese]好吃。</d>\n\n1—10秒：小可嘴唇开合。\n\n这一段结束时：\n· 景别：特写", 10)
T("1 秒块平均重排成 0—5 / 5—10", "0—5秒：火星落入。" in _ev and "5—10秒：小可嘴唇开合。" in _ev and "[Chinese]好吃" in _ev, _ev)
T("4 秒段两块 2 秒不动", au.beat_even_blocks("0—2秒：a\n\n2—4秒：b", 4) == "0—2秒：a\n\n2—4秒：b")

_nh2 = au._tl_norm_heads("【机位】特写。\n\n0—10秒：张明擦火石。\n小林说：<Subject 2> (S2) says:<d>[Chinese]好吃。</d>\n\n3—6。\n说完后小林站着。\n\n6—10。\n小林嘴唇开合。")
T("「3—6。」补成块头、正文接上；0—10 与 3—6 重叠 → 0—3", "0—3秒：张明擦火石。" in _nh2 and "3—6秒：说完后小林站着。" in _nh2 and "6—10秒：小林嘴唇开合。" in _nh2 and len(au._tl_blocks(_nh2)[0]) == 3, _nh2)

_mc = au.fix_mouth_cue_speaker("0—3秒：张明停止摩擦，张明嘴唇开合。\n小林说：<Subject 2> (S2) says:<d>[Chinese]好吃。</d>\n\n3—6秒：小可嘴唇开合。\n小可说：<Subject 4> (S4) says:<d>[Chinese]谢了。</d>")
T("口型提示改成说话人；对的不动", "小林嘴唇开合。\n小林说" in _mc and "张明嘴唇开合" not in _mc and "小可嘴唇开合。\n小可说" in _mc, _mc)

print("【P426】")
_tj = os_.normalize_beats([{"who": "张明", "act": "看了小可一眼嘴角扬起弧度", "react": "", "say": "", "place": "沙滩", "shot": "特写", "_d": None},
                           {"who": "张明", "act": "第二天清晨醒来起身走到帐篷边", "react": "", "say": "", "place": "沙滩", "shot": "中景", "_d": None},
                           {"who": "小可", "act": "荒岛的日子，才刚刚开始", "react": "", "say": "", "place": "沙滩", "shot": "全景", "_d": None}], ["张明", "小可"], "舒缓")
T("时间跳转的拍不并进前一拍且硬切；收尾句算空镜", len(_tj) == 3 and _tj[1]["cut"] and _tj[1]["establish"] and _tj[2]["who"] == "", [(x["who"], x["cut"], x["establish"]) for x in _tj])

_cp = os_.check_prose("莉娅和艾登过桥。艾登说：走吧。路人甲说：你好。", [{"n": 1, "text": "莉娅和艾登按地图过桥"}], "莉娅和艾登按地图过桥", {1: [1]}, [{"name": "艾登", "role": "其他"}, {"name": "路人甲", "role": "其他"}], [])
T("口述里点名的艾登不算多出来的人；真多出来的路人甲算", "艾登" not in _cp["extra_people"] and "路人甲" in _cp["extra_people"], _cp["extra_people"])

print("【P429】")
from cores import universal_writer as _uw
_din = _uw.writing_input({"story_mode": "single", "one_line": "x", "story_pace": "舒缓", "_writer_table": "【事件1】a\n【状态】还没喊张哥\n对白：\n小可：你是谁？"}, 1, [], brief="少年救了老者。老者报答少年。", brief_override=True)
T("写手输入表带上状态+对白表", os_._TABLE_KEY in _din and "还没喊张哥" in _din[os_._TABLE_KEY])
T("没有表就不带", os_._TABLE_KEY not in _uw.writing_input({"story_mode": "single", "one_line": "x"}, 1, [], brief="少年救了老者。", brief_override=True))
T("零模型时出表返回空串不报错", os_.status_dialogue_table([{"n": 1, "text": "a"}], None) == "")

_pm = os_.normalize_beats([{"who": "老者", "act": "面朝对方，开口说话", "react": "", "say": "老者：一是给钱，二是入门。", "place": "屋", "shot": "特写", "_d": None},
                           {"who": "林岩", "act": "搁柴刀擦手", "react": "神色认真", "say": "", "place": "屋", "shot": "中景", "_d": None}], ["林岩", "老者"], "舒缓")
T("占位拍并真动作 → 用真动作替换", len(_pm) == 1 and "开口说话，" not in _pm[0]["text"] and "林岩搁柴刀擦手" in _pm[0]["text"], _pm[0]["text"])

_ls = os_._split_say("老者：前者易得，后者难修。你若选钱，我即刻便给；你若选修炼，往后吃苦受累，可别后悔。")
T("长台词只在句末拆，逗号不拆", all("，" not in x[-1] for x in _ls) and any("你若选修炼，往后吃苦受累，可别后悔" in x for x in _ls) and len(_ls) >= 2, _ls)
T("单句超长整句一拍", len(os_._split_say("老者：这一句很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长，没有句号只有逗号")) == 1)

print("【P432】")
_ra = au.scrub_clauses("0—3秒：小林（S2）走到船舷边，背靠船舷，身体重心后仰，面朝张明。张明点头。", au._MOVE_WORDS, keep_if_in="张明点头", names=["张明", "小林"])
T("删掉带主语的短句后，主语接到下一短句", "小林背靠船舷" in _ra and "走到船舷边" not in _ra and "张明点头" in _ra, _ra)
_gs = os_.fix_env_beats([{"who": "", "act": "海潮起落，岛屿依旧荒凉，但四个人的身影在晨光中拉得很长", "react": "", "say": "", "place": "沙滩", "shot": "全景"}], ["张明", "小林", "小爱", "小可"])
T("群体词不在句首也算全员镜头，不当空镜", _gs[0]["who"] == "张明和小林和小爱和小可", _gs[0]["who"])
T("行首孤立句号去掉", au.beat_tidy("。小林咽下食物，嘴角带笑。") == "小林咽下食物，嘴角带笑。")

print("【P433】")
_fa, _ = au.fix_body_aliases("老者（S2）躺在榻上。青云门老者闭眼。林岩看着老者。", [{"name": "林岩"}, {"name": "青云门老者"}])
T("化名改全名不叠字", "青云门青云门" not in _fa and _fa.count("青云门老者") == 3, _fa)
_tp = au.beat_tail_pose_from_body("【机位】中景。\n\n0—3秒：老者平躺在榻上闭眼，林岩站在榻边。\n\n这一段结束时：\n· 老者：榻上｜面朝上｜手里无｜站立｜画面左侧\n· 林岩：榻边｜面朝老者｜手里无｜站立｜画面右侧", ["林岩", "老者"])
T("状态行姿势按最后一块：躺着的人不写站立", "· 老者：榻上｜面朝上｜手里无｜躺着｜" in _tp and "· 林岩：榻边｜面朝老者｜手里无｜站立｜" in _tp, _tp)
T("机位行括号不配对时去掉", "）" not in au.beat_tidy("【机位】中景，固定机位）。\n\n0—3秒：x"))

print("【P434】")
T("「一侧肩颈完全裸露」不算全裸", not au.is_nude({"name": "小爱", "age": 19, "sex": "女", "clothing": "纯白色高领打底衫，领口歪斜，一侧肩颈完全裸露；赤裸的肩头"}))
T("写明全裸才算", au.is_nude({"name": "x", "age": 25, "sex": "女", "clothing": "全裸，只戴一条项链"}) and au.is_nude({"name": "y", "age": 25, "sex": "女", "clothing": "全身赤裸"}))

print("【P435 导演分段】")
from cores import director_shots as ds
_raw = """【段】地点=沙滩｜在场=张明、小林、小可｜秒=15｜机位=中景，固定机位，张明在画面左侧，三个女孩在画面右侧
目的=防备
0-4秒=张明环顾树林礁石，迈步走向三个女孩 D1
4-8秒=小林退半步双手交握胸前 D2
8-12秒=小可上前半步张开双臂挡在前面 D3
12-17秒=张明在两米外停步，蹲下捡起一根枯枝
结束=张明：离女孩两米，面朝女孩们，右手握枯枝，蹲着，画面左侧；小林：小可身后，面朝张明，双手交握胸前，站立，画面右侧
【段】地点=浅滩｜在场=张明｜秒=6｜机位=全景低机位
目的=捕鱼
0-3秒=张明削尖树枝做鱼叉
3-6秒=张明叉起一条鱼
结束=张明：浅水里，面朝沙滩，左手提鱼，站立，画面中央"""
_D = [("张明", "别怕，我过去。"), ("小林", "你别过来。"), ("小可", "离远点。"), ("张明", "那边有鱼群，我过去看看。")]
_sh = ds.parse(_raw, ["沙滩", "礁石浅滩"], ["张明", "小林", "小爱", "小可"], _D)
T("解析出两段", len(_sh) == 2 and _sh[0]["cast"] == ["张明", "小林", "小可"] and len(_sh[0]["blocks"]) == 4, _sh)
T("块里的台词编号解析并从文本里去掉", _sh[0]["blocks"][0][3] == [1] and "D1" not in _sh[0]["blocks"][0][2])
_fx, _st = ds.fix_shots(_sh, _D, {1, 2, 3, 4}, ["沙滩", "礁石浅滩"], ["张明", "小林", "小爱", "小可"], {}, "", None)
T("超 15 秒按块拆两段、不足 8 秒并不了的短段拉到 8 秒", [x["seconds"] for x in _fx] == [12.0, 8.0, 8.0], [x["seconds"] for x in _fx])
T("漏掉的 D4 补到说话人所在的最后一块", any(4 in b[3] for x in _fx for b in x["blocks"]))
T("地点对到场景卡（浅滩→礁石浅滩）", _fx[-1]["place"] == "礁石浅滩", _fx[-1]["place"])
T("结束状态每个在场的人都有，缺的沿用上一段", set(_fx[1]["end_state"]) == {"张明", "小林", "小可"} and _fx[1]["end_state"]["小可"]["side"] in ("画面右侧", "画面中央"), _fx[1]["end_state"])
_sl = ds.to_slices(_fx)
T("切片带 director 骨架、全部硬切、who=在场", _sl[0]["director"]["camera"].startswith("中景") and all(x["cut"] for x in _sl) and _sl[0]["who"] == "张明、小林、小可", _sl[0])
_sh2 = ds.parse(_raw.replace("12-17秒", "12-15秒"), ["沙滩", "礁石浅滩"], ["张明", "小林", "小爱", "小可"], _D)
_fx2, _ = ds.fix_shots(_sh2, _D, {1, 2, 3, 4}, ["沙滩", "礁石浅滩"], ["张明", "小林", "小爱", "小可"], {}, "", None)
_sl = ds.to_slices(_fx2)
T("拆开的前一半继承后一半没提到的人的结束状态", _fx[0]["end_state"]["小林"]["posture"] == "站立" and _fx[0]["end_state"]["张明"]["side"] == "画面左侧", _fx[0]["end_state"])
_body = "【机位】特写。\n\n0—4秒：张明站在画面左侧，环顾树林与礁石，迈步走向右侧的三个女孩，靴子踩进沙里。\n\n4—8秒：小林退半步，双手交握在胸前。\n\n8—12秒：小可上前半步张开双臂。\n\n这一段结束时：\n· 张明：xx"
_by = {1: "张明说：<Subject 1> (S1) says:<d>[Chinese]别怕，我过去。</d>", 2: "小林说：<Subject 2> (S2) says:<d>[Chinese]你别过来。</d>", 3: "小可说：<Subject 3> (S3) says:<d>[Chinese]离远点。</d>"}
_out = ds.assemble(_body, _sl[0]["director"], [], _by)
T("机位行按导演的、台词放进标的块末并补口型、结尾状态程序写", _out.startswith("【机位】中景，固定机位") and "张明嘴唇开合。\n张明说：<Subject 1>" in _out and "· 张明：离女孩两米｜面朝女孩们｜右手握枯枝｜蹲着｜画面左侧" in _out and "· 张明：xx" not in _out, _out)
T("少写的块用骨架句补齐", "12—15秒：张明在两米外停步，蹲下捡起一根枯枝" in _out, _out)
T("skeleton_text 带机位和台词标记", "← 这一块末尾张明各说一句" in ds.skeleton_text(_sl[0]["director"], {}), ds.skeleton_text(_sl[0]["director"], {}))

print("【P436】")
_raw3 = """【段】地点=海中｜在场=张明、其余三人｜秒=14｜机位=全景，低机位，手持跟拍，张明在左，其余三人在右
目的=四人随波逐流
0-4秒=张明攥紧船板稳住重心，侧头看向右侧，【D1】张明：风浪有点大。
4-9秒=小林抹去脸上海水点头，【D2】小林：嗯，抓紧了。
9-13秒=小可伸手扶住小爱肩膀
结束=张明：画面左侧，面朝右侧，手里攥着船板，姿势：站，画面左侧；小林：画面中左，面朝前方，手里抓着木板，姿势：站，画面中央"""
_D3 = [("张明", "风浪有点大。"), ("小林", "嗯，抓紧了。")]
_p3 = ds.parse(_raw3, ["海中"], ["张明", "小林", "小爱", "小可"], _D3)
T("「【D1】张明：风浪有点大。」整句从块里去掉、编号留下", _p3[0]["blocks"][0][2] == "张明攥紧船板稳住重心，侧头看向右侧" and _p3[0]["blocks"][0][3] == [1], _p3[0]["blocks"][0])
_e3 = _p3[0]["end_state"]["张明"]
T("结束状态按标签认栏", _e3["side"] == "画面左侧" and _e3["facing"] == "面朝右侧" and _e3["hands"] == "手里攥着船板" and _e3["posture"] == "站立", _e3)
_f3, _ = ds.fix_shots(_p3, _D3, {1, 2}, ["海中"], ["张明", "小林", "小爱", "小可"], {}, "", None)
T("「其余三人」→ 全员在场", _f3[0]["cast"] == ["张明", "小林", "小爱", "小可"], _f3[0]["cast"])
T("小爱没写结束状态 → 默认按机位行的左右", _f3[0]["end_state"]["小爱"]["side"] == "画面中央" or _f3[0]["end_state"]["小可"]["side"] in ("画面中央", "画面右侧"))

print("【P437】")
_raw4 = """【段】地点=沙滩｜在场=张明、小林｜秒=13｜机位=全景，固定
目的=上岸
0-4秒=张明扯衣襟苦笑【D1】张明：衣服破了。｜小林低头看裙摆
4-9秒=小爱抱臂发抖
9-13秒=小林伸手拉小爱站起来
结束张明：左侧，面朝右，无，姿势：站，画面左侧；小林：右侧，面朝左，无，站，画面右侧"""
_D4 = [("张明", "衣服破了。"), ("小林", "我的也是。"), ("小爱", "好冷啊。")]
_p4 = ds.parse(_raw4, ["沙滩"], ["张明", "小林", "小爱", "小可"], _D4)
T("「结束张明：」漏了 = 也认", _p4[0]["end_state"].get("张明", {}).get("side") == "画面左侧" and _p4[0]["end_state"]["张明"]["pos"] == "左侧", _p4[0]["end_state"])
T("块里的「｜」换成「；」", "｜" not in _p4[0]["blocks"][0][2] and "；" in _p4[0]["blocks"][0][2], _p4[0]["blocks"][0][2])
_f4, _ = ds.fix_shots(_p4, _D4, {1, 2, 3}, ["沙滩"], ["张明", "小林", "小爱", "小可"], {}, "", None)
_bl4 = _f4[0]["blocks"]
T("漏的 D2 按顺序放在 D1 之后、说话人所在的块（不是最后一块）", 2 in _bl4[0][3] and 3 in _bl4[1][3], [b[3] for b in _bl4])
_sk = ds.skeleton_text(ds.to_slices(_f4)[0]["director"], {})
T("骨架台词标记写人名不写 D 编号", "张明、小林各说一句" in _sk and "D1" not in _sk, _sk)
_out4 = ds.assemble("【机位】x\n\n0—4秒：张明扯衣襟。D6位于中央，嘴唇开合。张明嘴唇开合。张明嘴唇开合。\n\n4—9秒：小爱抱臂。\n\n9—13秒：小林拉小爱。", ds.to_slices(_f4)[0]["director"], [], {1: "张明说：<Subject 1> (S1) says:<d>[Chinese]衣服破了。</d>", 2: "小林说：<Subject 2> (S2) says:<d>[Chinese]我的也是。</d>", 3: "小爱说：<Subject 3> (S3) says:<d>[Chinese]好冷啊。</d>"})
T("正文里的 D6 人物删掉、口型不重复", "D6" not in _out4 and _out4.count("张明嘴唇开合") == 1, _out4)

print("【P438】")
_d8 = {"camera": "中景，跟拍", "cast": ["张明"], "blocks": [[0, 4, "张明涉水走向浅滩", []], [4, 8, "张明弯腰看水面", []]], "end_state": {"张明": {"pos": "浅水里", "facing": "面朝前方", "hands": "无", "posture": "站立", "side": "画面中央"}}, "says": []}
_o8 = ds.assemble("【机位】中景。\n\n0—4秒：深栗色短碎发随动作微晃，他踩入浅水（假设S2位于该方向），身体前倾；无\n\n4—8秒：距画面中心约1.5米，弯腰观察水面；无", _d8, [{"name": "张明"}], {})
T("块开头没主语补人、推理括号删、块末「；无」删", "0—4秒：张明的深栗色短碎发" in _o8 and "假设" not in _o8 and "；无" not in _o8 and "4—8秒：张明距画面中心" in _o8, _o8)

print("【P439】")
_raw9 = """【段】地点=沙滩｜在场=张明、小林｜秒=12｜机位=中景，固定，张明在左，小林在右
目的=<递鱼>
0-4秒=<张明递鱼给小林>
4-8秒=小林咬一口
8-12秒=张明拍手上的灰
结束=张明：火堆左，面朝小林，无，站，画面左侧；小林：火堆右，面朝张明，手里拿鱼，站，画面右侧
【段】地点=沙滩｜在场=张明、小林｜秒=12｜机位=中景，固定，小林在左，张明在右
目的=夸他
0-4秒=小林看着张明说
4-8秒=张明笑
8-12秒=两人对视
结束=张明：火堆右，面朝小林，无，站，画面右侧；小林：火堆左，面朝张明，无，站，画面左侧"""
_p9 = ds.parse(_raw9, ["沙滩"], ["张明", "小林"], [])
T("尖括号去掉", _p9[0]["purpose"] == "递鱼" and _p9[0]["blocks"][0][2] == "张明递鱼给小林", _p9[0])
_f9, _ = ds.fix_shots(_p9, [], set(), ["沙滩"], ["张明", "小林"], {}, "", None)
T("同一地点下一段没写走位 → 左右沿用上一段", _f9[1]["end_state"]["张明"]["side"] == "画面左侧" and _f9[1]["end_state"]["小林"]["side"] == "画面右侧", _f9[1]["end_state"])
_d9 = ds.to_slices(_f9)[1]["director"]
_o9 = ds.assemble("【机位】x\n\n0—4秒：小林位于画面左侧前景，看着张明。\n\n4—8秒：张明站在画面右侧，笑了。\n\n8—12秒：两人对视。", _d9, [{"name": "张明"}, {"name": "小林"}], {})
T("写手正文里的左右按账本改、机位行跟着改", "小林位于画面右侧" in _o9 and "张明站在画面左侧" in _o9 and _o9.startswith("【机位】中景，固定，小林在右，张明在左"), _o9)
_o9b = ds.assemble("【机位】x\n\n0—4秒：小林位于画面左侧前景，距离镜头约两米，身体微侧。\n\n4—8秒：张明笑了。\n\n8—12秒：两人对视。", _d9, [{"name": "张明"}, {"name": "小林"}], {})
T("0 秒先写动作", _o9b.split("0—4秒：")[1].startswith("小林看着张明说。"), _o9b)
T("口型只留一个", ds.final_tidy("0—4秒：张明递鱼。张明嘴唇开合。张明嘴唇开合。小林嘴唇开合。") == "0—4秒：张明递鱼。张明嘴唇开合。小林嘴唇开合。")

print("【P440】")
_rawa = """【段】地点=沙滩｜在场=张明、小林、小爱、小可｜秒=13｜机位=全景，固定
目的=上岸
0-4秒=四人翻上岸，张明撑地喘气，小可单手撑地
4-9秒=小林低头看裙摆，小爱抱臂发抖
9-13秒=张明扯衣襟苦笑；小林低头；小爱抱臂；小可站直
结束=张明：沙地，面朝前，无，坐着，画面左侧；小林：沙地，面朝前，无，坐着，画面中央；小爱：沙地，面朝前，无，坐着，画面右侧；小可：沙地，面朝左，无，站立，画面右侧"""
_Da = [("张明", "衣服破了。"), ("小林", "我的也是。"), ("小爱", "好冷啊。"), ("小可", "谁先上岸？")]
_pa = ds.parse(_rawa, ["沙滩"], ["张明", "小林", "小爱", "小可"], _Da)
_fa, _ = ds.fix_shots(_pa, _Da, {1, 2, 3, 4}, ["沙滩"], ["张明", "小林", "小爱", "小可"], {}, "", None)
T("漏掉的四句台词分散到三块", [b[3] for b in _fa[0]["blocks"]] == [[1], [2, 3], [4]], [b[3] for b in _fa[0]["blocks"]])
_da = ds.to_slices(_fa)[0]["director"]
_oa = ds.assemble("【机位】x\n\n0—4秒：张明站在沙地上喘气，<d>[]谁先上岸？</d>。小可撑地。\n\n4—9秒：小林站着看裙摆。\n\n9—13秒：张明扯衣襟。", _da, [{"name": n} for n in _da["cast"]], {})
T("台词碎片删、坐着的人正文不写站", "<d>" not in _oa and "张明坐在沙地上" in _oa and "小林坐着看裙摆" in _oa, _oa)

print("【P441】")
T("剧本没写清晨 → 沙滩·清晨换成沙滩", ds._time_fallback("沙滩·清晨", ["海中", "沙滩", "沙滩·清晨"], "张明迈步走向女孩们") == "沙滩")
T("剧本写了清晨 → 保留", ds._time_fallback("沙滩·清晨", ["海中", "沙滩", "沙滩·清晨"], "清晨的阳光洒满沙滩") == "沙滩·清晨")
_ms = ds.missing_points("张明很有经验，用树枝做鱼叉在礁石边捕了很多鱼，又砍树枝和棕榈叶给女孩搭了一个帐篷", [{"blocks": [[0, 4, "张明把烤好的鱼肉递给小林", []], [4, 8, "小林咬一口", []]]}])
T("要点覆盖：捕鱼和搭帐篷都算漏", any("鱼叉" in x for x in _ms) and any("帐篷" in x for x in _ms), _ms)
T("要点覆盖：拍了就不算漏", ds.missing_points("张明用树枝做鱼叉捕鱼", [{"blocks": [[0, 4, "张明削尖树枝做成鱼叉，叉起一条鱼", []]]}]) == [])

print("【P441b】")
T("非动作句不算漏", ds.missing_points("张明很有经验，女孩们慢慢接受了他，四个人合作在岛上活下来", [{"blocks": [[0, 4, "张明递鱼", []]]}]) == [])
T("名词拍到就不算漏（帐篷）", ds.missing_points("又砍树枝和棕榈叶给女孩搭了一个帐篷", [{"blocks": [[0, 4, "小可走到帐篷边摸叶子", []]]}]) == [])
T("帐篷沙滩·夜 → 同类不带时间词的沙滩", ds._time_fallback("帐篷沙滩·夜", ["海中", "沙滩", "礁石浅滩", "帐篷沙滩·夜", "沙滩·清晨"], "张明迈步走向女孩们") == "沙滩")

print("【P442】")
_Dq = [("小可", "张哥，开饭。"), ("张明", "今晚吃啥？")]
T("块里抄的台词整句去掉", ds._strip_quotes("小可端着食材走近；小可：张哥，开饭；张明目光扫过工具，语气随意", _Dq) == "小可端着食材走近；张明目光扫过工具，语气随意", ds._strip_quotes("小可端着食材走近；小可：张哥，开饭；张明目光扫过工具，语气随意", _Dq))
T("引号写法也去掉", "鱼烤好了" not in ds._strip_quotes("张明看着小林，温和地说：“鱼烤好了。”；小林接过鱼肉", [("张明", "鱼烤好了。")]))

print("【P442b】")
_pb = ds.parse("【段】地点=山坳｜在场=林岩、青云门老者｜秒=12｜机位=中景\n0-4秒=林岩挥斧劈藤，抹汗；画面左侧的林岩\n4-8秒=老者费力睁眼，声音沙哑；画面右侧的老者\n8-12秒=林岩上前两步", ["山坳"], ["林岩", "青云门老者"], [])
T("块尾的站位注去掉", _pb[0]["blocks"][0][2] == "林岩挥斧劈藤，抹汗" and _pb[0]["blocks"][1][2] == "老者费力睁眼，声音沙哑", [b[2] for b in _pb[0]["blocks"]])
_db = {"camera": "中景", "cast": ["林岩", "青云门老者"], "blocks": [[0, 4, "老者缓缓伸出手", []], [4, 8, "林岩看手掌", []]], "end_state": {}, "says": []}
_ob = ds.assemble("【机位】中景。\n\n0—4秒：老者缓缓伸出手，指尖虚点。\n\n4—8秒：林岩看着手掌。", _db, [{"name": "林岩"}, {"name": "青云门老者"}], {})
T("「老者」开头的块不再补「林岩」", "0—4秒：老者缓缓伸出手" in _ob, _ob)

print("【P443】")
_pics = "\n\n".join("段落%d" % i for i in range(1, 11))
_g = ds.split_events(_pics, n_events=3, event_map={"1": [1, 2], "2": [5, 6, 7], "3": [8, 9, 10]})
T("按映射表分件，没映射的段落归前一件", len(_g) == 3 and "段落3" in _g[0] and "段落4" in _g[0] and _g[1].startswith("段落5"), _g)
_g2 = ds.split_events(_pics, n_events=3)
T("没映射表：段落多就按事件数均分", len(_g2) == 3, _g2)
T("段落数和事件数差不多：一段落一件", len(ds.split_events("a\n\nb\n\nc", n_events=3)) == 3)

print("【P444】")
_d4 = {"camera": "近景", "cast": ["林岩", "青云门老者"], "blocks": [[0, 4, "老者目光从窗外收回，直视林岩", []], [4, 8, "林岩看老者", []]], "end_state": {}, "says": []}
_o4 = ds.assemble("【机位】近景。\n\n0—4秒：老者位于画面右侧榻上，面朝左侧的林岩，两人相隔约三步。老者目光从窗外收回，直视林岩，嘴唇微动。\n\n4—8秒：林岩看着老者。", _d4, [{"name": "林岩"}, {"name": "青云门老者"}], {})
_b4 = _o4.split("0—4秒：")[1].split("\n")[0]
T("骨架句已在块里 → 挪到最前不重复", _b4.startswith("老者目光从窗外收回") and _b4.count("目光从窗外收回") == 1, _b4)

print("【P445】")
from cores import episode_ledger as el
_led = el.parse("人物：张明｜衣着=深蓝T恤左肩裂口｜伤=无｜手里=铁签｜称呼=无\n人物：小林｜衣着=针织背心湿透｜伤=左臂划伤已包扎｜手里=无｜称呼=叫张明「张哥」\n场景：沙滩｜新添=帐篷、烤架、晾鱼架\n结尾：时间=清晨｜天气=晴｜各人在哪=张明在火堆旁、小林在鱼架前", ["张明", "小林", "小爱", "小可"], ["沙滩", "海中"])
T("账本解析：人物/场景/结尾", _led["people"]["小林"]["称呼"] == "叫张明「张哥」" and "手里" not in _led["people"]["小林"] and _led["scenes"]["沙滩"].startswith("帐篷") and _led["when"]["时间"] == "清晨", _led)
_hv = [{"name": "沙滩"}, {"name": "海中"}, {"name": "礁石浅滩"}]
T("新地点对卡：礁石旁→礁石浅滩（共词），沙滩上→沙滩，森林→新卡", au._place_match("礁石旁", _hv)["name"] == "礁石浅滩" and au._place_match("沙滩上", _hv)["name"] == "沙滩" and au._place_match("森林", _hv) is None and au._place_match("林间", _hv) is None)
_d445 = au._chars_digest([{"name": "小林", "clothing": "浅米色针织背心，深灰色百褶裙"}], None, {"小林": "针织背心湿透；左臂划伤已包扎"})
T("人物卡摘要带此刻状态", "此刻（上一话结束时）：针织背心湿透；左臂划伤已包扎" in _d445, _d445)

print("【P446】")
_sp = "室外，远景处是郁郁葱葱、枝叶交错的岛内森林，中景处有一片被踩实的松软腐叶地面和几丛茂密的蕨类植物，前景处散落着几块长满青苔的灰褐色礁石"
T("没环境词的块补一句背景（先远景）", ds.ensure_env("小爱举起左手，指尖渗出一滴鲜血", _sp).endswith("。人物身后一直到画面深处都是郁郁葱葱、枝叶交错的岛内森林。"), ds.ensure_env("小爱举起左手，指尖渗出一滴鲜血", _sp))
T("有环境词的块不动", ds.ensure_env("小林踩在松软腐叶上，拨开藤蔓，蕨类植物擦过小腿", _sp) == "小林踩在松软腐叶上，拨开藤蔓，蕨类植物擦过小腿")

print("【P448】")
_e1, _st1 = ds.fix_shots([], [("张明", "x")], {1}, ["沙滩"], ["张明"], {"张明": {"side": "画面左侧"}}, "", None)
T("一块都没有不崩、状态原样带回", _e1 == [] and _st1["张明"]["side"] == "画面左侧")
_p2 = ds.parse("【段】地点=沙滩｜在场=小可｜秒=12｜机位=中景\n0-4秒=小可抬头望向画外 D1\n4-8秒=小可退后\n8-12秒=小可坐下\n结束=小可：沙地，面朝左，无，坐，画面右侧", ["沙滩"], ["张明", "小可"], [("张明", "别怕。")])
_f2, _ = ds.fix_shots(_p2, [("张明", "别怕。")], {1}, ["沙滩"], ["张明", "小可"], {}, "", None)
T("说台词的人并进在场", "张明" in _f2[0]["cast"] and _f2[0]["says"] == [["张明", "别怕。"]] or _f2[0]["says"] == [("张明", "别怕。")], _f2[0])
_p3 = ds.parse("【段1】\n地点=沙滩｜在场=张明｜秒=12｜机位=中景，固定\n目的=x\n0-6秒=张明蹲下\n6-12秒=张明站起\n结束=张明：沙地，面朝右，木板，站，画面左侧", ["沙滩"], ["张明"], [])
T("「【段1】」单独一行也能解析", len(_p3) == 1 and _p3[0]["place"] == "沙滩" and _p3[0]["cast"] == ["张明"] and len(_p3[0]["blocks"]) == 2, _p3)
T("没动词的手里物（木板）不丢", _p3[0]["end_state"]["张明"]["hands"] == "木板", _p3[0]["end_state"])
_p5 = ds.parse("【段】地点=沙滩｜在场=张明｜秒=20｜机位=中景\n0-20秒=张明走了很久", ["沙滩"], ["张明"], [])
_f5, _ = ds.fix_shots(_p5, [], set(), ["沙滩"], ["张明"], {}, "", None)
T("只有一块的 20 秒段也拆", all(x["seconds"] <= 15 for x in _f5) and len(_f5) == 2, [x["seconds"] for x in _f5])
_l4 = el.parse("人物：张明｜衣着＝深蓝T恤｜伤＝无｜手里＝铁签｜称呼：叫小林「小林」", ["张明"], [])
T("账本全角＝/：都认", _l4["people"]["张明"]["衣着"] == "深蓝T恤" and _l4["people"]["张明"]["手里"] == "铁签", _l4)

print("【P454】")
_ev8 = [{"n": i, "text": t} for i, t in enumerate("甲乙丙丁戊己庚辛".split() if False else ["我打算讲一个故事", "少年去森林探险", "森林很大", "他踩中陷阱被网住", "他在木屋醒来", "在木屋醒来之后", "他解绳开门出去", "街上都是女精灵"], 1)]
_g = os_.group_events(_ev8, lambda sysm, user, **k: "事1=1-3\n事2=4\n事3=5-7\n事4=8")
T("口述句子按模型分组合并成事、原句不改", [x["src"] for x in _g] == [[1, 2, 3], [4], [5, 6, 7], [8]] and _g[0]["text"] == "我打算讲一个故事。少年去森林探险。森林很大", _g)
os_._GROUP_CACHE.clear()
T("分组解析不出 → 原样", os_.group_events(_ev8, lambda *a, **k: "乱写") == _ev8)
T("模型全并成一件 → 原样", os_.group_events(_ev8, lambda *a, **k: "事1=1-8") == _ev8)
_ch454 = [{"name": "少年探险家", "sex": "男"}, {"name": "矮个子女精灵（持枪者）", "sex": "女"}]
_lift = os_.lift_inline_dialogue("她开口，嗓音清脆。女：你是我们的俘虏。女：怎么擅自出来了？她保持着持枪姿势。\n他反问。少年：俘虏？他抬手。\n矮个子女精灵：跟我走。", _ch454)
T("段落中间的「女：台词」提成独立行、说话人对到卡名", _lift.splitlines() == ["她开口，嗓音清脆。", "矮个子女精灵（持枪者）：你是我们的俘虏。", "矮个子女精灵（持枪者）：怎么擅自出来了？", "她保持着持枪姿势。", "他反问。", "少年探险家：俘虏？", "他抬手。", "矮个子女精灵（持枪者）：跟我走。"], _lift)
T("提行后编号器认出 4 句", len(os_.numbered_script(_lift)[1]) == 4)
T("普通句里的冒号不当台词", os_.lift_inline_dialogue("他看到：一条路。天亮了。", _ch454) == "他看到：一条路。天亮了。")
_p454 = ds.parse("【段】地点=陷阱处｜在场=少年探险家｜秒=13｜机位=中景\n0-4秒=少年踩空；4-9秒=麻网弹起缠住四肢；9-13秒=他停下挣扎悬在半空\n结束=少年探险家：中央，面朝左，空着，悬空，画面中央", ["陷阱处"], ["少年探险家"], [])
T("一行里三个时间块拆成三块、13 秒", len(_p454[0]["blocks"]) == 3 and _p454[0]["blocks"][2][1] == 13.0, _p454[0]["blocks"])
_sn454 = ["林地", "陷阱处", "木屋客房", "村庄街道"]
_sx454 = {"少年探险家": "男", "矮个子女精灵（持枪者）": "女"}
_raw454 = "【段】地点=木屋客房｜在场=少年探险家、矮个子女精灵｜秒=13｜机位=中景，平视，固定，少年在左侧，精灵在右侧\n0-4秒=少年探险家：双眼睁开\n4-9秒=少年探险家：缓缓坐起身\n9-13秒=矮个子女精灵：站在画面右侧，持枪注视着少年\n结束=少年探险家：左侧，面朝右，空着，坐，画面左侧；矮个子女精灵：右侧，面朝左，持枪，站，画面右侧"
_f454, _ = ds.fix_shots(ds.parse(_raw454, _sn454, list(_sx454), []), [], set(), _sn454, list(_sx454), {}, "陷阱处", None, ev_text="意识从混沌中浮起，他睁开双眼。他躺在木床上，缓缓坐起身。", char_sex=_sx454, prev_cast=["少年探险家"])
T("剧本没提到的人剪出在场，她的块、结束状态、机位里的左右都去掉", _f454[0]["cast"] == ["少年探险家"] and len(_f454[0]["blocks"]) == 2 and list(_f454[0]["end_state"]) == ["少年探险家"] and "精灵" not in _f454[0]["camera"], _f454[0])
_raw454b = _raw454.replace("地点=木屋客房", "地点=林地")
_f454b, _ = ds.fix_shots(ds.parse(_raw454b, _sn454, list(_sx454), []), [], set(), _sn454, list(_sx454), {}, "村庄街道", None, ev_text="他刚迈出脚步，一根长枪横在面前，挡住去路。持枪的是名矮个子女精灵，枪杆点在他的胸口。", char_sex=_sx454, prev_cast=["少年探险家"])
T("地点没证据、街道有证据 → 换成村庄街道；两人都提到就都在场", _f454b[0]["place"] == "村庄街道" and set(_f454b[0]["cast"]) == set(_sx454), _f454b[0])
_f454c, _ = ds.fix_shots(ds.parse(_raw454, _sn454, list(_sx454), []), [], set(), _sn454, list(_sx454), {}, "陷阱处", None, ev_text="意识从混沌中浮起，他睁开双眼。", char_sex=_sx454, prev_cast=["少年探险家"])
T("谁都没证据的地点信模型（木屋客房不动）", _f454c[0]["place"] == "木屋客房")
_f454d, _ = ds.fix_shots(ds.parse(_raw454.replace("地点=木屋客房", "地点=林地"), _sn454, list(_sx454), []), [], set(), _sn454, list(_sx454), {}, "木屋客房", None, ev_text="【D1】队长：你擅自闯入我们的村庄。\n他坐起身。", char_sex=_sx454, prev_cast=["少年探险家"])
T("台词里的地名不算证据；没人换地方就接着上一处", _f454d[0]["place"] == "木屋客房", _f454d[0]["place"])
T("骨架里的群众词抽出来", ds.extras_phrases({"blocks": [[0, 4, "几名女精灵鱼贯而入；少年睁开眼", []], [4, 8, "女子们各自忙碌", []]]}, list(_sx454)) == ["几名女精灵", "女子们"])
_kp, _dr = au._ground_cards([{"name": "少年探险家"}, {"name": "矮个子女精灵（持枪者）"}, {"name": "精灵队长（村庄首领）"}, {"name": "白肤女精灵（村民）"}, {"name": "路人甲"}], "少年探险家走进村庄。矮个子女精灵拦住他。队长：我是队长。几名精灵围上来。", {"story_mode": "single"})
T("口述模式：卡名片段（队长/精灵）在正文里有就留、路人甲删", [c["name"] for c in _kp] == ["少年探险家", "矮个子女精灵（持枪者）", "精灵队长（村庄首领）", "白肤女精灵（村民）"] and _dr == ["路人甲"], (_kp, _dr))

_pp = au._place_paragraphs("木屋村落", "林恩在森林里走。\n\n他走出木屋，看见一条街道，两旁是木屋村落。\n\n她们围在床边。")
T("场景空间只喂提到这个地方的段落", "木屋村落" in _pp and "床边" not in _pp and "森林里走" not in _pp, _pp)
T("远景句去掉「远处是/视野尽头是」", ds.env_sentence("室外，远处是连绵的森林天际线，中景是木屋") == "连绵的森林天际线" and ds.env_sentence("室外，视野尽头是山峦轮廓，中景是古木") == "山峦轮廓")
T("卡名别名：男主→唯一男卡、人物类型词→卡", os_.card_aliases([{"name": "林恩", "sex": "男"}, {"name": "艾拉", "sex": "女", "char_type": "精灵守卫 / 小个子精灵"}, {"name": "瑟琳娜", "sex": "女", "char_type": "精灵队长"}]).get("男主") == "林恩" and os_.canon("小个子精灵抬枪", os_.card_aliases([{"name": "艾拉", "sex": "女", "char_type": "精灵守卫 / 小个子精灵"}])) == "艾拉")

print("【P455】")
_q455 = os_.quoted_lines("小个子女精灵拿着一个长枪，就碰了一下男主，说。喂，你是我们的俘虏，你怎么擅自出来了？然后男主就很疑惑，就是我怎么就被俘虏了？然后她就跟男主说，你擅自闯入了我们的村庄。按道理是要严罚你，但是我们的村子已经很多年没有男性了，所以我们暂时先观察你一下。这个就是结尾的时候，几个女精灵摸男主的身体。")
T("口述原话抽出三句、多句原话并成一条", _q455 == ["喂，你是我们的俘虏，你怎么擅自出来了？", "我怎么就被俘虏了？", "你擅自闯入了我们的村庄。按道理是要严罚你，但是我们的村子已经很多年没有男性了，所以我们暂时先观察你一下"], _q455)
T("「问她们，」后面整句是原话", os_.quoted_lines("男主问她们，你们找到什么了？一个女孩说，只找到几个野果。然后大家吃。") == ["你们找到什么了？", "只找到几个野果"])
T("没有对白的口述抽不出原话", os_.quoted_lines("一个少年家境贫寒。他救了一个老者。老者养伤之后带他去门派修炼。") == [])
T("原话覆盖判定：改短了算没写", os_.quote_covered("精灵队长：按道理要罚你。\n精灵队长：先观察一下。", _q455[2]) is False and os_.quote_covered("精灵队长：你擅自闯入了我们的村庄。\n精灵队长：按道理是要严罚你，但是我们的村子已经很多年没有男性了，所以我们暂时先观察你一下。", _q455[2]) is True)
_rep455 = os_.check_prose("林冠走进村子。\n精灵队长：按道理要罚你。", [{"n": 1, "text": "x"}], "", {1: [1]}, [], [], quotes=_q455[2:])
T("核对报告记下没写进去的原话", _rep455["missing_quotes"] == _q455[2:] and _rep455["verdict"] == "fail", _rep455)
_dd455 = os_.dedupe_dialogue_lines("林冠：都过来看看。\n精灵队长：都过来看看。\n林冠：好多人啊。\n精灵队长：别动。\n林冠：嗯。\n林冠：哦。\n小精灵：喂！站住！\n林冠：好吧。")
T("同一句相邻换人只留后一个、单字应答删掉、「喂！站住」留着", _dd455.splitlines() == ["精灵队长：都过来看看。", "林冠：好多人啊。", "精灵队长：别动。", "小精灵：喂！站住！"], _dd455)
_D455 = [("甲", "一"), ("甲", "二"), ("甲", "三"), ("甲", "四"), ("甲", "五")]
_p455 = ds.parse("【段】地点=沙滩｜在场=甲｜秒=13｜机位=中景\n0-4秒=甲说话 D1 D2\n4-9秒=甲继续 D3 D4\n9-13秒=甲收尾 D5\n结束=甲：沙地，面朝前，无，站，画面中央", ["沙滩"], ["甲"], _D455)
_f455, _ = ds.fix_shots(_p455, _D455, {1, 2, 3, 4, 5}, ["沙滩"], ["甲"], {}, "", None)
T("一段超过 4 句台词按块拆成两段", len(_f455) == 2 and sum(len(b[3]) for b in _f455[0]["blocks"]) <= 4 and sum(len(b[3]) for b in _f455[1]["blocks"]) == 1 and _f455[0]["seconds"] >= 8, [(x["seconds"], [b[3] for b in x["blocks"]]) for x in _f455])

print("\n%d/%d 通过" % (_ok, _n))
