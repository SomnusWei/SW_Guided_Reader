# 倪海厦体系运行时摘要

> 供 yixue-zonghe skill 各功能调用倪海厦经方体系时使用。本文件是运行时索引，不是知识库本体。
> 体系目录：`倪海厦体系/`（在 skill 根目录下）。

---

## 一、目录结构说明

```
倪海厦体系/
├── SKILL.md                          # 134K，倪师完整认知上下文（角色内核）
├── README.md / README_EN.MD          # 体系说明（中英文）
├── CHANGELOG.md                      # 版本变更
├── expression_style.md               # 表达风格规范
├── distilled_cases.md                # 243 条超长医案清单
├── index.html / logo.jpg             # 前端入口
│
├── modules/                          # 14 个原始知识模块（倪师讲义全集）
│   ├── 01_shanghan_sun.md            # 264K 伤寒论·太阳篇（含桂枝/麻黄等太阳方）
│   ├── 02_shanghan_other.md          #  30K 伤寒论·其他五经篇（阳明/少阳/太阴/少阴/厥阴）
│   ├── 03_yian.md                    # 960K 医案集（按日期叙事）
│   ├── 04_jingui.md                  #1181K 金匮要略讲义
│   ├── 05_huangdi_neijing.md         # 212K 黄帝内经讲义
│   ├── 06_liangdong.md               #  36K 梁冬对话倪师
│   ├── 07_bimen_hantang.md           #  45K 闭门密训+汉唐方剂
│   ├── 08_huangdi_detail.md          # 849K 黄帝内经详解
│   ├── 09_zhenjiu_bencao.md          # 453K 针灸教程+神农本草经374种+天纪+汉唐医疗
│   ├── 10_fuyang_luntan.md           #   7K 扶阳论坛
│   ├── 11_zhongjing_xinfa.md         #   8K 仲景心法
│   ├── 12_stanford_jingfang.md       #   6K 斯坦福经方讲座
│   ├── 13_shanghan_quebing.md        # 559K 伤寒论缺病（疑难病补充）
│   └── 14_yijinjing_bidu.md          #   9K 易筋经必读（导引/养生）
│
├── cases/                            # 1257 例倪师真实医案
│   ├── 00_merged_table.md            # 687K **全量结构化表**（1257例，12列字段）
│   ├── 01_cancer.md                  #  85K 癌症分类医案
│   ├── 02_cardiovascular.md         #  12K 心血管医案
│   ├── 03_metabolic.md               #   6K 代谢病医案
│   ├── 04_autoimmune.md              #   1K 自身免疫医案
│   ├── 05_neurological.md           #   2K 神经科医案
│   └── 06_other.md                   #  31K 其他医案
│
└── references/
    ├── distilled/                    # 6 个蒸馏速查（结构化，优先调用）
    │   ├── 01-six-meridian-formulas.md   # 37K 六经辨证诊断公式（8公式+流程图+脉舌速查）
    │   ├── 02-acupuncture-quick-ref.md   # 24K 针灸速查（十二原穴/五输穴/子母补泻）
    │   ├── 03-clinical-experience.md    # 10K 治感冒六大经方+病机十九条
    │   ├── 04-acupuncture-highlights.md #146K 针灸全集（辅助02，深度详注）
    │   ├── 05-clinical-dose-quickref.md  # 13K 临床剂量速查（伤寒14方+金匮19方）
    │   ├── 06-clinical-dose-c99.md       # 18K C类99方剂量全量摸查
    │   ├── audit-notes.md                #  6K 教验报告（知识库vs skill对比）
    │   └── README.md                     #  2K distilled 目录导航
    ├── research/                     # 9 个调研文件（蒸馏过程留痕）
    │   ├── 01-writings.md ~ 08-clinical-cases.md
    │   └── combined_reference.md     # 调研汇总
    └── audit/                        # 审计文件目录
```

---

## 二、检索方式

### 总原则：先 distilled 速查 → 不够再翻 modules 原文 → cases 用于医案取材

