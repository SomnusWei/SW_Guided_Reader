#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交互式 HTML 试卷生成器（zhongyi-zonghe 功能六）

输入 JSON 题库（由 AI 出题后按 schema 构造），输出文件：
  1. {name}.html      —— 交互式试卷（**默认唯一产出**）：顶部填姓名，按题型作答（单选/多选/填空/简答），
                         底部提交后原地批改：客观题自动判分，主观题给参考答案+要点自评，
                         成绩卡显示姓名、得分明细与总分
  2. {name}_答案卷.html —— 题目+答案+解析+出处（打印友好，可转 PDF 交付）
                         **默认不出**，仅当用户明确要求答案卷/打印版/PDF 时加 --with-answers 生成

JSON 题目 schema（exam.json）：
{
  "title": "中医基础理论模拟试卷",
  "outline": "覆盖：阴阳五行、脏腑、气血津液",   // 出题大纲（仅供内部命题取材，不上卷面）
  "total_score": 100,
  "duration": "60分钟",                        // 可选
  "questions": [
    {"type": "single",   "score": 2, "stem": "题干",
     "options": ["A. …", "B. …", …], "answer": 0,           // answer=正确项索引(0起)
     "explanation": "解析", "source": "《中医基础理论》第一章"},
    {"type": "multiple", "score": 2, "stem": "…",
     "options": […], "answers": [0, 2], …},
    {"type": "fill",     "score": 2, "stem": "阳浮者，____",
     "answers": ["热自发"], …},                              // 任一匹配即得分
    {"type": "short",    "score": 10, "stem": "试述…",
     "reference": "参考答案全文", "key_points": ["要点1", "要点2"], …},
    // short 题可选 keywords：按“作答中是否命中参考答案的实质内容”自动批改给分——
    // 注意：命中词请取参考答案里的关键结论/术语（病名、证型、治法要点、方名等）或同义词；
    //       不要把题干要求词（如“辨病”“代表方”本身）当命中词——那只是答题要求，不是答案。
    {"type": "short", "score": 5, "stem": "医案分析：…（要求答出辨病、辨证、核心病机、治法、代表方）",
     "reference": "辨病：感冒（太阳伤寒表实证）；辨证：风寒束表证；核心病机：风寒外束、肺气失宣；治法：发汗解表、宣肺平喘；代表方：麻黄汤。",
     "key_points": ["辨病准确", "辨证准确", "病机准确", "治法一致", "方药正确"],
     "keywords": [
       {"words": ["感冒", "太阳伤寒"], "score": 1},        // 辨病（答出其一即命中）
       {"words": ["风寒束表", "表实证"], "score": 1},      // 辨证
       {"words": ["风寒外束", "肺气失宣"], "score": 1},    // 核心病机
       {"words": ["发汗解表", "宣肺平喘"], "score": 1},    // 治法
       {"word": "麻黄汤", "score": 1}]}                     // 代表方 —— 全命中得满 5 分
  ]
}

用法：
  python build_exam_html.py --exam exam.json --output-dir 试卷输出 --name 中医基础模拟卷
  # 默认会随机打乱选择题选项顺序，避免“正确答案位置集中（如几乎全为 A）”。
  # 需要固定某次顺序用 --seed 复现；需要保留原选项顺序时加 --no-shuffle：
  python build_exam_html.py --exam exam.json --output-dir 试卷输出 --name 模拟卷 --no-shuffle
  python build_exam_html.py --exam exam.json --output-dir 试卷输出 --name 模拟卷 --seed 42
  # PWA 部署时可自定义成绩上报接口（默认 POST /api/exam/submit）：
  python build_exam_html.py --exam exam.json --output-dir 试卷输出 --name 中医基础模拟卷 --submit-url /api/exam/submit
  # 答案卷默认不出；仅用户明确要答案卷/打印版/PDF 时才加 --with-answers：
  python build_exam_html.py --exam exam.json --output-dir 试卷输出 --name 中医基础模拟卷 --with-answers
  # 再由答案卷转 PDF（可选）：
  "C:/Program Files/Google/Chrome/Application/chrome.exe" --headless --disable-gpu \
    --print-to-pdf="答案卷.pdf" --no-pdf-header-footer "试卷输出/xxx_答案卷.html"

