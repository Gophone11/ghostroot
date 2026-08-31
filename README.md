# Ghostroot

Ghostroot 是一个面向**授权 Web 渗透测试**与 **CTF Web 渗透场景**的多智能体自动化测试系统。系统以测试入口和授权约束为起始状态，以明确的测试目标为终止状态，组织智能体持续完成信息收集、漏洞假设、验证利用、结果归纳和报告生成。

Ghostroot 的核心不是保存冗长的自然语言执行日志，而是将多轮测试过程表征为可检索、可追溯、可用于决策控制的 Web 渗透测试状态 DAG 图。图中的事实节点保存结构化原子事实，意图边记录下一步测试行为及其来源；决策模块据此选择后续路径，并对已经证实不可行的路线进行剪枝。

> 本项目仅限用于已取得明确授权的安全测试、靶场、教学研究和 CTF 竞赛。禁止将其用于任何未经授权的目标。

## 适用场景

- 企业授权 Web 应用安全测试，包括资产入口探索、接口验证、鉴权测试和漏洞证据整理。
- CTF Web 题目与合法靶场中的多步攻击路径搜索。
- 需要长时间、多回合执行的 Web 渗透任务，以及执行中间状态的恢复与追踪。
- 渗透测试过程复盘、状态图展示、YAML/时间线导出和测试报告生成。

## 核心设计

### 1. 基于 DAG 图的 Web 渗透测试状态表征

Ghostroot 将一次测试项目表示为由**起始节点、事实节点、意图边和终止节点**构成的有向无环图：

- **起始节点**：保存目标入口、测试基本信息和授权约束，是状态演化的起点。
- **事实节点**：保存某一步探索后已经确认的测试状态，不把未经验证的推测写成事实。
- **意图边**：连接来源事实与后继事实，记录智能体为什么执行某项测试、准备验证什么以及何时停止。
- **终止节点**：表示项目目标已经由可核验事实证明，而不是仅凭智能体主观判断完成。

一个意图可以引用一个或多个来源事实。执行智能体完成验证后，系统把确认结果写入新的事实节点，并以该意图边建立来源事实到新事实的状态转移关系，从而保留完整的决策依据和探索路径。

### 2. 结构化原子事实

事实节点以事实类型、事实状态、目标相关性和原子事实集合为核心语义。每条原子事实由以下四个字段组成：

| 字段 | 代码字段 | 含义 |
| --- | --- | --- |
| 主体 | `subject` | 事实所描述的接口、参数、凭据、服务、能力或其他测试对象 |
| 关系 | `predicate` | 主体与客体之间已经确认的关系 |
| 客体 | `object` | 关系指向的属性、结果、对象或证据 |
| 目标作用属性 | `polarity` | 积极性属性（`positive`）表示有助于接近或证明测试目标，消极性属性（`negative`）表示阻碍目标或排除当前路线 |

积极性属性和消极性属性表示事实对测试目标的作用方向，不表示事实本身的真假。写入状态图的原子事实均应当是已经确认的信息。

示例：

```yaml
facts:
  - id: fact_012
    kind: exploration_result
    outcome: mixed
    goal_relevance: advances
    atoms:
      - subject: endpoint:/admin
        predicate: returns
        object: authenticated management page
        polarity: positive
      - subject: route:file-upload
        predicate: rejects
        object: executable file type
        polarity: negative
```

其中，`kind`、`outcome`、`goal_relevance` 和 `atoms` 分别对应事实类型、事实状态、目标相关性和原子事实集合。前端还可以展示事实描述、标签等辅助信息，便于测试人员阅读、审计和复盘。

### 3. 事实—意图状态转移

Ghostroot 使用“事实产生意图、意图产生新事实”的闭环推进测试：

1. 决策智能体读取当前项目的 YAML 状态图快照，以结构化原子事实作为主要决策依据。
2. 决策智能体从现有事实节点出发，生成一个或多个候选测试意图。
3. 调度器将意图分配给隔离的项目工作容器。
4. 执行智能体调用测试工具验证意图，并归纳已确认的结果。
5. 系统将结果写成新的事实节点，将本次意图写成来源事实与新事实之间的有向边。
6. 若新事实足以证明项目目标，则连接终止节点；否则进入下一轮决策。

这种表征方式将“已确认状态”和“待执行计划”分开，能够明确回答每个事实从哪里产生、每次测试为什么发生，以及某条路径为什么继续或停止。

### 4. 失败路径剪枝