### 2.1 Grep 关键词定位（主要检索方式）

```bash
# 工具：Grep（不是 shell grep）
# 1. 辨证/六经 → distilled/01
Grep "倪海厦体系/references/distilled/01-six-meridian-formulas.md"
  pattern: "{症状关键词，如 口苦|往来寒热|脉微细|但欲寐}"

# 2. 临床剂量 → distilled/05 + 06
Grep "倪海厦体系/references/distilled/05-clinical-dose-quickref.md"
  pattern: "{方名，如 桂枝汤|小柴胡汤|乌梅丸}"
Grep "倪海厦体系/references/distilled/06-clinical-dose-c99.md"
  pattern: "{方名}"  # C类96方兜底

# 3. 针灸速查 → distilled/02（短）→ 04（长）
Grep "倪海厦体系/references/distilled/02-acupuncture-quick-ref.md"
  pattern: "{穴名|经络|五输}"

# 4. 单味药药性 → modules/09
Grep "倪海厦体系/modules/09_zhenjiu_bencao.md"
  pattern: "{药名，如 桂枝|白芍|附子|细辛}"

# 5. 倪师原文讲义 → modules/01-08,13
Grep "倪海厦体系/modules/04_jingui.md"
  pattern: "{方名|病名|条文关键词}"

# 6. 真实医案 → cases/00_merged_table.md
Grep "倪海厦体系/cases/00_merged_table.md"
  pattern: "{诊断|方剂，如 肺癌|十枣汤|大柴胡汤}"
# 高频诊断：癌227/肝病182/乳癌89/肝癌84/肺癌74/失眠61/心脏病43/糖尿病39/血癌35/肝硬化22/脑瘤22
```

### 2.2 调用顺序（按文件大小，先短后长）

> ⚠️ **优先读短文档（distilled/01-03、05-06），按需读长文档（modules/03、04、08、13、cases/00）**

