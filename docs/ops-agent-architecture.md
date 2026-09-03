# 智能运维 Agent 系统架构

## 一、整体架构分层

```mermaid
graph TB
    subgraph "用户层"
        A1[值班人员]
        A2[开发人员]
        A3[运维经理]
    end

    subgraph "通知层"
        N1[钉钉/企微]
        N2[短信/电话]
        N3[运维看板]
    end

    subgraph "Agent 核心层"
        subgraph "⑤ 反馈学习"
            F1[知识库]
            F2[基线模型]
            F3[动作历史]
        end

        subgraph "④ 决策执行"
            E1[DS API Client]
            E2[K8s API Client]
            E3[通知分发器]
        end

        subgraph "③ 决策引擎"
            D1[LLM 推理器]
            D2[规则引擎]
            D3[风险评估器]
            D4[动作验证器]
            D5[熔断器]
        end

        subgraph "② 异常检测"
            C1[规则检测器]
            C2[基线检测器]
            C3[关联分析器]
            C4[异常分类器]
        end

        subgraph "① 数据采集"
            B1[DS API Poller]
            B2[Event Hook]
            B3[集群指标收集]
            B4[数据缓冲层]
        end
    end

    subgraph "基础设施层"
        DS[DolphinScheduler 集群]
        K8s[Kubernetes]
        DB[(Hive / MySQL)]
        MQ[Kafka 消息队列]
    end

    %% 连接关系
    DS --- B1 & B2
    K8s --- B3
    B1 & B2 & B3 --> B4
    B4 --> C1 & C2 & C3
    C1 & C2 & C3 --> C4
    C4 --> D1 & D2
    D1 & D2 & D3 --> D4
    D4 --> D5
    D5 --> E1 & E2 & E3
    E1 --> DS
    E2 --> K8s
    E3 --> N1 & N2 & N3
    N1 & N2 & N3 --> A1 & A2 & A3
    E1 & E2 --> F1 & F3
    F1 --> D1
    F2 --> C2
    A1 -->|手动操作| DS
```

## 二、组件详情

### ① 数据采集层

```mermaid
graph LR
    subgraph "数据源"
        DS1[DS Master]
        DS2[DS Worker]
        DS3[DS API]
        K8s1[K8s API Server]
    end

    subgraph "采集器"
        P1[状态轮询器<br/>间隔: 30s]
        P2[事件监听器<br/>WebSocket]
        P3[指标采集器<br/>间隔: 15s]
    end

    subgraph "缓冲 & 归一化"
        Q1[(Kafka Topic:<br/>ds-raw-events)]
        Q2[事件归一化<br/>Schema 统一]
    end

    subgraph "存储"
        S1[(TSDB<br/>指标存储)]
        S2[(MySQL<br/>事件记录)]
    end

    DS1 & DS2 ---|任务状态| P1
    DS1 & DS2 ---|状态变更推送| P2
    DS3 ---|集群指标| P3
    K8s1 ---|Pod/Node 指标| P3

    P1 & P2 & P3 --> Q1
    Q1 --> Q2
    Q2 --> S1 & S2
```

| 组件 | 职责 | 采集内容 |
|------|------|---------|
| 状态轮询器 | 定时调用 DS REST API | 工作流实例状态、任务实例状态、流程定义 |
| 事件监听器 | 接收 DS 主动推送 | 任务创建/运行/失败/完成事件 |
| 指标采集器 | 采集集群资源指标 | Worker CPU/内存、队列深度、连接池状态 |
| 数据缓冲层 | 削峰填谷、解耦 | Kafka 异步缓冲，统一事件 Schema |

### ② 异常检测层

```mermaid
graph TB
    subgraph "检测器"
        R1[规则检测器]
        B1[基线检测器]
        A1[关联分析器]
    end

    subgraph "规则库"
        RULES[检测规则]
    end

    subgraph "基线模型"
        BM[历史耗时分布<br/>季节性模式<br/>同比/环比]
    end

    subgraph "图谱"
        KG[依赖关系图<br/>资源拓扑]
    end

    subgraph "输出"
        O1[异常事件]
        O2[严重等级 P0-P3]
        O3[根因线索]
    end

    R1 --- RULES
    B1 --- BM
    A1 --- KG

    R1 & B1 & A1 --> O1
    O1 --> O2
    A1 --> O3
```

