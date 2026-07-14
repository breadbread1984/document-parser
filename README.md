# Document Parser 服务

通过 HTTP 上传 PDF，用 **MinerU** 做 OCR/版面解析生成 Markdown；遇到化学结构式图片时，用 **MolScribe** 转成 SMILES，最后可下载处理后的 Markdown。

> 本服务**只做** PDF → Markdown（含结构式 SMILES），不做专利检索、序列抽取、活性/渗透性入库。那些能力在 Wipo-agent。

---

## 使用方法

### 1. 启动服务

**方式 A：Docker Compose（推荐部署）**

```bash
# CPU
docker compose up --build -d

# GPU（需已安装 NVIDIA Container Toolkit）
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d
```

**方式 B：本机 Windows（当前联调方式）**

先准备好 API 环境（`.venvs\api`）以及带 MinerU/MolScribe 的环境（可用已有 `.venv`）：

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

启动成功后：

| 地址 | 说明 |
|------|------|
| http://127.0.0.1:8000/docs | Swagger 交互文档（可直接上传 PDF） |
| http://127.0.0.1:8000/health | 健康检查，返回 `{"status":"ok",...}` |

### 2. 处理一份 PDF（三步）

任务是异步的：上传后立刻返回 `job_id`，处理完再下载。

#### 步骤 ① 上传

```bash
curl -F "file=@你的专利.pdf" http://127.0.0.1:8000/v1/jobs
```

返回示例：

```json
{
  "job_id": "23e25fb7cdbd4f97affb5f4739103b7e",
  "status": "queued",
  "message": "Job accepted. Poll GET /v1/jobs/{job_id}; download GET /v1/jobs/{job_id}/markdown when done."
}
```

PowerShell：

```powershell
curl.exe -F "file=@C:\path\to\patent.pdf" http://127.0.0.1:8000/v1/jobs
```

#### 步骤 ② 查询状态

把上面的 `job_id` 替换进去：

```bash
curl http://127.0.0.1:8000/v1/jobs/<job_id>
```

状态含义：

| status | 含义 |
|--------|------|
| `queued` | 已排队 |
| `running` | 正在 OCR / 识别结构式 |
| `done` | 完成，可下载 |
| `failed` | 失败，看返回里的 `error` 字段 |

`done` 时还会带 `stats`（如图片数、SMILES 替换数）和 `result_url`。

#### 步骤 ③ 下载 Markdown

仅当 `status=done`：

```bash
curl -OJ http://127.0.0.1:8000/v1/jobs/<job_id>/markdown
```

会保存为类似 `WO2025050169A1_final.md` 的文件。结构式若置信度够高，会变成行内 SMILES（反引号包裹）；不够高的仍保留原图引用。

### 3. 接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 存活检查 |
| `POST` | `/v1/jobs` | 上传 PDF（`multipart/form-data`，字段名 `file`） |
| `GET` | `/v1/jobs/{id}` | 查状态 / 统计 / 错误 |
| `GET` | `/v1/jobs/{id}/markdown` | 下载结果 Markdown |
| `GET` | `/v1/jobs/{id}/images/{name}` | 可选：下载结果中保留的图片 |

同一时间默认只跑 **1** 个任务（避免 GPU/CPU 抢占）。也可用浏览器打开 `/docs` 点「Try it out」完成上传与下载。

### 4. 完整示例（WO2025050169A1）

```bash
# 上传
curl -F "file=@WO2025050169A1.pdf" http://127.0.0.1:8000/v1/jobs
# → job_id=...

# 轮询直到 done（大专利大约数分钟）
curl http://127.0.0.1:8000/v1/jobs/<job_id>

# 下载
curl -OJ http://127.0.0.1:8000/v1/jobs/<job_id>/markdown
```

本机实测：约 **5.5 分钟**，59 张图中 **42** 张替换为 SMILES。

---

## 架构说明

为避免 MinerU / MolScribe / API 依赖互相冲突，采用与 Wipo-agent `chemistry_extractor` 相同的思路：**三个独立虚拟环境 + 子进程调用**。

```
上传 PDF
  → API 进程（FastAPI，很轻）
  → 子进程调用 MinerU 环境（OCR）
  → 子进程调用 MolScribe 环境（结构式 → SMILES）
  → 下载 Markdown
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