| 层级 | 文件 | 何时读 |
|------|------|--------|
| L1 速查（必读） | distilled/01-03, 05-06 | 任何涉及倪师体系时优先查 |
| L2 详注（按需） | distilled/04 | 针灸深度详注（02不够时） |
| L3 讲义原文（按需） | modules/01-02, 04, 05, 08, 13 | 速查无果/需引原文 |
| L4 医案（按需） | cases/00_merged_table → 01-06 → modules/03 | 病案分析/医案出题 |
| L5 调研（极少） | references/research/* | 需追溯蒸馏来源/审计 |

---

## 三、各功能调用指引

### 3.1 学习功能（中医学习/讲义）

| 子主题 | 主入口 | 辅助入口 |
|--------|--------|---------|
| 伤寒论·太阳病 | `modules/01_shanghan_sun.md` | `distilled/01`（速查） |
| 伤寒论·其他五经 | `modules/02_shanghan_other.md` | `distilled/01` |
| 金匮要略 | `modules/04_jingui.md` | `distilled/05`（方剂） |
| 黄帝内经 | `modules/05_huangdi_neijing.md` + `08_huangdi_detail.md` | — |
| 针灸/本草 | `modules/09_zhenjiu_bencao.md` | `distilled/02` + `04` |
| 仲景心法 | `modules/11_zhongjing_xinfa.md` | — |
| 疑难病 | `modules/13_shanghan_quebing.md` | — |

### 3.2 病案分析功能

按 `references/case-analysis.md` 六步法走，关键节点调用：

- **第 2 步辨证（六经定位）** → `distilled/01-six-meridian-formulas.md`（8 公式+流程图）
- **第 3 步病机推演（传变路径）** → `distilled/01` 末段「传变规律」
- **第 4 步治法（先辨阴阳再选方）** → `distilled/03-clinical-experience.md`（六大感冒经方对照）
- **第 5 步方药（剂量档位）** → `distilled/05` + `06`（临床剂量速查）
- **第 6 步三库对照** → 见 `case-analysis.md` 三库对照表

### 3.3 处方讲解功能

按 `references/prescription-guide.md` 五层讲解走，关键节点调用：

- **条文卡片** → `modules/01`（伤寒）+ `modules/04`（金匮）+ `scripts/search_textbooks.py` 核对原文
- **三列剂量** → `distilled/05-clinical-dose-quickref.md`（优先）+ `06-clinical-dose-c99.md`（兜底）
- **倪师药性认识** → `modules/09_zhenjiu_bencao.md`（Grep 单味药名）

### 3.4 针灸功能

按 SKILL.md 通用针灸规范走，关键节点调用：

- **十二经络流注/五输穴速查** → `distilled/02-acupuncture-quick-ref.md`（必读）
- **针灸深度详注/急救手法** → `distilled/04-acupuncture-highlights.md`（按需）
- **倪师腧穴/本草** → `modules/09_zhenjiu_bencao.md`

> 注：针灸功能同时调用石学敏体系（醒脑开窍量学），见 `shixuemin-integration.md`。倪师体系是基础针灸+经络辨证，石师体系是醒脑开窍+量学参数，两者互补。

### 3.5 模拟题功能

默认从教材出题，按 `references/quiz-guide.md` 标准流程走。**仅当用户明确要求**"倪师医案题/经方医案题"时启用倪师医案题命题规范（见 quiz-guide.md 末段），从 `cases/00_merged_table.md` 取材。

---

## 四、倪师观点标注规则

### 4.1 必须标注倪师观点的情形

凡输出内容涉及以下情况，必须明确标注「倪师观点」与来源文件：

1. **六经辨证结论**：辨证结论中的"六经归属"字段——标注「（倪师体系，见 distilled/01）」
2. **临床剂量**：三列剂量中的"倪师临床量"列——标〔口述〕或〔换算〕，注明 `distilled/05`/`06` 出处
3. **药性认识**：与教材并列的倪师口述药性——注明「（倪师，modules/09）」
4. **方剂禁忌/铁律**：如"少阳三禁"、"少阴不可发汗"、"有汗用麻黄亡阳"——注明「（倪师铁律）」
5. **医案改编题**：题干末尾标「改编自倪海厦真实医案（第 N 例）」
6. **医案分析**：引用倪师医案时注明「倪师 2005-2009 医案集，序号 N」

### 4.2 标注格式

```
**倪师观点**：{观点一句话}（{来源文件:行号/段落}）
```

范例：
> **倪师观点**：少阳病只能和解，不可汗、不可下、不可吐——汗之则谵语，下之则悸而惊，吐之则烦而悸（distilled/01-six-meridian-formulas.md，少阳病三禁段）。

### 4.3 与教材分歧时的处理

凡倪师观点与现行教材不一致时，**必须并列呈现**，不得只取一方：

| 维度 | 现代教材 | 倪师观点 | 分歧说明 |
|------|---------|---------|---------|
| {维度} | {教材立场} | {倪师立场} | {分歧点+各自依据} |

典型分歧：
- 剂量：教材一两≈3g vs 倪师一两=一钱≈3.75g（介于教材与原方 15.6g 之间）
- 附子用法：教材多用炮附子且量小 vs 倪师生附子与炮附子分用，生附子配干姜、炮附子配生姜
- 治肝：教材多疏肝理气 vs 倪师"治肝第一方"乌梅丸，苦温化湿
- 糖尿病：教材多分上中下消论治 vs 倪师以下消为肾阳虚，主桂附八味丸

### 4.4 不得越界的情形

- 倪师对西医的强烈批评性言论（如"西药毒害"）——**仅在分析倪师医案时按原意引用并加注"倪师原话，本知识库保持中立"**，不得扩散到一般性论述
- 倪师未使用过的方剂（如侯氏黑散、鳖甲煎丸——倪师明言未用过）——不得借倪师之名讲解，转回教材体系
- 倪师处方保密的医案——不得作为出题素材或方义分析对象
