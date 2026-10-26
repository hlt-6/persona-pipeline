# 本地运行时（runner）

仓库原本是纯提示词文档，没有可执行入口。runner 把 01 → 02 → 03 变成一条能跑的命令，并把
"引文必须是原文"这条纪律做成可执行的闸门。

零第三方依赖：只用 Python 标准库（`urllib` + `tomllib`），克隆下来就能跑，不需要 `pip install`。

## 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| Python | >= 3.11 | 需要标准库 `tomllib`；实测 3.13.14 |
| 第三方包 | 无 | 想跑 `fetch_corpus.py` 的繁转简才需要 `opencc-python-reimplemented` |
| 网络 | 可选 | 只有在线模式（调模型）和抓语料时需要；离线模式全本地 |
| API key | 可选 | 只有在线模式需要 |

## 快速开始

```bash
cd persona-pipeline

# 1. 生成本地配置
cp runner/config.example.toml runner/config.toml

# 2. 环境自检（不消耗额度）
python runner/cli.py doctor

# 3. 校验输入包（结构与引文）
python runner/cli.py verify --input sample/input-sun-wukong.json

# 4. 跑通流程（离线，不调模型，验证产物结构）
python runner/cli.py run --input sample/input-sun-wukong.json --offline
```

产物在 `runner/out/<输入包名>/`：`persona.md`、`card.json`、`persona.json`、
`verify-report.json`、`run-report.json`。

## 在线模式（调用模型）

任何 OpenAI 兼容端点都支持：DeepSeek 官方、DS 平台网关、one-api、vLLM 等。
改 `runner/config.toml`：

```toml
[llm]
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_env = "DS_API_KEY"
```

然后设置密钥并跑：

```bash
# Windows PowerShell（当前会话）
$env:DS_API_KEY = "sk-xxx"
# Git Bash / macOS / Linux
export DS_API_KEY=sk-xxx

python runner/cli.py doctor --probe            # 先验证端点与鉴权
python runner/cli.py run --input sample/input-sun-wukong.json
```

环境变量覆盖（不想改文件时）：
`PERSONA_BASE_URL`、`PERSONA_MODEL`、`PERSONA_API_KEY_ENV`、`PERSONA_TIMEOUT`。
密钥识别顺序：`api_key_env` → `DS_API_KEY` → `PERSONA_API_KEY` → `DEEPSEEK_API_KEY` → `OPENAI_API_KEY`。

## 引文核验（阻断级问题 1 的解法）

不带语料时，只能查结构（条数、必填字段），引文一律标记 `unverified`——**明确告诉你"没核验过"**，
而不是假装通过。

要真正核验，提供原著全文：

```bash
# 抓公版语料（默认《西游记》100 回，转简体）
python runner/fetch_corpus.py --chapters 100 --out data/xiyouji.txt

python runner/cli.py verify --input sample/input-sun-wukong.json --corpus data/xiyouji.txt
python runner/cli.py run   --input sample/input-sun-wukong.json --corpus data/xiyouji.txt
```

判定分三级：

| 判定 | 含义 | 后果 |
|---|---|---|
| `verified` | 归一化后精确命中语料 | 可写入卡片的 `mes_example` |
| `paraphrase` | 与某段原文高度重合但不等同 | 只作风格参考，不许标"原著原话" |
| `missing` | 语料中找不到 | 视为伪造引文，`run` 直接中止 |

退出码：`0` 全通过 / `1` 有 missing 或结构错误 / `3` 有 paraphrase / `4` 无法核验（无语料）。

## 命令一览

| 命令 | 作用 |
|---|---|
| `doctor [--probe]` | 检查 Python、项目文件、配置、密钥；`--probe` 额外探测端点（不消耗额度） |
| `verify --input X [--corpus Y] [--json Z]` | 校验结构与引文，输出报告 |
| `run --input X [--corpus Y] [--offline] [--skip-verify] [--out DIR]` | 跑完整流水线 |
| `fetch_corpus.py --chapters N --out F` | 从维基文库抓公版语料并转简体 |

## 角色蒸馏（从全书语料造一个人格）

上述命令是"按输入包出产物"。如果要直接从一本小说里蒸馏某个角色，走下面这条链，
**全程零模型成本也能跑到判读那一步**（判读本身由人或模型完成，工具只负责机械部分）：