注意：选择题选项会被随机打乱并重贴 A/B/C… 前缀；解析（explanation）文字内请勿引用选项字母，
应引用选项文字内容；确需按字母解析的题目可加 --no-shuffle 保留原序。
short 主观题可配置 keywords 关键词自动批改（每条命中给分，详见上方 schema 示例）。
"""

import os
import re
import json
import random
import hashlib
import argparse
from datetime import date

# 成绩上报接口默认路径（服务器需实现 POST /api/exam/submit 接收成绩 JSON）
DEFAULT_SUBMIT_URL = "/api/exam/submit"

# 题型元信息
TYPE_META = {
    "single":   {"label": "单选题",   "hint": "单选（选择一个最佳答案）"},
    "multiple": {"label": "多选题",   "hint": "多选（选出所有正确选项，错选/漏选不得分）"},
    "fill":     {"label": "填空题",   "hint": "填空（____ 处填写答案）"},
    "short":    {"label": "简答题",   "hint": "简答（文字作答，提交后对照参考答案自评）"},
}

# ---------- 题干清洗 ----------
# 卷面不得出现难度/场景标记（如【执医】【考研】【简单】等），生成时统一清除题干开头的此类标记
_DIFFICULTY_MARKER_RE = re.compile(
    r"^\s*[【\[（(]\s*(?:"
    r"执医|执业医师|执业助理医师|助理医师|助理|中医执业医师|西医执业医师|"
    r"考研|研究生|学硕|专硕|规培|住院医师|"
    r"期末|期中|毕业|升学|结业|"
    r"自测|跟读|磨耳|模拟|练习|"
    r"简单|较易|容易|易|中等|中等难度|较难|困难|难|高难度|"
    r"难度[:：]?\s*[易中难]?"
    r")\s*[】\]）)]\s*"
)


def clean_stem(stem):
    """移除题干开头的难度/场景标记（如【执医】【考研】），卷面不体现难度与场景"""
    s = stem or ""
    while True:
        new = _DIFFICULTY_MARKER_RE.sub("", s, count=1)
        if new == s:
            return s
        s = new


# ---------- 选择题选项随机打乱 ----------
# 默认在生成试卷时打乱选择题选项顺序，避免“正确答案大概率固定在某一位（如几乎全为 A）”
OPTION_PREFIXES = "ABCDEFGHIJKLMNOP"
_OPTION_PREFIX_RE = re.compile(r"^[A-Za-z][.、．)）:：]\s*")


def _strip_option_prefix(opt):
    """去掉选项文本自带的首字母前缀（A. / B． / C、 等），返回正文"""
    return _OPTION_PREFIX_RE.sub("", str(opt)).strip()


def shuffle_question_options(questions, rng=None):
    """随机打乱 single/multiple 选择题的选项顺序，并同步修正 answer/answers 索引。

    - 打乱前先剥离旧字母前缀，打乱后按新位置重贴 A/B/C… 前缀，
      保证考卷卷面、在线判分、答案卷三处始终一致；
    - 解析（explanation）文字内请勿引用选项字母，应引用选项文字内容，
      否则打乱后字母不再对应原选项（此类题目可用 --no-shuffle 保留原序）。
    返回被洗牌的题目数。
    """
    if rng is None:
        rng = random
    shuffled = 0
    for q in questions:
        if q.get("type") not in ("single", "multiple"):
            continue
        opts = q.get("options")
        if not isinstance(opts, list) or len(opts) < 2:
            continue
        contents = [_strip_option_prefix(o) or str(o).strip() for o in opts]
        order = list(range(len(contents)))
        rng.shuffle(order)
        q["options"] = [f"{OPTION_PREFIXES[j]}. {contents[i]}" for j, i in enumerate(order)]
        old_to_new = [order.index(i) for i in range(len(order))]
        if q["type"] == "single" and "answer" in q:
            q["answer"] = old_to_new[int(q["answer"])]
        elif q["type"] == "multiple" and q.get("answers"):
            q["answers"] = sorted(old_to_new[int(a)] for a in q["answers"])
        shuffled += 1
    return shuffled


def esc(s):
    """HTML 文本转义"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def validate_exam(data):
    """校验题库 JSON 结构，返回错误列表"""
    errors = []
    for key in ("title", "total_score", "questions"):
        if key not in data:
            errors.append(f"缺少必填字段: {key}")
    if errors:
        return errors
    total = sum(q.get("score", 0) for q in data["questions"])
    declared = data["total_score"]
    if total != declared:
        errors.append(f"各题分值合计 {total} ≠ 声明总分 {declared}（需一致）")
    for i, q in enumerate(data["questions"]):
        t = q.get("type")
        if t not in TYPE_META:
            errors.append(f"第{i+1}题未知题型: {t}")
            continue
        if not q.get("stem"):
            errors.append(f"第{i+1}题缺少题干 stem")
        if t in ("single", "multiple"):
            if not q.get("options") or len(q["options"]) < 3:
                errors.append(f"第{i+1}题选项不足 3 个")
            if t == "single" and "answer" not in q:
                errors.append(f"第{i+1}题缺少答案 answer")
            if t == "multiple" and not q.get("answers"):
                errors.append(f"第{i+1}题缺少答案 answers")
        if t == "fill" and not q.get("answers"):
            errors.append(f"第{i+1}题缺少答案 answers")
        if t == "short" and not q.get("reference"):
            errors.append(f"第{i+1}题缺少参考答案 reference")
        if t == "short" and q.get("keywords") is not None:
            kws = q["keywords"]
            if not isinstance(kws, list) or not kws:
                errors.append(f"第{i+1}题 keywords 需为非空数组")
            else:
                ksum = 0
                for j, k in enumerate(kws):
                    if not isinstance(k, dict):
                        errors.append(f"第{i+1}题 keywords[{j}] 需为对象 {{word 或 words, score}}")
                        continue
                    ws = k.get("words") or ([k["word"]] if k.get("word") is not None else [])
                    if not (isinstance(ws, list) and ws and all(isinstance(w, str) and w.strip() for w in ws)):
                        errors.append(f"第{i+1}题 keywords[{j}] 缺少关键词 word/words")
                        continue
                    s = k.get("score", 1)
                    if isinstance(s, bool) or not isinstance(s, (int, float)) or s <= 0:
                        errors.append(f"第{i+1}题 keywords[{j}] score 需为正数")
                        continue
                    ksum += s
                if ksum != q.get("score", 0):
                    errors.append(f"第{i+1}题 keywords 分值合计 {ksum} ≠ 本题分值 {q.get('score', 0)}（需一致）")
    return errors


def render_question_html(q, idx):
    """渲染单题 HTML（作答区按题型）"""
    t = q["type"]
    meta = TYPE_META[t]
    parts = []
    parts.append(f'<div class="question" data-qid="{idx}" data-type="{t}" data-score="{q["score"]}">')
    parts.append(
        f'<div class="q-head"><span class="q-no">第 {idx} 题</span>'
        f'<span class="q-type t-{t}">{meta["label"]}</span>'
        f'<span class="q-score">（{q["score"]} 分）</span>'
        f'<span class="verdict" id="verdict-{idx}"></span></div>')
    parts.append(f'<div class="q-stem">{esc(q["stem"])}</div>')

    if t == "single":
        for j, opt in enumerate(q["options"]):
            parts.append(
                f'<label class="opt opt-{idx}-{j}"><input type="radio" name="q{idx}" value="{j}"> '
                f'<span>{esc(opt)}</span></label>')
    elif t == "multiple":
        for j, opt in enumerate(q["options"]):
            parts.append(
                f'<label class="opt opt-{idx}-{j}"><input type="checkbox" name="q{idx}" value="{j}"> '
                f'<span>{esc(opt)}</span></label>')
    elif t == "fill":
        parts.append(
            f'<input type="text" class="fill-input" id="fill-{idx}" '
            f'placeholder="在此填写答案" autocomplete="off">')
    elif t == "short":
        parts.append(
            f'<textarea class="short-input" id="short-{idx}" rows="4" '
            f'placeholder="在此作答"></textarea>')
        kw = q.get("keywords")
        if kw:
            ksum = int(sum(int(k.get("score", 1) or 0) for k in kw))
            parts.append(
                '<div style="font-size:12.5px;color:#1d6f42;background:#eafaf1;'
                'border:1px dashed #5cb885;border-radius:6px;padding:5px 10px;margin:6px 0 2px;">'
                f'⚙️ 本题按参考答案实质内容自动批改（共 {len(kw)} 项要点 / 满分 {ksum} 分），'
                "作答时请把各项分析写全写清。</div>")
        parts.append(
            f'<div class="self-grade hidden" id="sg-{idx}"'
            + (' style="display:none" data-kw="1"' if kw else '')
            + '>自评分：'
            f'<input type="number" class="sg-input" id="sgv-{idx}" min="0" '
            f'max="{q["score"]}" value="{q["score"]}"> / {q["score"]} 分'
            f'（对照参考答案与要点自评，未填按 0 分计）</div>')

    # 批改反馈区（提交后填充）
    parts.append(f'<div class="feedback hidden" id="fb-{idx}"></div>')
    parts.append("</div>")
    return "\n".join(parts)