| 检测器 | 检测方式 | 典型规则 |
|--------|---------|---------|
| **规则检测器** | 确定性匹配 | 超时 > 阈值、重试 > N 次、错误码匹配、资源使用率 > 90% |
| **基线检测器** | 统计异常检测 | 耗时偏离历史均值 3σ、同比/环比偏差 > 50%、季节性模式违例 |
| **关联分析器** | 图谱推理 | 上游任务未完成、资源竞争任务列表、共享资源瓶颈点 |

### ③ 决策引擎 (核心大脑)

```mermaid
graph TB
    subgraph "输入层"
        I1[异常事件]
        I2[集群实时状态]
        I3[历史经验知识库]
        I4[应急预案库]
    end

    subgraph "推理层"
        subgraph "LLM 推理流水线"
            L1[Step 1: 上下文聚合<br/>收集所有相关数据]
            L2[Step 2: 根因分析<br/>LLM 多角度推理]
            L3[Step 3: 动作规划<br/>生成候选方案]
            L4[Step 4: 方案优选<br/>评估成本/效果/风险]
        end

        subgraph "规则引擎"
            R1[确定性规则匹配<br/>快速路径]
        end
    end

    subgraph "安全层"
        S1[动作验证器]
        S2[风险评估器]
        S3[熔断器]
    end

    subgraph "输出层"
        O1[行动计划]
        O2[回滚预案]
        O3[升级决策]
    end

    I1 & I2 & I3 & I4 --> L1
    L1 --> L2 --> L3 --> L4
    I1 --> R1
    R1 -->|低风险快速决策| S1
    L4 -->|复杂决策| S1
    S1 --> S2 --> S3
    S3 --> O1 & O2 & O3
```

| 模块 | 说明 |
|------|------|
| **LLM 推理器** | 核心智能，负责根因分析、动作规划、方案优选。需要强推理能力的模型 |
| **规则引擎** | 快速路径，对已知确定性场景直接出决策，不经过 LLM（降低延迟和成本） |
| **动作验证器** | 安全检查，验证操作是否在白名单、频次是否超限、目标是否在灰度范围 |
| **风险评估器** | 评估动作影响范围、回滚成本、历史成功率 |
| **熔断器** | 自我保护，连续失败 N 次后自动熔断，降级为只告警 |

### ④ 执行层

```mermaid
graph TB
    subgraph "执行器"
        EXEC[执行调度器]
    end

    subgraph "动作通道"
        C1[DS API 通道]
        C2[K8s API 通道]
        C3[通知通道]
    end

    subgraph "DS 操作"
        OP1[rerun_task]
        OP2[adjust_priority]
        OP3[kill_task]
        OP4[block_downstream]
        OP5[modify_timeout]
    end

    subgraph "K8s 操作"
        OP6[scale_worker]
        OP7[adjust_resources]
        OP8[cordon_node]
    end

    subgraph "通知"
        N1[处理摘要]
        N2[升级告警]
        N3[日报/周报]
    end

    EXEC --> C1 & C2 & C3
    C1 --> OP1 & OP2 & OP3 & OP4 & OP5
    C2 --> OP6 & OP7 & OP8
    C3 --> N1 & N2 & N3
```

| 操作 | 触发条件 | 风险等级 | 幂等性 |
|------|---------|---------|-------|
| rerun_task | 任务失败且可重试 | 低 | 是 |
| adjust_priority | 资源竞争导致排队 | 中 | 是 |
| kill_task | 任务卡死/死循环 | 高 | 是 |
| block_downstream | 上游数据异常，防止污染下游 | 中 | 是 |
| scale_worker | Worker 满载/队列积压 | 低 | 是 |
| adjust_resources | 单个任务 OOM/超时 | 低 | 是 |

### ⑤ 反馈学习层

