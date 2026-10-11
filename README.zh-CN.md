<div align="center">

# KSM — 知识服务模块

**面向 AI 业务系统的本地知识基础设施，基于 Obsidian Vault 标准。**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green.svg)](https://fastapi.tiangolo.com/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB.svg)](https://react.dev/)

[English](README.md) | [中文](README.zh-CN.md)

</div>

---

<div align="center">
  <img src="assets/screenshots/dashboard.png" alt="KSM Web 控制台仪表盘，展示实例、知识统计和写入操作" width="100%">
</div>

## 什么是 KSM？

KSM 是一个面向 AI 业务系统的本地知识服务模块。系统以 Obsidian Vault 为底层知识载体，通过标准目录、frontmatter 元数据、Wikilinks 和 SQLite 索引，把原始资料加工成可追溯、可检索、可组织的结构化知识节点。

### 核心能力

| 能力 | 说明 |
|------|------|
| **实例管理** | 创建和管理隔离的知识库实例，生成标准 vault 目录结构 |
| **知识写入** | 将 Markdown 文档加工为资料来源、知识卡片和知识地图 |
| **异步入库** | SSE 驱动的 8 步管线，展示卡片、实体、关系数量与去重状态 |
| **写入密度控制** | 按文档预算限制卡片、实体和关系数量，避免大文档生成失控的知识图谱 |
| **模型配置** | 在 Web 控制台中配置一个 LLM 模型和一个 embedding 模型，并支持获取可用模型 |
| **关系组织** | 解析和索引知识关系（source_trace、extracted_from、concept_overlap 等） |
| **结构化检索** | 基于意图的检索，返回 core_hits、related_cards、source_notes 和 maps |
| **知识图谱** | 基于 React Flow 的交互式可视化，支持三层筛选 |
| **Web 控制台** | 浏览器管理界面，包含仪表盘、图谱、入库、检索、笔记详情和设置 |
| **本地优先** | 数据存储在本地文件系统和 SQLite，便于调试、迁移和人工审阅 |

### 技术栈

| 层级 | 技术 |
|------|------|
| **后端** | Python 3.11+ / FastAPI / SQLite (FTS5) / 本地文件系统 |
| **前端** | React 19 / TypeScript / Vite / Tailwind CSS / shadcn/ui / Zustand / React Flow |

---

## 界面预览

<table>
  <tr>
    <td width="50%"><img src="assets/screenshots/graph.png" alt="交互式知识图谱"></td>
    <td width="50%"><img src="assets/screenshots/knowledge.png" alt="知识库浏览与筛选"></td>
  </tr>
  <tr>
    <td width="50%"><img src="assets/screenshots/ingest.png" alt="写入任务列表，展示卡片、实体、关系和去重指标"></td>
    <td width="50%"><img src="assets/screenshots/settings.png" alt="LLM 与 embedding 模型配置"></td>
  </tr>
</table>

---

## 快速开始

### 前置要求

- Python 3.11+
- Node.js 18+
- 一个 LLM 提供商和一个 embedding 提供商的 API 访问权限（推荐配置 embedding；未配置时会退化为规则去重）

### 1. 克隆并配置后端

```bash
git clone https://github.com/ningQT/KSM.git
cd KSM

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 2. 启动后端

```bash
uvicorn app.api.app:create_app --factory --host 127.0.0.1 --port 8900
```

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

打开 http://localhost:5173

### 4. 配置模型

打开 http://localhost:5173/settings，配置：

- **LLM 模型**：用于写入、回答合成和其他模型辅助任务。
- **embedding 模型**：用于语义去重和向量召回。

模型配置会保存到本地数据库。未配置 embedding 模型时，写入仍会继续，但系统会退化为规则去重，并在界面中提示。

---

## Docker 部署

创建部署环境文件，并设置浏览器访问地址和前端端口：

```bash
cp .env.example .env
```

```dotenv
KSM_PUBLIC_HOST=你的服务器IP或域名
KSM_FRONTEND_PORT=8080
```

启动服务：

```bash
docker compose up -d --build
```

使用 HTTPS 时，同时设置 `KSM_PUBLIC_SCHEME=https` 和 `KSM_SECURE_COOKIES=true`。模型凭据在 Web 控制台中配置。

---

## API 概览

| 分类 | 端点 |
|------|------|
| **实例管理** | `POST/GET /api/v1/instances` |
| **文档入库** | `POST /api/v1/instances/{id}/ingest[/async]` |
| **知识检索** | `POST /api/v1/search` |
| **知识图谱** | `GET /api/v1/instances/{id}/graph` |
| **系统设置** | `GET/PUT /api/v1/settings/models/{category}` |

运行时交互式文档：http://127.0.0.1:8900/docs

---

## Web 控制台

| 页面 | 路径 | 说明 |
|------|------|------|
| 仪表盘 | `/` | 实例列表与知识统计 |
| 知识库 | `/knowledge` | 浏览和筛选来源笔记、知识卡片与地图 |
| 图谱视图 | `/graph` | 交互式三层知识图谱 |
| 入库 | `/ingest` | 文档上传、SSE 实时进度和写入任务指标 |
| 检索 | `/search` | 结构化知识检索 |
| 检索词典 | `/search-lexicon` | 查询扩展与同义词管理 |
| 本体管理 | `/ontology` | 实体类型、关系类型和图谱词表 |
| API 管理 | `/api-management` | API Key 与账户访问控制 |
| 笔记详情 | `/note/:path` | Markdown 渲染与元数据 |
| 设置 | `/settings` | LLM 与 embedding 模型配置，以及账户管理 |

支持亮色/暗色主题。

---

## 配置

LLM 和 embedding 凭据通过 Web 控制台 `/settings` 页面管理，存储在本地数据库中。运行环境相关配置仍由环境变量控制。

主要环境变量：

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `KSM_DATA_DIR` | 否 | `./data/vaults` | 数据存储目录 |
| `KSM_PORT` | 否 | `8900` | HTTP 端口 |
| `KSM_ENABLE_DOCS` | 否 | `true` | 启用 `/docs` 端点 |

模型凭据不再通过 `.env` 管理，请在控制台中配置。

---

## 测试

```bash
# 全量测试
python -m pytest -q

# 单元测试
pytest tests/unit/ -v

# 集成测试
pytest tests/integration/ -v

# 带覆盖率
pytest tests/ --cov=app --cov-report=term-missing
```

---

## 项目结构

```
ksm/
├── app/                    # Python 后端
│   ├── api/                # FastAPI 路由与依赖
│   ├── core/               # 业务服务
│   ├── llm/                # LLM 客户端与提示词
│   ├── pipeline/           # 检索管线
│   ├── storage/            # SQLite + FTS5
│   └── template/           # Vault 模板
├── frontend/               # React 前端
│   └── src/
│       ├── components/     # UI 组件
│       ├── pages/          # 页面组件
│       ├── services/       # API 调用
│       └── stores/         # Zustand 状态
├── configs/                # 策略配置
├── templates/              # Vault 模板
└── tests/                  # 测试套件
```

---

## 许可证

本项目基于 MIT 许可证开源 — 详见 [LICENSE](LICENSE)。
