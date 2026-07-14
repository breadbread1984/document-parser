# Document Parser 服务

通过 HTTP 上传 PDF，用 **MinerU** 做 OCR/版面解析生成 Markdown；遇到化学结构式图片时，用 **MolScribe** 转成 SMILES，最后可下载处理后的 Markdown。

> 本服务**只做** PDF → Markdown（含结构式 SMILES），不做专利检索、序列抽取、活性/渗透性入库。那些能力在 Wipo-agent。

---

## 使用方法（请按顺序做）

### 0. 先看清：哪个才是正确结果？

| 对 / 错 | 文件或内容 | 说明 |
|---------|------------|------|
| 正确 | 接口下载的 `*_final.md`，或任务目录里的 `result.md` | 结构式已尽量换成 SMILES |
| 错误 | MinerU 目录下的 `input.md` / `*.md` | 只是 OCR 中间稿，结构式仍是图片 |
| 错误 | 打开后只有一行 `{"detail":"Not Found"}` | **不是结果**，是下载 URL 写错或任务不存在 |
| 错误 | 全文大量 `![](images/xxxx.jpg)`、几乎没有反引号 SMILES | 多半打开了中间稿，或任务未完成就拷贝了文件 |

**如何一眼判断打开对了：**

- 搜 `(a)` 或 `cyclo[` 附近：应看到类似  
  `` `CC(C)C[C@@H]1NC(=O)...` ``  
  （反引号包着的 SMILES）
- 若仍是 `![](images/d37fce3e....jpg)`，说明看错文件了

**唯一推荐的下载地址（务必用这个）：**

```text
GET http://<主机>:8000/v1/jobs/<job_id>/markdown
```

兼容别名（效果相同）：

```text
GET http://<主机>:8000/v1/jobs/<job_id>/result.md
```

不要发明其它路径。不要把服务器磁盘上的路径直接拼进 URL。

---

### 1. 启动服务

**方式 A：Docker Compose（推荐部署）**

```bash
# CPU
docker compose up --build -d

# GPU（需已安装 NVIDIA Container Toolkit）
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d
```

**方式 B：本机 Windows（联调）**

```powershell
cd D:\CODE\Workspace\python\BIO\document-parser

# 若还没有 API 虚拟环境：
# py -3.11 -m venv .venvs\api
# .\.venvs\api\Scripts\pip.exe install -r requirements-api.txt

$env:DATA_DIR = "$PWD\data"
$env:MINERU_VENV = "$PWD\.venv"
$env:MOLSCRIBE_VENV = "$PWD\.venv"
$env:MOLSCRIBE_DEVICE = "cuda"   # 无 GPU 改为 cpu

.\.venvs\api\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
```

**方式 C：本机 Linux / macOS**