JS_GRADER = r"""
const EXAM = __EXAM_DATA__;

// —— 学习中心账号自动识别（与 PWA 学习中心“账号关联”对接）——
// 识别来源（按优先级）：URL ?student=<username> / ?u=<username> → 同源 localStorage['lc_user'].username
// 识别到的账号写入 window.__lcUser，仅用于成绩归属上报；卷首姓名仍可自行修改。
(function detectLcAccount() {
  let account = "";
  try {
    const qp = new URLSearchParams(window.location.search);
    account = (qp.get("student") || qp.get("u") || "").trim();
    if (!account) {
      const raw = localStorage.getItem("lc_user");
      if (raw) {
        const lc = JSON.parse(raw);
        if (lc && typeof lc.username === "string" && lc.username.trim()) {
          account = lc.username.trim();
        }
      }
    }
  } catch (e) { /* URL/localStorage/JSON 异常时静默降级，不影响作答 */ }
  if (!account) return;
  window.__lcUser = account;
  const box = document.getElementById("student-name");
  if (box && !box.value.trim()) {
    box.value = account;            // 预填，学生可修改
    box.placeholder = "已识别学习中心账号 " + account + "，可修改";
  }
})();

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// 文本归一化：全角字母数字→半角→转小写→去掉所有空白与常见中英文标点。
// 作答与关键词同时归一后做包含匹配，可忽略全/半角、顿逗号、引号括号、间隔符等书写差异；
// 也支持关键词条自身含连接标点（如“涤痰祛瘀，宣肺平喘”），两端归一后一致即可命中。
function normText(s) {
  return String(s || "")
    .replace(/[\uFF01-\uFF5E]/g, c => String.fromCharCode(c.charCodeAt(0) - 0xFEE0)) // 全角→半角
    .toLowerCase()
    .replace(/[\s\u3000，,。．.、；;：:！!？?·・/／\\()（）[\]【】《》〈〉“”‘’"'‘’…—–-]/g, "")
    .trim();
}

function gradeExam() {
  if (window.__REVIEW_MODE__) { alert("成绩回显模式不可提交，本卷已锁定"); return; }
  const name = document.getElementById("student-name").value.trim();
  if (!name) { alert("请先在卷首填写学生姓名！"); document.getElementById("student-name").focus(); return; }

  let objectiveScore = 0, objectiveMax = 0, subjectiveMax = 0, subjectiveSelf = 0;
  let anyAuto = false, autoOnly = 0;
  let answered = 0, correctCount = 0, objectiveCount = 0;
  const qResults = [];

  EXAM.questions.forEach((q, i) => {
    const idx = i + 1;
    const qDiv = document.querySelector('[data-qid="' + idx + '"]');
    const verdict = document.getElementById("verdict-" + idx);
    const fb = document.getElementById("fb-" + idx);
    fb.innerHTML = ""; fb.classList.remove("hidden");
    let gained = 0, full = q.score, isObjective = true;
    let rec = { qid: idx, type: q.type, max_score: full, score: 0, answer: null, correct: null };

    if (q.type === "single") {
      objectiveMax += full; objectiveCount++;
      const chosen = document.querySelector('input[name="q' + idx + '"]:checked');
      if (chosen) { answered++; rec.answer = Number(chosen.value); }
      if (chosen && Number(chosen.value) === q.answer) { gained = full; correctCount++; }
      rec.correct = (rec.answer === q.answer);
      // 高亮：正确答案绿色，错选红色
      q.options.forEach((_, j) => {
        const el = document.querySelector(".opt-" + idx + "-" + j);
        el.classList.remove("opt-right", "opt-wrong");
        if (j === q.answer) el.classList.add("opt-right");
        else if (chosen && Number(chosen.value) === j) el.classList.add("opt-wrong");
      });
      fb.innerHTML = feedbackHTML(chosen, q);
      disableGroup('input[name="q' + idx + '"]');
    } else if (q.type === "multiple") {
      objectiveMax += full; objectiveCount++;
      const checked = [...document.querySelectorAll('input[name="q' + idx + '"]:checked')].map(x => Number(x.value)).sort();
      if (checked.length) { answered++; rec.answer = checked; }
      const std = [...q.answers].sort((a,b)=>a-b);
      if (checked.length && checked.length === std.length && checked.every((v, k) => v === std[k])) {
        gained = full; correctCount++;
      }
      rec.correct = (Array.isArray(rec.answer) && rec.answer.length === std.length && rec.answer.every((v, k) => v === std[k]));
      q.options.forEach((_, j) => {
        const el = document.querySelector(".opt-" + idx + "-" + j);
        el.classList.remove("opt-right", "opt-wrong");
        if (std.includes(j)) el.classList.add("opt-right");
        else if (checked.includes(j)) el.classList.add("opt-wrong");
      });
      const ansLabel = std.map(j => q.options[j].split(/[.、．]/)[0] || ("选项" + (j+1))).join("、");
      fb.innerHTML = (checked.length ? "" : '<p class="miss">⚠️ 未作答</p>') +
        '<p><b>正确答案：</b>' + esc(ansLabel) + answerTail(q) + "</p>";
      disableGroup('input[name="q' + idx + '"]');
    } else if (q.type === "fill") {
      objectiveMax += full; objectiveCount++;
      const inp = document.getElementById("fill-" + idx);
      const val = normText(inp.value);
      if (val) { answered++; rec.answer = inp.value; }
      const ok = val && q.answers.some(a => normText(a) === val);
      if (ok) { gained = full; correctCount++; }
      rec.correct = ok;
      inp.disabled = true;
      inp.classList.add(ok ? "fill-right" : "fill-wrong");
      fb.innerHTML = '<p><b>你的答案：</b>' + esc(inp.value || "（未作答）") +
        '<br><b>正确答案：</b>' + esc(q.answers.join(" ／ ")) + answerTail(q) + "</p>";
    } else if (q.type === "short") {
      isObjective = false; subjectiveMax += full;
      const ta = document.getElementById("short-" + idx);
      const sg = document.getElementById("sg-" + idx);
      const sgv = document.getElementById("sgv-" + idx);
      ta.disabled = true;
      const taText = ta.value || "";
      const answeredThis = !!taText.trim();
      if (answeredThis) { answered++; rec.answer = taText; }
      rec.correct = null;
      const kwList = Array.isArray(q.keywords) && q.keywords.length ? q.keywords : null;
      if (kwList) {
        // —— 关键词自动批改：命中一条得对应分（可多条累加、封顶本题分值）——
        anyAuto = true;
        const nv = normText(taText);
        const kwHits = kwList.map(k => {
          const ws = (Array.isArray(k.words) && k.words.length) ? k.words
            : (k.word != null ? [k.word] : []);
          const s = Math.max(Number(k.score) || 1, 0);
          const hit = !!nv && ws.some(w => { const nw = normText(w); return !!nw && nv.indexOf(nw) >= 0; });
          return { label: ws.length ? ws.join("／") : (k.word || ""), s: s, hit: hit };
        });
        let autoSum = 0;
        kwHits.forEach(h => { if (h.hit) autoSum = Math.min(autoSum + h.s, full); });
        gained = autoSum; subjectiveSelf += autoSum; autoOnly += autoSum;
        rec.self_score = autoSum;
        fb.innerHTML =
          "<p><b>你的作答：</b>" + esc(taText || "（未作答）") + "</p>" +
          '<p style="color:#1d6f42;font-weight:700;margin:4px 0 2px;">⚙️ 关键词自动批改：' + autoSum + " / " + full + " 分</p>" +
          '<ul style="margin:0;padding-left:20px;font-size:13.5px;">' +
          kwHits.map(h => "<li style='color:" + (h.hit ? "#16a34a" : "#c62828") + "'>" +
            esc(h.label) + "：" + (h.hit ? "✓ 命中 +" + h.s : "✗ 未命中 0") + " 分</li>").join("") +
          "</ul>" +
          '<div class="ref-answer"><p><b>📝 参考答案：</b></p>' +
          '<p class="ref-text">' + esc(q.reference).replace(/\n/g, "<br>") + "</p>" +
          (q.key_points ? '<p><b>🎯 评分要点：</b></p><ul>' +
            q.key_points.map(p => "<li>" + esc(p) + "</li>").join("") + "</ul>" : "") +
          answerTail(q) + "</div>";
      } else {
        // —— 原自评模式（无 keywords 配置时）——
        const selfVal = Math.min(Math.max(Number(sgv.value) || 0, 0), full);
        subjectiveSelf += selfVal;
        gained = selfVal;
        rec.self_score = selfVal;
        sg.classList.remove("hidden");
        fb.innerHTML = '<div class="ref-answer"><p><b>📝 参考答案：</b></p>' +
          '<p class="ref-text">' + esc(q.reference).replace(/\n/g, "<br>") + "</p>" +
          (q.key_points ? '<p><b>🎯 评分要点：</b></p><ul>' +
            q.key_points.map(p => "<li>" + esc(p) + "</li>").join("") + "</ul>" : "") +
          answerTail(q) + "</div>";
      }
    }
    rec.score = gained;
    if (isObjective) objectiveScore += gained;
    qResults.push(rec);
    qDiv.classList.add(gained >= full ? "q-full" : (gained > 0 ? "q-partial" : "q-zero"));
    verdict.textContent = gained >= full ? "✓ " + gained + " 分" : (gained > 0 ? "△ " + gained + " 分" : "✗ 0 分");
    verdict.className = "verdict " + (gained >= full ? "v-right" : (gained > 0 ? "v-partial" : "v-wrong"));
    if (q.type === "short") {
      const kw = Array.isArray(q.keywords) && q.keywords.length;
      verdict.textContent = (kw ? "自动 " : "自评 ") + gained + "/" + full + " 分";
    }
  });

  const total = objectiveScore + subjectiveSelf;
  const rate = EXAM.total_score ? Math.round(total / EXAM.total_score * 100) : 0;
  const subjLabel = anyAuto ? "主观题（关键词自动批改/自评）" : "主观题（自评）";
  const subjNote = anyAuto
    ? "关键词自动批改得分已自动计入；未配置关键词的简答题以自评分为准"
    : "主观题得分以自评为准，可对照上方参考答案与评分要点";
  const card = document.getElementById("score-card");
  card.classList.remove("hidden");
  card.innerHTML =
    '<h2>📊 成绩单</h2>' +
    '<div class="score-grid">' +
    '<div class="s-item"><div class="s-label">学生姓名</div><div class="s-value" id="recorded-name">' + esc(name) + "</div></div>" +
    '<div class="s-item"><div class="s-label">客观题（自动批改）</div><div class="s-value">' + objectiveScore + " / " + objectiveMax + ' 分</div></div>' +
    '<div class="s-item"><div class="s-label">' + subjLabel + "</div><div class='s-value'>" + subjectiveSelf + " / " + subjectiveMax + ' 分</div></div>' +
    '<div class="s-item"><div class="s-label">答题情况</div><div class="s-value">' + answered + " / " + EXAM.questions.length + ' 题已答</div></div>' +
    '<div class="s-item"><div class="s-label">客观题正确率</div><div class="s-value">' + (objectiveCount ? Math.round(correctCount / objectiveCount * 100) : 0) + '%</div></div>' +
    '<div class="s-item s-total"><div class="s-label">总分</div><div class="s-value">' + total + " / " + EXAM.total_score + ' 分</div></div>' +
    "</div>" +
    '<div class="score-bar"><div class="score-bar-fill" style="width:' + rate + '%"></div></div>' +
    '<p class="score-note">提交时间已记录 · ' + subjNote + "</p>" +
    '<p class="score-note" id="upload-note"></p>' +
    '<button type="button" id="retry-upload" class="retry-btn hidden" onclick="retryUpload()">重新上报成绩</button>';
  if (card.scrollIntoView) card.scrollIntoView({ behavior: "smooth" });
  document.getElementById("submit-btn").disabled = true;
  document.getElementById("submit-btn").textContent = "已提交并批改";

  // —— 成绩上报：构造结构化 JSON，POST 到服务器约定接口 ——
  const payload = {
    type: "exam_result",            // 固定标识：服务器以此识别成绩上报请求
    version: 1,
    exam_id: EXAM.exam_id,
    exam_title: EXAM.title,
    student_name: name,
    submitted_at: new Date().toISOString(),
    total_score: EXAM.total_score,  // 满分
    final_score: total,             // 总得分（客观自动 + 主观[关键词自动批改/自评]）
    objective_score: objectiveScore,
    objective_max: objectiveMax,
    subjective_self: subjectiveSelf,   // 主观合计分 = 关键词自动批改得分 + 自评得分
    subjective_auto: autoOnly,         // 其中关键词自动批改得分
    subjective_max: subjectiveMax,
    answered_count: answered,
    question_count: EXAM.questions.length,
    correct_count: correctCount,
    objective_count: objectiveCount,
    accuracy: objectiveCount ? Math.round(correctCount / objectiveCount * 100) : 0,
    answers: qResults               // 逐题明细：qid/type/score/max_score/answer/correct/self_score
  };
  // 学习中心账号归属：若已识别到账号（URL ?student/?u 或 localStorage['lc_user']），
  // 则显式携带 student_username，服务器据此归属（优先级高于卷内可修改的 student_name）。
  if (window.__lcUser) { payload.student_username = window.__lcUser; }
  reportResult(payload);
}

/**
 * 成绩上报（成功后卷面明确回执，失败不再静默）：
 *  1. fetch(keepalive) 为主 —— 能读到服务器真实响应（200 入库 / 400 被拒），据此给出准确提示
 *  2. sendBeacon 兜底 —— fetch 抛错（离线、页面正在卸载）时再补一次；
 *     服务器对「同一考试同一学生」只记首次，重复上报会被忽略，无副作用
 *  3. localStorage 留档 —— 无论成败都本地保存，供「重新上报」按钮复用
 * 结果写入 #upload-note；未成功时显示 #retry-upload 按钮。
 */
function reportResult(payload) {
  const body = JSON.stringify(payload);
  const url = EXAM.submit_url || "/api/exam/submit";
  try {
    localStorage.setItem("exam_result:" + payload.exam_id + ":" + payload.submitted_at, body);
  } catch (e) { /* ignore */ }
  window.__LAST_EXAM_RESULT__ = payload;
  setUploadNote("pending");
  const beacon = () => {
    let sent = false;
    try {
      sent = !!(navigator.sendBeacon &&
        navigator.sendBeacon(url, new Blob([body], { type: "application/json" })));
    } catch (e) { /* ignore */ }
    setUploadNote(sent ? "unconfirmed" : "fail", "网络异常");
  };
  try {
    fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                 body: body, keepalive: true })
      .then(r => r.json().catch(() => ({})).then(d => {
        if (r.ok) setUploadNote("ok");
        else setUploadNote("rejected", (d && d.message) || ("服务器返回 " + r.status));
      }))
      .catch(beacon);
  } catch (e) {
    beacon();
  }
}

// 重新上报：复用本机留档的本次成绩
function retryUpload() {
  const payload = window.__LAST_EXAM_RESULT__;
  if (!payload) { setUploadNote("fail", "本机未找到本次成绩留档"); return; }
  reportResult(payload);
}

function setUploadNote(state, extra) {
  const note = document.getElementById("upload-note");
  if (!note) return;
  const btn = document.getElementById("retry-upload");
  const url = EXAM.submit_url || "/api/exam/submit";
  const text = {
    ok: "✅ 成绩已上报至服务器（POST " + url + "），并已本地留档",
    pending: "⏳ 正在上报成绩…",
    unconfirmed: "⚠️ 成绩上报结果未确认（已尝试在页面卸载时补发，无法读取服务器应答）。成绩已在本机留档，可点下方按钮复查是否入库",
    // 服务器已应答但拒收（如考试已截止）：重试无用，提示把原因反馈给老师
    rejected: "⚠️ 成绩未上报：" + (extra || "服务器拒收") +
              "。成绩已在本机留档，请把以上原因告知老师",
    // 网络层失败：联网后重试可能成功
    fail: "⚠️ 成绩未上报：" + (extra || "网络异常") +
          "。成绩已保存在本机，联网后点下方按钮重新上报"
  }[state] || "";
  note.textContent = text;
  if (btn) btn.classList.toggle("hidden", state === "ok" || state === "pending");
}

function feedbackHTML(chosen, q) {
  const label = j => q.options[j].split(/[.、．]/)[0] || ("选项" + (j + 1));
  let html = chosen ? "" : '<p class="miss">⚠️ 未作答</p>';
  html += '<p><b>正确答案：</b>' + esc(label(q.answer)) + answerTail(q) + "</p>";
  if (chosen && Number(chosen.value) !== q.answer) {
    html += '<p class="wrong-pick"><b>你的选择：</b>' + esc(label(Number(chosen.value))) + "（错误）</p>";
  }
  return html;
}

function answerTail(q) {
  let html = "";
  if (q.explanation) html += '<br><b>解析：</b>' + esc(q.explanation);
  if (q.source) html += ' <span class="src">【出处：' + esc(q.source) + "】</span>";
  return html;
}

function disableGroup(sel) {
  document.querySelectorAll(sel).forEach(x => x.disabled = true);
}

/* =====================================================================
 * 成绩回显模式（?r=1 或 ?r + 同源 localStorage['lc_exam_review:<exam_id>']）
 * —— 再次打开已提交过的试卷时还原该生作答与批改结果并锁定整卷 ——
 * 仅在进入回显模式时运行，不影响正常作答/判分/上报/留档流程。
 * ===================================================================== */
function loadReviewRecord() {
  try {
    const qp = new URLSearchParams(window.location.search);
    if (!qp.has("r")) return null;                       // 无 r 参数 → 全新作答
    const raw = localStorage.getItem("lc_exam_review:" + EXAM.exam_id);
    if (!raw) return null;                               // 无留档 → 全新作答
    const rec = JSON.parse(raw);
    if (!rec || !Array.isArray(rec.answers)) return null;
    return rec;
  } catch (e) { return null; }  // URL/localStorage/JSON 异常一律静默降级为全新作答
}

function reviewOptLabel(opt, j) {
  return String(opt).split(/[.、．]/)[0] || ("选项" + (j + 1));
}

function reviewAnswered(ans) {
  return !(ans == null || ans.answer == null ||
    (Array.isArray(ans.answer) && ans.answer.length === 0) || String(ans.answer) === "");
}

function formatReviewTime(t) {
  if (!t) return "未知";
  const d = new Date(t);
  if (isNaN(d.getTime())) return String(t);
  const p = n => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " +
    p(d.getHours()) + ":" + p(d.getMinutes());
}

// 将留档作答还原到表单控件
function reviewRestore(q, idx, ans) {
  if (q.type === "single" || q.type === "multiple") {
    const picks = q.type === "single"
      ? (ans && ans.answer != null ? [Number(ans.answer)] : [])
      : (ans && Array.isArray(ans.answer) ? ans.answer.map(Number) : []);
    document.querySelectorAll('input[name="q' + idx + '"]').forEach(inp => {
      inp.checked = picks.includes(Number(inp.value));
    });
  } else if (q.type === "fill") {
    const inp = document.getElementById("fill-" + idx);
    if (ans && ans.answer != null) inp.value = String(ans.answer);
  } else if (q.type === "short") {
    const ta = document.getElementById("short-" + idx);
    if (ans && ans.answer != null) ta.value = String(ans.answer);
    const sgv = document.getElementById("sgv-" + idx);
    if (ans && ans.self_score != null) sgv.value = Math.min(Math.max(Number(ans.self_score) || 0, 0), q.score);
    document.getElementById("sg-" + idx).classList.remove("hidden");
  }
}

// 每题回显批改标注（客观题：绿=对/红=错并展示正误与作答；未答=未作答；简答：作答+自评）
function reviewMark(q, idx, ans) {
  const qDiv = document.querySelector('[data-qid="' + idx + '"]');
  const verdict = document.getElementById("verdict-" + idx);
  const fb = document.getElementById("fb-" + idx);
  fb.innerHTML = ""; fb.classList.remove("hidden");
  const full = q.score;
  const answered = reviewAnswered(ans);
  const correct = !!(ans && ans.correct);

  if (q.type === "short") {
    const kw = Array.isArray(q.keywords) && q.keywords.length;
    const modeLabel = kw ? "自动" : "自评";
    const selfVal = ans && typeof ans.self_score === "number"
      ? Math.min(Math.max(ans.self_score, 0), full)
      : (ans && typeof ans.score === "number" ? Math.min(Math.max(ans.score, 0), full) : 0);
    let kwHtml = "";
    if (kw) {
      const nv = normText(String(ans && ans.answer != null ? ans.answer : ""));
      kwHtml = '<p style="color:#1d6f42;font-weight:700;margin:4px 0 2px;">⚙️ 关键词自动批改：' +
        selfVal + " / " + full + " 分</p><ul style='margin:0;padding-left:20px;font-size:13.5px;'>" +
        q.keywords.map(k => {
          const ws = (Array.isArray(k.words) && k.words.length) ? k.words
            : (k.word != null ? [k.word] : []);
          const s = Math.max(Number(k.score) || 1, 0);
          const hit = !!nv && ws.some(w => { const nw = normText(w); return !!nw && nv.indexOf(nw) >= 0; });
          const lab = ws.length ? ws.join("／") : (k.word || "");
          return "<li style='color:" + (hit ? "#16a34a" : "#c62828") + "'>" + esc(lab) +
            "：" + (hit ? "✓ 命中 +" + s : "✗ 未命中 0") + " 分</li>";
        }).join("") + "</ul>";
    }
    fb.innerHTML = "<p><b>你的作答：</b>" + (answered ? esc(String(ans.answer)).replace(/\n/g, "<br>") : "（未作答）") + "</p>" +
      kwHtml +
      "<div class='ref-answer'><p><b>📝 参考答案：</b></p>" +
      "<p class='ref-text'>" + esc(q.reference).replace(/\n/g, "<br>") + "</p>" +
      (q.key_points ? "<p><b>🎯 评分要点：</b></p><ul>" +
        q.key_points.map(p => "<li>" + esc(p) + "</li>").join("") + "</ul>" : "") +
      answerTail(q) +
      "<p><b>" + modeLabel + "得分：</b>" + selfVal + " / " + full + " 分</p></div>";
    qDiv.classList.add(selfVal >= full ? "q-full" : (selfVal > 0 ? "q-partial" : "q-zero"));
    verdict.textContent = modeLabel + " " + selfVal + "/" + full + " 分";
    verdict.className = "verdict " + (selfVal >= full ? "v-right" : (selfVal > 0 ? "v-partial" : "v-wrong"));
    return;
  }

  // —— 客观题 single / multiple ——
  if (q.type === "single" || q.type === "multiple") {
    const chosen = answered ? (q.type === "single" ? [Number(ans.answer)] : ans.answer.map(Number)) : [];
    const std = q.type === "single" ? [Number(q.answer)] : q.answers.map(Number);
    q.options.forEach((_, j) => {
      const el = document.querySelector(".opt-" + idx + "-" + j);
      el.classList.remove("opt-right", "opt-wrong");
      if (std.indexOf(j) >= 0) el.classList.add("opt-right");
      else if (chosen.indexOf(j) >= 0) el.classList.add("opt-wrong");
    });
    const stdLabel = std.map(j => reviewOptLabel(q.options[j], j)).join("、");
    let h = answered ? "" : '<p class="miss">⚠️ 未作答</p>';
    h += "<p><b>正确答案：</b>" + esc(stdLabel) + answerTail(q) + "</p>";
    if (answered) {
      const myLabel = chosen.map(j => reviewOptLabel(q.options[j], j)).join("、");
      h += '<p class="' + (correct ? "ok-pick" : "wrong-pick") + '"><b>你的选择：</b>' +
        esc(myLabel) + (correct ? "（正确）" : "（错误）") + "</p>";
    }
    fb.innerHTML = h;
  } else {
    // 填空题：还原文本 + 对错底色 + 正确/本人作答展示
    const inp = document.getElementById("fill-" + idx);
    inp.classList.add(correct ? "fill-right" : "fill-wrong");
    fb.innerHTML = "<p><b>你的答案：</b>" + esc(answered ? String(ans.answer) : "（未作答）") +
      "<br><b>正确答案：</b>" + esc(q.answers.join(" ／ ")) + answerTail(q) + "</p>";
  }
  qDiv.classList.add(correct ? "q-full" : "q-zero");
  verdict.textContent = !answered ? "✗ 未作答" : (correct ? "✓ " + full + " 分" : "✗ 0 分");
  verdict.className = "verdict " + (correct ? "v-right" : "v-wrong");
}

// 顶部横幅 + 成绩卡（复用 #score-card 容器与既有 .s-item/.score-bar 样式）
function renderReviewSummary(rec, name) {
  let objMax = 0, subMax = 0;
  EXAM.questions.forEach(q => { if (q.type === "short") subMax += q.score; else objMax += q.score; });
  const num = v => (typeof v === "number" && isFinite(v) ? v : null);
  const dash = v => (v == null ? "—" : v);
  const objScore = num(rec.objective_score), selfScore = num(rec.subjective_self);
  const finalScore = num(rec.final_score) != null ? num(rec.final_score)
    : (objScore != null && selfScore != null ? objScore + selfScore : null);
  const om = num(rec.objective_max) != null ? rec.objective_max : objMax;
  const answeredCount = rec.answers.filter(a => reviewAnswered(a)).length;
  const totalNum = num(finalScore);
  const rate = EXAM.total_score ? Math.round((totalNum != null ? totalNum : 0) / EXAM.total_score * 100) : 0;

  const head = document.querySelector(".paper-head");
  const banner = document.createElement("div");
  banner.id = "review-banner";
  banner.className = "review-banner";
  banner.innerHTML = "🔒 <b>成绩回显 · 已锁定</b>　学生：" + esc(name) +
    "　总分：" + dash(finalScore) + " / " + EXAM.total_score + " 分　提交时间：" + esc(formatReviewTime(rec.submitted_at));
  head.insertBefore(banner, document.querySelector(".name-row"));

  const subjLabel = EXAM.questions.some(q => q.type === "short" && Array.isArray(q.keywords) && q.keywords.length)
    ? "主观题（关键词自动批改/自评）" : "主观题（自评）";
  const card = document.getElementById("score-card");
  card.classList.remove("hidden");
  card.innerHTML =
    '<h2>📊 成绩单<span class="review-tag">成绩回显 · 已锁定</span></h2>' +
    '<div class="score-grid">' +
    '<div class="s-item"><div class="s-label">学生姓名</div><div class="s-value">' + esc(name) + "</div></div>" +
    '<div class="s-item"><div class="s-label">客观题（自动批改）</div><div class="s-value">' + dash(objScore) + " / " + om + ' 分</div></div>' +
    '<div class="s-item"><div class="s-label">' + subjLabel + "</div><div class='s-value'>" + dash(selfScore) + " / " + subMax + ' 分</div></div>' +
    '<div class="s-item"><div class="s-label">答题情况</div><div class="s-value">' + answeredCount + " / " + EXAM.questions.length + ' 题已答</div></div>' +
    '<div class="s-item"><div class="s-label">提交时间</div><div class="s-value" style="font-size:15px">' + esc(formatReviewTime(rec.submitted_at)) + "</div></div>" +
    '<div class="s-item s-total"><div class="s-label">总分</div><div class="s-value">' + dash(finalScore) + " / " + EXAM.total_score + ' 分</div></div>' +
    "</div>" +
    '<div class="score-bar"><div class="score-bar-fill" style="width:' + rate + '%"></div></div>' +
    '<p class="score-note">🔒 成绩回显模式：本卷已锁定，仅可查看，不可修改或重新提交</p>';
  if (card.scrollIntoView) card.scrollIntoView({ behavior: "smooth" });
}

function enterReviewMode(rec) {
  const byQid = {};
  rec.answers.forEach(a => { byQid[a.qid] = a; });
  EXAM.questions.forEach((q, i) => {
    const idx = i + 1;
    reviewRestore(q, idx, byQid[idx]);
    reviewMark(q, idx, byQid[idx]);
  });
  // 锁定整卷：禁用全部作答控件与姓名输入
  document.querySelectorAll("input, textarea, select").forEach(el => { el.disabled = true; });
  // 禁用提交（双保险：按钮禁用 + gradeExam 顶部守卫）
  const btn = document.getElementById("submit-btn");
  btn.disabled = true;
  btn.textContent = "成绩回显 · 已锁定";
  btn.onclick = null;
  // 卷首姓名回填并标注回显横幅、渲染成绩卡
  const nameBox = document.getElementById("student-name");
  const shownName = (rec.student_name && String(rec.student_name).trim()) || nameBox.value.trim() || "学生";
  nameBox.value = shownName;
  nameBox.placeholder = "成绩回显 · 已锁定";
  renderReviewSummary(rec, shownName);
}

// —— 页面初始化：命中回显条件则立即进入回显模式 ——
(function initReviewMode() {
  const rec = loadReviewRecord();
  if (!rec) return;               // 未命中 → 保持全新作答流程
  window.__REVIEW_MODE__ = true;
  enterReviewMode(rec);
})();
"""