```mermaid
graph LR
    subgraph "数据采集"
        H1[动作记录]
        H2[执行结果]
        H3[人工反馈]
    end

    subgraph "学习 & 优化"
        L1[基线模型更新<br/>· 耗时分布更新<br/>· 阈值动态调整]
        L2[知识库沉淀<br/>· 异常-原因-动作映射<br/>· 成功/失败案例]
        L3[规则优化<br/>· 新增规则<br/>· 调整规则参数]
    end

    subgraph "输出"
        O1[更新后的基线]
        O2[优化后的规则]
        O3[运维报告]
    end

    H1 & H2 & H3 --> L1 & L2 & L3
    L1 --> O1
    L3 --> O2
    L1 & L2 & L3 --> O3
```

## 三、部署架构

```mermaid
graph TB
    subgraph "Kubernetes 集群"
        subgraph "ds-namespace"
            DS_M1[DS Master-1]
            DS_M2[DS Master-2]
            DS_W1[DS Worker-1]
            DS_W2[DS Worker-2]
            DS_WN[DS Worker-N]
        end

        subgraph "ops-agent-namespace"
            SUBGRAPH "Agent 服务"
                API[Operator API<br/>2 pods]
                SVC[Agent Core<br/>2 pods]
                CACHE[(Redis<br/>缓存/队列)]
            end

            subgraph "MCP Servers"
                M1[ds-monitor<br/>2 pods]
                M2[ds-operator<br/>2 pods]
                M3[knowledge<br/>1 pod]
            end

            subgraph "数据管道"
                K1[Kafka<br/>3 brokers]
                FLINK[Flink<br/>实时处理]
            end

            subgraph "存储"
                TSDB[(Prometheus<br/> + Thanos)]
                MYSQL[(MySQL<br/>主从)]
            end
        end
    end

    subgraph "外部依赖"
        LDAP[LDAP/SSO]
        MAIL[邮件网关]
        SMS[短信网关]
    end

    DS_M1 & DS_M2 ---|API| M1 & M2
    M1 & M2 & M3 ---|stdio IPC| SVC
    SVC --- API
    API ---|外部访问| LDAP
    SVC ---|通知| MAIL & SMS
```

## 四、数据流全景

```mermaid
flowchart LR
    subgraph "数据流"
        direction LR
        SRC[(Dolphin<br/>Scheduler)]
        COL[采集]
        DET[检测]
        DEC[决策]
        EXE[执行]
        FB[反馈]
        DEST[(优化后的<br/>系统)]
    end

    SRC -->|"① 原始数据<br/>状态/日志/指标"| COL
    COL -->|"② 归一化事件<br/>统一Schema"| DET
    DET -->|"③ 异常事件<br/>+上下文"| DEC
    DEC -->|"④ 行动计划<br/>+回滚预案"| EXE
    EXE -->|"⑤ 执行结果"| FB
    FB -->|"⑥ 基线更新<br/>知识沉淀"| DEST

    %% 存储层
    COL -.->|写入| TSDB[(TSDB)]
    COL -.->|写入| MQ[(Kafka)]
    DEC -.->|查询| KG[(知识图谱)]
    DET -.->|读取| TSDB
    DEC -.->|读取| MQ
```

## 五、技术栈

| 层 | 组件 | 技术选型 | 说明 |
|----|------|---------|------|
| 数据采集 | Poller | Python + asyncio + aiohttp | 异步非阻塞轮询 |
| 数据采集 | Event Hook | DS Webhook / Python | 接收 DS 推送 |
| 数据缓冲 | 消息队列 | Kafka | 削峰填谷，异步解耦 |
| 异常检测 | 规则引擎 | Drools / Python Rule Engine | 确定性规则匹配 |
| 异常检测 | 基线模型 | Prophet / statsmodels | 时序异常检测 |
| 决策引擎 | LLM 推理 | GPT-4 / DeepSeek / Qwen | 根因分析 + 动作规划 |
| 决策引擎 | 知识库 | Chroma / Milvus 向量库 | 相似故障检索 |
| 执行层 | DS API | Python requests + DS REST API | 操作 DS |
| 执行层 | K8s API | kubernetes-client Python | 操作 K8s |
| 存储 | 时序数据 | Prometheus + Thanos | 指标存储 |
| 存储 | 事件数据 | MySQL / PostgreSQL | 事件记录 |
| 存储 | 缓存 | Redis | 实时状态、队列 |
| 通知 | 消息推送 | 钉钉/企微 Robot API | 告警通知 |