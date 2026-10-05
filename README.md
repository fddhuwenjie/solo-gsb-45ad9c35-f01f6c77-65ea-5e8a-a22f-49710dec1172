# 电缆隧道接头巡检复核台

基于 **Flask + SQLite + 原生 JavaScript** 的电缆隧道接头巡检数据复核系统。
巡检员录入线路里程、接头、巡检批次、负荷区间、测温距离、温差读数、设备校准版本，
以及复测读数与人工复核意见；系统按**同一接头 + 同一批次**对齐观测并生成风险结论与处置清单。

## 一条命令启动

```bash
docker compose up --build -d
```

启动后访问：**http://localhost:8000**

> 首次启动自动建表并播种演示数据（含可比较 / 证据不足 / 相互矛盾 / 更正留痕 / 高风险已复核 等示例）。
> SQLite 数据保存在命名卷 `inspection-data`（容器内 `/data/inspection.db`），停止重建容器不丢数据。

停止：`docker compose down`（加 `-v` 会同时删除数据卷）。

### 不使用 Docker 的本地运行

```bash
pip install -r requirements.txt
python wsgi.py        # 开发模式，默认库文件 /data/inspection.db
# 或指定库路径：
DATABASE_PATH=./inspection.db python wsgi.py
```

## 核心业务规则

### 1. 同接头同批次对齐，三档证据状态

- **可比较**：测温距离（差 ≤ 1m）、负荷区间有交集、设备校准版本一致，且多条读数温差极差 ≤ 3K，可相互印证。
- **证据不足**：只有一条有效读数；或距离差 > 1m、校准版本不同、负荷区间无交集导致观测条件无法对齐（原因逐条列明）。
- **相互矛盾**：条件可对齐，但温差极差 > 3K，读数不能相互印证。

### 2. 风险结论（带原因 + 处置清单）

| 风险 | 判定 |
|---|---|
| 高 | 读数相互矛盾；或最大温差 ≥ 8K（证据不足时降为中风险） |
| 中 | 最大温差 ≥ 4K |
| 低 | 其余 |

每条结论包含人类可读原因（引用了哪些观测、因何不可比/矛盾）和处置清单
（复测、人工复核、设备校准核查、限负荷盯守、趋势跟踪、常规巡检等）。

### 3. 原始记录不可覆盖

`observations` / `reviews` / `conclusions` / `reports` 全部只追加：

- **更正**：写入新记录并以 `corrects_id` 指向原记录；原记录仅置 `superseded` 标记，内容永不修改。
- **补录 / 新增复核**：只令该**受影响接头**的当前结论 `invalidated=1` 并写明失效原因，再追加一条新结论；其他接头不受影响，历史结论完整可追溯。

### 4. 封存报告固定批次快照

- 封存即生成整批次 JSON 快照（全部原始观测、复核、结论版本历史），此后任何更正/补录都**不会改变**已封存内容；同一批次可封存出 v1、v2… 新版本。
- **封存门槛**：批次内每个高风险接头都必须具备**当前复测读数**或**明确人工复核意见**，否则整份报告拒绝封存，并在“批次复核”页列出全部阻断原因。

### 5. 证据链页面

- **批次复核**：按批次查看全部接头的状态/风险/原因/处置与封存门槛。
- **线路证据链**：按线路查看每个接头在各批次的当前结论及结论失效历史（含失效原因），可下钻到接头的完整证据：原始读数、更正链、复测、人工复核和全部结论版本。

## 页面

| 路由 | 功能 |
|---|---|
| `#/entry` | 观测录入（含“更正已有读数”、复测标记）与人工复核意见 |
| `#/batch` / `#/batch/<id>` | 批次总览、一键重算、封存门槛与封存 |
| `#/line` / `#/line/<id>` | 按线路查看证据链与失效原因 |
| `#/reports` / `#/reports/<id>` | 封存报告列表与固定快照 |

## API 摘要

```
GET  /api/meta
POST /api/observations                 # 录入/更正/补录观测（corrects_id 表示更正）
POST /api/reviews                      # 人工复核意见
GET  /api/batches/:id                  # 批次总览 + 封存门槛
POST /api/batches/:id/recompute        # 重算本批结论
POST /api/batches/:id/seal             # 封存（不满足门槛返回 400 及原因）
GET  /api/reports · /api/reports/:id   # 封存报告及快照
GET  /api/lines/:id/evidence           # 线路证据链
GET  /api/joints/:jid/batches/:bid/evidence  # 接头单批次完整证据
```

## 技术结构

```
app/
  __init__.py      # Flask 应用工厂
  db.py            # SQLite 连接、幂等建表、审计日志
  evaluation.py    # 对齐/分级/风险/处置规则（阈值常量集中可调）
  services.py      # 只追加写入、结论失效与重算、封存校验、快照
  routes.py        # JSON API
  seed.py          # 演示数据
schema.sql         # 表结构
static/js/app.js   # 原生 JS 单页（hash 路由，无框架无构建）
```