def build_exam_html(data, submit_url=DEFAULT_SUBMIT_URL):
    """构建交互式试卷 HTML"""
    # 成绩上报所需字段：exam_id（未提供则按卷名生成稳定 ID）、submit_url（服务器接收接口）
    if not data.get("exam_id"):
        digest = hashlib.md5(data["title"].encode("utf-8")).hexdigest()[:10]
        data["exam_id"] = f"exam-{digest}"
    data["submit_url"] = submit_url
    questions_html = "\n".join(
        render_question_html(q, i + 1) for i, q in enumerate(data["questions"]))
    n_obj = sum(1 for q in data["questions"] if q["type"] in ("single", "multiple", "fill"))
    n_sub = len(data["questions"]) - n_obj
    exam_data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    info_bits = [f"共 {len(data['questions'])} 题"]
    if data.get("duration"):
        info_bits.append(f"时长 {data['duration']}")
    info_bits.append(f"满分 {data['total_score']} 分")
    if n_obj:
        info_bits.append(f"客观题 {n_obj} 题（自动批改）")
    if n_sub:
        info_bits.append(f"主观题 {n_sub} 题（对照参考答案自评）")

    js = JS_GRADER.replace("__EXAM_DATA__", exam_data_json)

    return f"""<!DOCTYPE html>
<!-- exam_id: {data['exam_id']} | submit_url: {data['submit_url']} | 成绩上报字段说明见试卷内 JS（提交时 POST JSON） -->
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="exam-id" content="{data['exam_id']}">
<meta name="exam-submit-url" content="{data['submit_url']}">
<meta name="exam-total-score" content="{data['total_score']}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(data['title'])}</title>
<style>
:root {{
  --c-primary: #8b1e1e; --c-primary-light: #fdf2f2;
  --c-ink: #2d2a26; --c-muted: #7a736b;
  --c-right: #2e7d32; --c-right-bg: #e8f5e9;
  --c-wrong: #c62828; --c-wrong-bg: #ffebee;
  --c-border: #e2ddd6; --c-card: #ffffff; --c-page: #f7f5f2;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; background: var(--c-page);
       color: var(--c-ink); line-height: 1.75; padding: 24px 12px 60px; }}
.paper {{ max-width: 860px; margin: 0 auto; }}
.paper-head {{ background: var(--c-card); border: 1px solid var(--c-border); border-radius: 12px;
               padding: 28px 32px; margin-bottom: 20px; border-top: 4px solid var(--c-primary); }}
.paper-head h1 {{ font-size: 22px; color: var(--c-primary); margin-bottom: 8px; }}
.paper-meta {{ color: var(--c-muted); font-size: 14px; }}
.name-row {{ margin-top: 16px; display: flex; align-items: center; gap: 10px; }}
.name-row label {{ font-weight: bold; white-space: nowrap; }}
#student-name {{ flex: 1; max-width: 280px; padding: 9px 12px; font-size: 15px;
                 border: 1.5px solid var(--c-border); border-radius: 8px; }}
#student-name:focus {{ outline: none; border-color: var(--c-primary); }}
.question {{ background: var(--c-card); border: 1px solid var(--c-border); border-radius: 12px;
             padding: 20px 24px; margin-bottom: 16px; }}
.q-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }}
.q-no {{ font-weight: bold; color: var(--c-primary); }}
.q-type {{ font-size: 12px; padding: 2px 10px; border-radius: 10px; background: #eef2f5; color: #456; }}
.t-short {{ background: #f3ecf7; color: #6a4c93; }}
.q-score {{ color: var(--c-muted); font-size: 13px; }}
.verdict {{ margin-left: auto; font-weight: bold; }}
.v-right {{ color: var(--c-right); }} .v-partial {{ color: #e65100; }} .v-wrong {{ color: var(--c-wrong); }}
.q-stem {{ font-size: 15px; margin-bottom: 12px; }}
.opt {{ display: block; padding: 9px 12px; margin: 6px 0; border-radius: 8px; cursor: pointer;
        border: 1px solid transparent; }}
.opt:hover {{ background: var(--c-page); }}
.opt-right {{ background: var(--c-right-bg) !important; border-color: var(--c-right); }}
.opt-wrong {{ background: var(--c-wrong-bg) !important; border-color: var(--c-wrong); }}
.fill-input, .short-input {{ width: 100%; padding: 10px 12px; font-size: 15px;
                             border: 1.5px solid var(--c-border); border-radius: 8px;
                             font-family: inherit; }}
.fill-input:focus, .short-input:focus {{ outline: none; border-color: var(--c-primary); }}
.fill-right {{ background: var(--c-right-bg); border-color: var(--c-right) !important; }}
.fill-wrong {{ background: var(--c-wrong-bg); border-color: var(--c-wrong) !important; }}
.self-grade {{ margin-top: 10px; font-size: 14px; color: #6a4c93; background: #f3ecf7;
               border-radius: 8px; padding: 8px 12px; }}
.sg-input {{ width: 64px; padding: 4px 8px; border: 1.5px solid #d5c6e3; border-radius: 6px; }}
.feedback {{ margin-top: 12px; font-size: 14px; background: var(--c-page);
             border-radius: 8px; padding: 12px 16px; }}
.feedback p {{ margin: 4px 0; }}
.miss, .wrong-pick {{ color: var(--c-wrong); }}
.ref-answer {{ background: #fffde7; border-left: 3px solid #f9a825; border-radius: 0 8px 8px 0;
               padding: 12px 16px; }}
.ref-text {{ white-space: pre-wrap; }}
.src {{ color: var(--c-muted); font-size: 12.5px; }}
.q-full {{ border-color: var(--c-right); }}
.q-zero {{ border-color: #f3c3c3; }}
.hidden {{ display: none; }}
.submit-bar {{ text-align: center; margin: 28px 0; }}
#submit-btn {{ background: var(--c-primary); color: #fff; border: none; font-size: 16px;
               padding: 13px 52px; border-radius: 999px; cursor: pointer; font-weight: bold;
               box-shadow: 0 4px 14px rgba(139,30,30,.3); }}
#submit-btn:hover {{ filter: brightness(1.1); }}
#submit-btn:disabled {{ background: #9e9e9e; box-shadow: none; cursor: default; }}
#score-card {{ background: var(--c-card); border: 2px solid var(--c-primary); border-radius: 14px;
               padding: 26px 32px; margin-top: 20px; }}
#score-card h2 {{ color: var(--c-primary); margin-bottom: 16px; }}
.score-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
               gap: 12px; }}
.s-item {{ background: var(--c-page); border-radius: 10px; padding: 12px 16px; }}
.s-label {{ font-size: 12.5px; color: var(--c-muted); }}
.s-value {{ font-size: 19px; font-weight: bold; margin-top: 3px; }}
.s-total {{ background: var(--c-primary); color: #fff; }}
.s-total .s-label {{ color: rgba(255,255,255,.8); }}
.score-bar {{ height: 10px; background: #eee; border-radius: 5px; margin-top: 16px; overflow: hidden; }}
.score-bar-fill {{ height: 100%; background: linear-gradient(90deg, #d84343, #8b1e1e);
                   border-radius: 5px; transition: width .8s ease; }}
.score-note {{ font-size: 12.5px; color: var(--c-muted); margin-top: 10px; }}
.retry-btn {{ margin-top: 8px; background: transparent; color: var(--c-primary); font-size: 13px;
              border: 1px solid var(--c-primary); border-radius: 999px; padding: 5px 16px; cursor: pointer; }}
.retry-btn:hover {{ background: var(--c-primary); color: #fff; }}
/* —— 成绩回显模式（?r=1 + localStorage['lc_exam_review:<exam_id>']）样式 —— */
.review-banner {{ background: #fff8e1; border: 1px solid #f0c36d; color: #6d4c00;
                  border-radius: 8px; padding: 10px 14px; margin-top: 14px; font-size: 14px; }}
.review-tag {{ display: inline-block; background: var(--c-primary); color: #fff; font-size: 12px;
               padding: 2px 10px; border-radius: 10px; vertical-align: middle; margin-left: 6px; }}
.ok-pick {{ color: var(--c-right); }}
.paper-foot {{ text-align: center; color: var(--c-muted); font-size: 12.5px; margin-top: 24px; }}
</style>
</head>
<body>
<div class="paper">
  <div class="paper-head">
    <h1>{esc(data['title'])}</h1>
    <div class="paper-meta">{' · '.join(esc(b) for b in info_bits)}</div>
    <div class="name-row">
      <label for="student-name">👤 学生姓名：</label>
      <input type="text" id="student-name" placeholder="请输入姓名后开始作答">
    </div>
  </div>

{questions_html}

  <div class="submit-bar">
    <button id="submit-btn" onclick="gradeExam()">提交试卷 · 查看成绩</button>
  </div>

  <div id="score-card" class="hidden"></div>

  <div class="paper-foot">中医综合题库 · 生成于 {date.today().strftime('%Y-%m-%d')} · 本卷仅供学习自测，不构成医疗建议</div>
</div>
<script>
{js}
</script>
</body>
</html>"""


