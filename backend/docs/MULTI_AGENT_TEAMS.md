# Multi-Agent Teams

DeerFlow 支持创建和管理多智能体团队（Agent Teams），让多个专业智能体协同工作来完成复杂任务。

## 概述

多智能体团队允许你定义一组专业化的子智能体（subagents），它们可以按顺序、并行或条件分支的方式协作。这种模式特别适合需要多种专业技能的复杂任务，例如创作小说、编写技术文档等。

## 内置小说智能体团队

DeerFlow 内置了一个完整的小说创作智能体团队，包含以下三种专业智能体：

### 1. story-planner（创意策划）
- **职责**：负责前期创意策划
- **核心能力**：
  - 创意生成与故事概念设计
  - 情节结构规划
  - 角色设定与动机设计
  - 世界观构建
- **适用场景**：用户想要创建新故事、需要情节设计和角色背景

### 2. content-writer（内容创作）
- **职责**：负责实际的内容撰写
- **核心能力**：
  - 文笔润色与风格统一
  - 章节内容扩展
  - 对话与场景描写
  - 上下文连续性保持
- **适用场景**：需要撰写实际的故事情节、章节内容

### 3. proofreader（审核校对）
- **职责**：负责质量审核
- **核心能力**：
  - 错别字和拼写检查
  - 语法和标点审核
  - 叙事一致性验证
  - 逻辑漏洞检测
- **适用场景**：需要审核已写内容、确保质量

## 配置方法

### 在 config.yaml 中定义团队

```yaml
teams:
  novel-writing-team:
    description: "小说创作智能体团队"
    lead_agent: "content-writer"
    workflow: "sequential"
    members:
      - "story-planner"
      - "content-writer"
      - "proofreader"
```

### 配置选项说明

| 配置项 | 类型 | 说明 |
|--------|------|------|
| `team_name` | string | 团队唯一标识符 |
| `description` | string | 团队描述 |
| `lead_agent` | string | 主编智能体名称（协调者） |
| `workflow` | string | 协作模式：`sequential`（顺序）、`parallel`（并行）、`conditional`（条件） |
| `members` | list | 团队成员智能体名称列表 |

## 使用方法

### 使用 invoke_team 工具

在对话中，你可以使用 `invoke_team` 工具来调用整个智能体团队：

```
invoke_team 工具参数：
- description: 任务描述（简短）
- prompt: 详细的任务提示词
- team_name: 团队名称（如 novel-writing-team）
```

### 示例对话

**用户**：
```
帮我写一个科幻小说，讲述人类在火星建立第一个殖民地
```

**AI 使用工具**：
```python
invoke_team(
    description="创作科幻小说",
    prompt="创作一个关于人类在火星建立第一个殖民地的科幻小说。要求：1）有完整的故事结构 2）角色鲜活 3）科幻设定合理 4）有冲突和解决过程",
    team_name="novel-writing-team"
)
```

### 执行流程

1. **story-planner** 首先接手，分析任务需求
   - 生成故事概念和情节大纲
   - 设计主要角色和背景设定
   - 输出：故事策划文档

2. **content-writer** 基于策划开始撰写
   - 创作章节内容
   - 发展角色和情节
   - 输出：完整的故事情节

3. **proofreader** 进行质量审核
   - 检查错别字和语法
   - 验证叙事一致性
   - 提供修改建议
   - 输出：审核报告

4. 最终结果返回给用户，包含完整的小说内容和审核意见

## 自定义智能体团队

你可以通过扩展现有的 subagent 配置来创建更多专业智能体。

### 添加新的智能体类型

1. 在 `backend/packages/harness/deerflow/subagents/builtins/` 目录下创建新的 Python 文件
2. 定义 `SubagentConfig` 对象
3. 在 `__init__.py` 中注册

### 添加新的团队

在 `config.yaml` 的 `teams` 部分添加新的团队配置：

```yaml
teams:
  technical-writer-team:
    description: "技术文档写作团队"
    lead_agent: "content-writer"
    workflow: "sequential"
    members:
      - "researcher"
      - "content-writer"
      - "proofreader"
```

## 工作流程模式

### Sequential（顺序执行）
默认模式。智能体按顺序执行，每个智能体完成后再调用下一个。上一个智能体的输出会作为下一个智能体的上下文。

### Parallel（并行执行）
所有智能体同时开始执行，各自独立完成任务。适用于可以完全独立的子任务。

### Conditional（条件执行）
根据前一个智能体的输出结果，决定是否调用后续智能体。可以用于实现审查-修改循环等逻辑。

## 最佳实践

1. **明确任务边界**：在 prompt 中清晰地定义每个智能体应该做什么
2. **合理分工**：确保每个智能体有明确的职责范围
3. **适当检查点**：使用 proofreader 等审核智能体来确保质量
4. **迭代优化**：可以根据用户反馈多次调用团队进行修改

## 扩展阅读

- [Subagent 配置](./SUBAGENTS.md)
- [工具配置](./TOOLS.md)
- [技能系统](./SKILLS.md)
