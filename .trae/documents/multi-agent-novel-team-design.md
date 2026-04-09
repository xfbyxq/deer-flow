# 多智能体组合（小说智能体团队）实现计划

## 目标
在 DeerFlow 框架中构建一个可配置的多智能体组合系统，支持创建小说智能体团队。

## 核心设计

### 1. 智能体类型定义
基于需求（创意策划型、内容创作型、审核校对型），设计以下专业智能体：

| 智能体名称 | 职责 | 核心能力 |
|-----------|------|----------|
| `story-planner` | 创意策划 | 创意生成、情节设计、角色设定、世界观构建 |
| `content-writer` | 内容创作 | 初稿撰写、文笔润色、风格统一、章节扩展 |
| `proofreader` | 审核校对 | 错别字检查、语法修正、逻辑审核、一致性检查 |

### 2. 协作模式
采用**层级协作**模式：
- `lead-agent`（主编/协调者）：负责任务分发、进度协调、最终审核
- `story-planner`：负责前期创意策划
- `content-writer`：负责内容撰写
- `proofreader`：负责质量把控

### 3. 配置文件方式
使用 YAML 配置文件定义智能体组合，与现有 DeerFlow 配置体系保持一致。

---

## 实现步骤

### 步骤 1：扩展 Subagent 配置系统
**文件**: `backend/packages/harness/deerflow/subagents/config.py`

- 添加新字段：`role`（角色类型）、`team`（所属团队）、`next_agents`（完成后可调用的下游智能体）
- 支持智能体间调用关系定义

### 步骤 2：创建小说智能体配置
**目录**: `backend/packages/harness/deerflow/subagents/builtins/novel/` (或直接放在 builtins 中)

创建三个专业智能体配置文件：

1. **story_planner.py** - 创意策划智能体
   - 系统提示词：强调创意生成、故事结构、角色弧光
   - 工具：搜索工具、文件读取工具

2. **content_writer.py** - 内容创作智能体  
   - 系统提示词：强调写作风格、文笔润色、上下文保持
   - 工具：文件读写工具

3. **proofreader.py** - 审核校对智能体
   - 系统提示词：强调语法检查、逻辑连贯性、一致性
   - 工具：搜索工具、文件分析工具

### 步骤 3：创建智能体组合配置
**新文件**: `backend/packages/harness/deerflow/config/teams_config.py`

定义 `AgentTeam` 配置类：
- `team_name`: 团队名称（如 `novel-writing-team`）
- `lead_agent`: 主编智能体名称
- `members`: 团队成员列表
- `workflow`: 工作流程定义（顺序/并行/条件分支）

### 步骤 4：扩展 invoke_acp_agent_tool
**文件**: `backend/packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py`

- 支持调用智能体团队（而非单个智能体）
- 添加团队协作模式支持

### 步骤 5：示例配置文件
**目录**: `config.example.yaml` (或新增示例)

添加小说智能体团队配置示例：

```yaml
teams:
  novel-writing-team:
    description: "小说创作智能体团队"
    lead_agent: "content-writer"
    workflow: "sequential"  # 顺序执行
    members:
      - story-planner
      - content-writer  
      - proofreader
```

### 步骤 6：文档
**文件**: `backend/docs/MULTI_AGENT_TEAMS.md`

编写使用文档说明如何创建和使用智能体团队。

---

## 技术细节

### 智能体间通信
- 使用现有 message_bus 进行消息传递
- 每个智能体完成任务后返回结构化结果
- 下游智能体可访问上游智能体的输出

### 配置加载流程
1. 加载 `teams` 配置
2. 验证团队成员存在
3. 构建智能体调用图
4. 运行时根据 workflow 执行

---

## 预期效果

用户可以通过配置文件定义智能体团队：
```yaml
teams:
  my-novel-team:
    lead_agent: "content-writer"
    members: ["story-planner", "content-writer", "proofreader"]
```

然后在对话中调用：
```
/invoke-team my-novel-team 帮我写一个科幻小说
```

系统将按顺序调用各个专业智能体完成创作任务。
