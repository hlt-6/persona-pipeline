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