| 步骤 | 命令 | 作用 |
|---|---|---|
| 1 量规模 | `probe_character.py 名字 --alias 别名…` | 角色出现在多少章、多少引号片段、机械可命中率（通常极低） |
| 2 看弧线 | `char_timeline.py 名字 --alias … --pick 40` | 存在曲线 + 分层抽样章单（跨全书均匀取，避免只读开头） |
| 3 查称呼 | `char_scan.py 名字 --alias … --bucket 100` | 各名字随章节的出现量（找改口节点）+ 共现人物 |
| 4 出包 | `rulepass.py --packet --batch "1,2,3"` | 引号原位插编号，模型只需回编号不抄台词 |
| 5 回填 | `apply_answers.py --chapter N --answers F --out-dir DIR` | 台词从章节反查，逐字核验 + 编号对账；`--out-dir` 避免覆盖基准卷 |
| 6 量风格 | `style_profile.py 名字 --dir DIR` | 句长分布、省略号/问号密度、语气词、口头禅 n-gram |
| 7 出语料 | `export_character_lines.py 名字 --dir DIR --out F` | 该角色全部台词，可带前一轮对话语境 |
| 8 渲染 | `render.write_outputs(persona, dir)` | persona.json → persona.md + SillyTavern card |
| 9 验收 | `accept.py --static / --verify / --sample / --audit` | 全书四条命令过验收，判据见 `ACCEPTANCE.md` |

实例：杨思思 v1（44 章 / 760 条台词）。该版因验收标准升级已整体归档、不再作为交付物——
它的"通过"只覆盖覆盖率与逐字核验，**未测判错人率**，不能当作已验证的成品。

### 全书验收（`accept.py`）

判据写在根的 `ACCEPTANCE.md`，工具只负责执行，不改判据：

| 模式 | 时机 | 检查什么 | 阻断阈值 |
|---|---|---|---|
| `--static` | 判读**之前** | 文本冻结哈希、章号集合、**包—原文去编号还原一致**、包内编号连续 | 任一不符即阻断 |
| `--verify` | 判读**之后** | 编号级闭合、笑声片段覆盖、单章未知率/低置信率/排除率 | 单章 >10% 告警 |
| `--sample` | 抽检前 | 生成 20 随机（固定种子）+ 10 特征章的复核清单，**不含原文** | — |
| `--audit` | 复核后 | 判错人率，口径分歧单列 | 错人率 >2% 不通过 |

`--static` 必须在判读前跑。771 章跑完再发现包是坏的，返工成本是前者的百倍。

判错人率的分母是**已明确判定**的条数，不含"未知"——否则"判不出"会被算成"没判错"，两个指标互相稀释。错人率管判错，未知率管不敢判，各自卡线。

## 与 ds 平台的关系

ds 平台上已有的模型接入与筛选逻辑不需要改动，runner 只是本地并行的验证通道：
用同一份 01/02/03 提示词、同一份输入包，在本地跑出可检查的产物，方便做引文核验与回归对比。
`run` 的在线模式只调用一次 `chat/completions`，不接管平台侧的工作流编排。

## 常见问题

**`未找到密钥`**
在线模式需要 key，离线模式不需要。先跑 `--offline` 确认流程本身没问题。

**`无法连接 ... (SSL: CERTIFICATE_VERIFY_FAILED)`**
企业代理常替换证书。临时放行：`PERSONA_INSECURE=1 python runner/cli.py doctor --probe`。
生产环境建议把代理根证书加进系统信任链，而不是长期关校验。

**`HTTP 401/403`**
key 无效、过期，或 base_url 与该 key 不属于同一平台（DeepSeek 的 key 不能打 OpenAI 端点）。

**模型返回不是 JSON**
`llm.parse_json_loose` 会剥掉 ```json 围栏并截取最外层 `{}`。仍失败时把 `temperature` 调到 0.2，
或换支持 `response_format: json_object` 的模型。

**Windows 控制台中文乱码**
`set PYTHONIOENCODING=utf-8`，或用 PowerShell 而非 cmd。

**改了 02/03 提示词后无法回溯**
每次运行都会落 `run-report.json`（含模型名、耗时、校验摘要）与 `persona.json`（含生成来源）。
建议给提示词文件名加版本号，例如 `02-novel-persona-analyzer.v2.md`。