系统保留具有消极性属性的原子事实，并在决策结果进入执行阶段前检查候选意图。若候选意图重复了来源事实中已经确认失败或不可行的路线，决策护栏可以阻止其再次执行；若同一事实同时给出了仍然可行的替代路线，则保留替代探索方向。

失败事实不会从状态图中删除。它既是测试证据，也是后续剪枝依据，可减少重复尝试及由此产生的无效工具调用和模型上下文消耗。

### 5. 面向多智能体的工程运行机制

- **Bootstrap**：基于起始节点进行初始探测，建立首批事实节点。
- **Reason**：读取状态图，判断目标是否完成并生成下一批测试意图。
- **Explore**：在项目容器中执行意图，调用工具并产出结构化事实。
- **Report**：汇总状态图、工具事件和目标证据，生成测试报告。
- **Dispatcher**：负责任务调度、并发限制、租约心跳、容器生命周期和工作智能体选择。
- **Web/API**：提供项目管理、DAG 图展示、事实与意图详情、指标及导出能力。

当前支持的工作智能体适配器包括 **Claude Code**、**Codex** 和 **Pi**。

## 运行流程

## 项目组成

| 模块 | 路径 | 作用 |
| --- | --- | --- |
| Web 服务与前端 | `ghostroot/src/ghostroot/server/` | 项目、事实、意图、报告、状态图展示及 API |
| 调度器 | `ghostroot/src/ghostroot/dispatcher/scheduler/` | 项目调度、任务编排和并发控制 |
| 智能体任务 | `ghostroot/src/ghostroot/dispatcher/tasks/` | Bootstrap、Reason、Explore 和 Report 流程 |
| 决策与运行护栏 | `ghostroot/src/ghostroot/dispatcher/decision_guardrails.py`、`guardrails.py` | 失败路线过滤和项目级停止判断 |
| 容器运行时 | `ghostroot/src/ghostroot/dispatcher/runtime/` | 项目容器、进程、心跳和启动检查 |
| 提示词与协议 | `ghostroot/src/ghostroot/dispatcher/prompts/`、`protocol/` | 状态上下文输入、结构化输出和服务通信 |

## 部署与运行

### 环境要求

- macOS 或 Linux；Windows 建议使用 WSL2。
- Docker Engine 与 Docker Compose 插件，且 Docker daemon 已启动。
- 手动运行时需要 Python 3.12 或更高版本及 [uv](https://docs.astral.sh/uv/)。
- 可用的大模型 API 地址、模型名称和访问凭据。

启动前可用以下命令确认 Docker 正常：

```bash
docker info
```

Linux 上若 Docker 尚未启动，可执行：

```bash
sudo systemctl start docker
```

### 1. 拉取工作容器镜像

Docker Compose 和手动启动均需要工作容器镜像：

```bash
docker pull --platform=linux/amd64 ghcr.io/oritera/ghostroot-worker-container:latest
```

### 2. 创建调度配置

```bash
cp dispatch.example.yaml dispatch.yaml
```

编辑 `dispatch.yaml`，填写模型端点、模型名称和 API 凭据，并按运行方式设置 `server`：

- Docker Compose：`http://ghostroot-server:8000`
- 宿主机手动运行：`http://127.0.0.1:8000`

不要将真实 API 密钥提交到版本库。

### 3. Docker Compose 启动（推荐）

先拉取构建 Ghostroot 使用的基础镜像：

```bash
docker pull ghcr.io/astral-sh/uv:python3.13-trixie
```

启动服务：

```bash
docker compose up --build
```

该命令会启动 `ghostroot-server` 和 `ghostroot-dispatcher`。服务端健康检查通过后，调度器自动启动；调度器通过宿主机 Docker socket 创建每个项目的隔离工作容器。项目数据持久化到 `./datas/ghostroot/`。

启动完成后访问：<http://127.0.0.1:8000>

停止服务：

```bash
docker compose down
```

### 4. 手动启动

确保 Docker daemon 正常运行，并打开两个终端。

终端一启动 Web 服务：

```bash
uv run --project ghostroot ghostroot serve --host 127.0.0.1 --no-access-log
```

终端二启动调度器：

```bash
uv run --project ghostroot ghostroot dispatch --config dispatch.yaml
```

如需仅检查调度配置、工作智能体和容器环境：

```bash
uv run --project ghostroot ghostroot dispatch --config dispatch.yaml --startup-healthcheck-only
```

## 测试

运行不依赖 Docker 和在线模型端点的回归测试：

```bash
uv run --project ghostroot --group dev pytest
```

