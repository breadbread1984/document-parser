# Document Parser 服务

通过 HTTP 上传 PDF，用 **MinerU** 做 OCR/版面解析生成 Markdown；遇到化学结构式图片时，用 **MolScribe** 转成 SMILES，最后可下载处理后的 Markdown。

> 本服务**只做** PDF → Markdown（含结构式 SMILES），不做专利检索、序列抽取、活性/渗透性入库。那些能力在 Wipo-agent。

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

## 快速启动（Docker Compose）

```bash
# CPU
docker compose up --build -d

# GPU（需 NVIDIA Container Toolkit）
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d
```

服务地址：

- API 文档：http://localhost:8000/docs  
- 健康检查：http://localhost:8000/health  

首次构建镜像较久（要装 MinerU / MolScribe）；首次任务还会下载模型到数据卷 `/data/cache`。

## 接口用法

```bash
# 1. 上传 PDF，拿到 job_id
curl -F "file=@专利.pdf" http://localhost:8000/v1/jobs

# 2. 查询状态（queued → running → done / failed）
curl http://localhost:8000/v1/jobs/<job_id>

# 3. 完成后下载 Markdown
curl -OJ http://localhost:8000/v1/jobs/<job_id>/markdown
```

| 接口 | 说明 |
|------|------|
| `GET /health` | 存活检查 |
| `POST /v1/jobs` | 上传 PDF，创建异步任务 |
| `GET /v1/jobs/{id}` | 任务状态、统计、错误信息 |
| `GET /v1/jobs/{id}/markdown` | 下载结果 Markdown（仅 `done`） |

同一时间默认只跑 **1** 个任务（避免 GPU/CPU 抢占）。

## 本机双 venv（不用 Docker）

Linux / macOS：

```bash
bash scripts/setup_venvs.sh
export DATA_DIR="$PWD/data"
export MINERU_VENV="$PWD/.venvs/mineru"
export MOLSCRIBE_VENV="$PWD/.venvs/molscribe"
.venvs/api/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Windows（已有可用的 MinerU+MolScribe 环境时，可先指向同一 `.venv` 做联调）：

```powershell
$env:DATA_DIR = "$PWD\data"
$env:MINERU_VENV = "$PWD\.venv"      # 或独立的 .venvs\mineru
$env:MOLSCRIBE_VENV = "$PWD\.venv"   # 或独立的 .venvs\molscribe
$env:MOLSCRIBE_DEVICE = "cuda"       # 无 GPU 则填 cpu
.\.venvs\api\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
```

## 实测记录（WO2025050169A1）

| 项 | 结果 |
|----|------|
| 输入 | `WO2025050169A1.pdf`（约 4.4 MB） |
| 状态 | `done` |
| 耗时 | 约 **5.5 分钟**（本机 RTX 3060，模型已缓存） |
| 图片 | 发现 59 张，解析 59 张 |
| SMILES 替换 | **42** 张（置信度 ≥ 0.5） |
| 产物 | 可下载 Markdown（约 160 KB） |

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