```bash
bash scripts/setup_venvs.sh
export DATA_DIR="$PWD/data"
export MINERU_VENV="$PWD/.venvs/mineru"
export MOLSCRIBE_VENV="$PWD/.venvs/molscribe"
.venvs/api/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动成功后先自检：

```bash
curl http://127.0.0.1:8000/health
# 期望：{"status":"ok", ...}
```

| 地址 | 说明 |
|------|------|
| http://127.0.0.1:8000/docs | Swagger 页面，可点按钮上传/下载（不容易写错 URL） |
| http://127.0.0.1:8000/health | 健康检查 |

---

### 2. 处理一份 PDF（必须三步）

任务是**异步**的：上传后立刻返回 `job_id`，**等 status=done 再下载**。

#### 步骤 ① 上传，记下 job_id

```bash
curl -F "file=@你的专利.pdf" http://127.0.0.1:8000/v1/jobs
```

返回示例（请复制其中的 `job_id`）：

```json
{
  "job_id": "098b1a165300472790eb80a1991f469c",
  "status": "queued",
  "message": "..."
}
```

#### 步骤 ② 查询状态，直到 done

```bash
curl http://127.0.0.1:8000/v1/jobs/098b1a165300472790eb80a1991f469c
```

| status | 含义 | 能否下载 |
|--------|------|----------|
| `queued` | 排队中 | 否 |
| `running` | OCR / MolScribe 进行中（大 PDF 可能要数分钟） | 否 |
| `done` | 完成 | **可以** |
| `failed` | 失败，看 `error` 字段 | 否 |

`done` 时响应里会有：

- `result_url`：例如 `/v1/jobs/<job_id>/markdown`（相对路径，前面加主机即可）
- `stats.smiles_replaced`：成功替换成 SMILES 的图片数量

也可以用循环等待（Linux / macOS）：

```bash
JOB=098b1a165300472790eb80a1991f469c
while true; do
  ST=$(curl -s http://127.0.0.1:8000/v1/jobs/$JOB | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "status=$ST"
  [ "$ST" = "done" ] || [ "$ST" = "failed" ] && break
  sleep 20
done
```

#### 步骤 ③ 用正确 URL 下载

**推荐（请原样复制，只改 job_id）：**

```bash
curl -OJ http://127.0.0.1:8000/v1/jobs/098b1a165300472790eb80a1991f469c/markdown
```

兼容写法（效果相同）：

```bash
curl -OJ http://127.0.0.1:8000/v1/jobs/098b1a165300472790eb80a1991f469c/result.md
```

本地一般会得到类似 `WO2025050169A1_final.md` 的文件（由响应头文件名决定）。

**服务器磁盘上的等价文件（若你有机器权限）：**

```text
$DATA_DIR/jobs/<job_id>/result.md
```

Compose 默认数据卷对应容器内 `/data/jobs/<job_id>/result.md`。

**不要打开：**

```text
$DATA_DIR/jobs/<job_id>/work/mineru/**/*.md    ← 中间稿
```

---

### 3. 常见踩坑（同事服务器上真实发生过）

#### 坑 A：下载下来只有 `{"detail":"Not Found"}`

原因：URL 写错，或 job_id 不存在。curl `-OJ` 仍会把 404 JSON 存成 `result.md`，看起来像“下好了”，其实只有约 **22 字节**。

处理：

1. 先 `curl http://127.0.0.1:8000/v1/jobs/<job_id>`，确认存在且 `status=done`
2. 再用 **`.../markdown`** 下载（见上文）
3. 检查文件大小：正常专利结果通常是 **几十～几百 KB**；只有几十字节几乎肯定是错误响应

#### 坑 B：Markdown 里结构式还是图片，没有 SMILES

可能原因：

1. 打开了 MinerU 中间稿（`work/mineru/...`），不是 `result.md` / `*_final.md`
2. 任务还在 `running` 就拷贝了中间文件
3. 该图置信度低于阈值（默认 `MOLSCRIBE_CONFIDENCE=0.5`），会故意保留原图；看 `stats.smiles_replaced` 是否 > 0

#### 坑 C：不想记 URL → 用浏览器

打开 http://127.0.0.1:8000/docs → `POST /v1/jobs` 上传 → `GET /v1/jobs/{job_id}` 看状态 → `GET /v1/jobs/{job_id}/markdown` 下载。

---

### 4. 接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 存活检查 |
| `POST` | `/v1/jobs` | 上传 PDF（字段名必须是 `file`） |
| `GET` | `/v1/jobs/{id}` | 查状态；`done` 时看 `result_url` / `stats` |
| `GET` | `/v1/jobs/{id}/markdown` | **下载结果（推荐）** |
| `GET` | `/v1/jobs/{id}/result.md` | 下载结果（兼容别名，内容相同） |
| `GET` | `/v1/jobs/{id}/images/{name}` | 可选：下载仍保留的图片 |

同一时间默认只跑 **1** 个任务。

---

### 5. 一条龙示例

```bash
# 1) 上传
RESP=$(curl -s -F "file=@WO2025050169A1.pdf" http://127.0.0.1:8000/v1/jobs)
echo "$RESP"
JOB=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

# 2) 等到 done
curl -s http://127.0.0.1:8000/v1/jobs/$JOB
# 直到 "status":"done"

# 3) 下载（注意是 /markdown）
curl -OJ http://127.0.0.1:8000/v1/jobs/$JOB/markdown

# 4) 抽查：应能搜到反引号 SMILES
grep -n 'CC(C)' ./*_final.md | head
```

本机实测（WO2025050169A1）：约 **5.5 分钟**，59 张图中 **42** 张替换为 SMILES。

---

## 架构说明

为避免 MinerU / MolScribe / API 依赖互相冲突，采用与 Wipo-agent `chemistry_extractor` 相同的思路：**三个独立虚拟环境 + 子进程调用**。

```
上传 PDF
  → API 进程（FastAPI，很轻）
  → 子进程调用 MinerU 环境（OCR）
  → 子进程调用 MolScribe 环境（结构式 → SMILES）
  → 下载 Markdown（/v1/jobs/{id}/markdown）
```

| 环境 | 职责 |
|------|------|
| `api` | 仅跑 FastAPI，不 import torch / mineru / molscribe |
| `mineru` | 安装 `mineru[all]`，跑 `mineru` CLI |
| `molscribe` | 安装同事 fork 的 MolScribe，跑 `workers/molscribe_predict_batch.py` |

首次 Docker 构建较久；首次任务还会下载模型到数据目录（Compose 下为卷 `/data/cache`）。

## 配置项

参考 [`.env.service.example`](.env.service.example)。常用：

| 变量 | 含义 | 默认 |
|------|------|------|
| `MINERU_BACKEND` | MinerU 后端 | `pipeline` |
| `MINERU_METHOD` | `ocr` / `auto` / `txt` | `ocr` |
| `MOLSCRIBE_DEVICE` | `cuda` / `cpu` | Compose CPU 版为 `cpu` |
| `MOLSCRIBE_CONFIDENCE` | 替换 SMILES 的最低置信度 | `0.5` |
| `DATA_DIR` | 任务与缓存目录 | `/data` |
| `MAX_UPLOAD_MB` | 上传大小上限 | `200` |

## 目录结构

```
app/                  FastAPI、任务状态、流水线编排
app/mineru_runner.py  子进程调用 MinerU
app/molscribe_runner.py 子进程调用 MolScribe
workers/              仅在 MolScribe venv 中运行的脚本
Dockerfile            构建三套 /opt/venvs/*
docker-compose.yml    CPU 部署
docker-compose.gpu.yml GPU 覆盖
src/ + main.py        旧版「单 venv CLI」（保留作对照）
```

## 与旧版 CLI / Wipo-agent 的区别

| | 旧版 CLI（`src/`） | 本服务 | Wipo-agent |
|--|-------------------|--------|------------|
| 使用方式 | 本地命令行 | HTTP 上传下载 | 靶点检索 + 入库 |
| 依赖隔离 | 单 venv | 三 venv 子进程 | 主环境 + 化学双 venv |
| OCR→SMILES | 有 | 有 | 可选 `chemistry_extractor` |
| 序列 / 活性 / DB | 无 | 无 | 有 |

更细的对比见 [COMPARISON.md](COMPARISON.md)。

## 依赖说明

- API：`requirements-api.txt`
- MinerU：`requirements-mineru.txt`
- MolScribe：`requirements-molscribe.txt`（含同事 fork；`albumentations==1.3.1` 用于兼容）

如需把同事改进后的 MolScribe 换成私有仓库地址，只需改 `requirements-molscribe.txt` 中的 git 源，并重建 molscribe 环境 / 镜像。