def build_answer_html(data):
    """构建题目+答案卷 HTML（打印友好，供转 PDF）"""
    parts = []
    total = 0
    for i, q in enumerate(data["questions"], 1):
        total += q["score"]
        meta = TYPE_META[q["type"]]
        parts.append(f'<div class="q"><div class="qh"><b>第 {i} 题</b>'
                     f'<span class="tag">{meta["label"]}（{q["score"]} 分）</span></div>')
        parts.append(f'<div class="stem">{esc(q["stem"])}</div>')
        if q["type"] in ("single", "multiple"):
            parts.append('<ol class="opts">')
            for opt in q["options"]:
                parts.append(f'<li>{esc(opt)}</li>')
            parts.append("</ol>")
        if q["type"] == "single":
            ans = q["options"][q["answer"]]
            parts.append(f'<div class="ans"><b>答案：</b>{esc(ans)}</div>')
        elif q["type"] == "multiple":
            ans = "、".join(q["options"][j] for j in sorted(q["answers"]))
            parts.append(f'<div class="ans"><b>答案：</b>{esc(ans)}</div>')
        elif q["type"] == "fill":
            parts.append(f'<div class="ans"><b>答案：</b>{esc(" ／ ".join(q["answers"]))}</div>')
        elif q["type"] == "short":
            parts.append(f'<div class="ans"><b>参考答案：</b><br>{esc(q["reference"]).replace(chr(10), "<br>")}</div>')
            if q.get("key_points"):
                parts.append('<div class="ans"><b>评分要点：</b><ul>' +
                             "".join(f"<li>{esc(p)}</li>" for p in q["key_points"]) + "</ul></div>")
            if q.get("keywords"):
                kws = q["keywords"]
                parts.append('<div class="ans"><b>关键词自动批改（命中即得分）：</b><ul>' + "".join(
                    f"<li>{esc('/'.join(k['words'] if isinstance(k.get('words'), list) else ([k['word']] if k.get('word') else [])))}"
                    f" —— {k.get('score', 1)} 分</li>" for k in kws) + "</ul></div>")
        if q.get("explanation"):
            parts.append(f'<div class="expl"><b>解析：</b>{esc(q["explanation"])}</div>')
        if q.get("source"):
            parts.append(f'<div class="src">出处：{esc(q["source"])}</div>')
        parts.append("</div>")

    return f"""<!DOCTYPE html>
<!-- exam_id: {data['exam_id']} -->
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="exam-id" content="{data['exam_id']}">
<title>{esc(data['title'])} · 答案卷</title>
<style>
:root {{ --c-primary: #8b1e1e; --c-ink: #2d2a26; --c-muted: #7a736b;
         --c-border: #e2ddd6; --c-right-bg: #e8f5e9; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; color: var(--c-ink);
       line-height: 1.8; padding: 32px 40px; max-width: 820px; margin: 0 auto; font-size: 14.5px; }}
h1 {{ text-align: center; color: var(--c-primary); font-size: 20px; }}
.sub {{ text-align: center; color: var(--c-muted); font-size: 13px; margin: 6px 0 20px;
        border-bottom: 2px solid var(--c-primary); padding-bottom: 14px; }}
.q {{ border: 1px solid var(--c-border); border-radius: 8px; padding: 14px 18px;
      margin-bottom: 12px; page-break-inside: avoid; }}
.qh {{ display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }}
.tag {{ font-size: 12px; color: #456; background: #eef2f5; border-radius: 8px; padding: 1px 8px; }}
.stem {{ margin-bottom: 8px; }}
.opts {{ margin: 0 0 8px 22px; }}
.ans {{ background: var(--c-right-bg); border-radius: 6px; padding: 8px 12px; margin: 6px 0;
        font-size: 13.5px; }}
.expl {{ font-size: 13.5px; color: #555; margin-top: 4px; }}
.src {{ font-size: 12px; color: var(--c-muted); margin-top: 2px; }}
@media print {{ body {{ padding: 0; font-size: 12.5px; }}
                 .q {{ border-color: #ccc; }} }}
</style>
</head>
<body>
<h1>{esc(data['title'])}（答案卷）</h1>
<div class="sub">共 {len(data['questions'])} 题 · 满分 {total} 分 · 生成于 {date.today().strftime('%Y-%m-%d')}</div>
{''.join(parts)}
<div class="sub" style="border:none; margin-top:24px;">本卷仅供学习自测，不构成医疗建议</div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="交互式 HTML 试卷生成器")
    parser.add_argument("--exam", required=True, help="题库 JSON 文件路径")
    parser.add_argument("--output-dir", default="试卷输出", help="输出目录")
    parser.add_argument("--name", default="", help="输出文件名（不含扩展名），默认取卷名")
    parser.add_argument("--submit-url", default=DEFAULT_SUBMIT_URL,
                        help=f"成绩上报接口 URL（默认 {DEFAULT_SUBMIT_URL}，相对路径则上报到 PWA 同源）")
    parser.add_argument("--no-shuffle", action="store_true",
                        help="不随机打乱选择题选项顺序（默认打乱，避免正确答案位置集中出现）")
    parser.add_argument("--seed", type=int, default=None,
                        help="选项打乱随机种子（便于复现某一次选项顺序；默认每次随机）")
    parser.add_argument("--with-answers", action="store_true",
                        help="额外出答案卷 HTML（默认不出；仅用户明确要求答案卷/打印版/PDF 时使用）")
    args = parser.parse_args()

    with open(args.exam, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 卷面洁净：统一清除题干开头的难度/场景标记（如【执医】【考研】），
    # 供试卷 HTML、答案卷 HTML 及内嵌 JS 共用，确保任何呈现位置都不出现
    for q in data.get("questions", []):
        if isinstance(q, dict) and q.get("stem"):
            q["stem"] = clean_stem(q["stem"])

    errors = validate_exam(data)
    if errors:
        print("❌ 题库校验失败：")
        for e in errors:
            print(f"  - {e}")
        return 1

    # 打乱选择题选项顺序（试卷与答案卷共用同一份 data，顺序天然一致）
    if not args.no_shuffle:
        n = shuffle_question_options(data["questions"], random.Random(args.seed))
        if n:
            print(f"🔀 已随机打乱 {n} 道选择题的选项顺序（固定某次顺序可加 --seed，保留原序可加 --no-shuffle）")

    os.makedirs(args.output_dir, exist_ok=True)
    base = args.name or data["title"]

    exam_path = os.path.join(args.output_dir, f"{base}.html")
    with open(exam_path, "w", encoding="utf-8") as f:
        f.write(build_exam_html(data, submit_url=args.submit_url))

    # 答案卷默认不出，仅在 --with-answers 时生成
    answer_path = None
    if args.with_answers:
        answer_path = os.path.join(args.output_dir, f"{base}_答案卷.html")
        with open(answer_path, "w", encoding="utf-8") as f:
            f.write(build_answer_html(data))

    n_type = {}
    for q in data["questions"]:
        n_type[q["type"]] = n_type.get(q["type"], 0) + 1
    type_summary = "、".join(
        f"{TYPE_META[t]['label']}{n}" for t, n in n_type.items())
    print(f"✅ 试卷生成完成：")
    print(f"   📄 交互式试卷: {exam_path}")
    if answer_path:
        print(f"   📄 答案卷(可转PDF): {answer_path}")
    else:
        print(f"   （答案卷未生成；如需请加 --with-answers）")
    print(f"   题量: {len(data['questions'])} 题（{type_summary}）")
    print(f"   总分: {data['total_score']} 分")
    print(f"   📡 成绩上报: POST {args.submit_url}（exam_id={data['exam_id']}）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
