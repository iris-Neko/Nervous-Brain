# Evaluation Datasets

这里存放固定评测样例，用于 prompt、模型路由、检索策略、数据源和格式化改动后的回答质量回归。

## 文件划分

```text
ai_generated_cases.jsonl      # 人工设计 / AI 辅助生成的基准样例
human_collected_cases.jsonl   # 真实内测或人工收集的 bad case
```

`ai_generated_cases.jsonl` 主要用于覆盖已知产品能力，例如多轮补参、资料推荐、开发指导和排障。

`human_collected_cases.jsonl` 用于后续沉淀真实坏例子，例如 CCC 教程、TS/JS CKB 转账、Spore DOB、Fiber、xUDT、Nervos Talk 社区讨论等。这个文件可以为空；收集到样例后按 JSONL 一行一个 case 追加。

## Case 格式

```json
{
  "case_id": "human-ccc-transfer-001",
  "category": "bad_case",
  "title": "TS/JS user asks for CCC CKB transfer tutorial",
  "conversation": [
    {"role": "user", "content": "我是 TS/JS 开发者，想用 CCC 写 CKB 转账小应用，给我最简教程。"}
  ],
  "expected_signals": {
    "must_use_tools": ["qdrant_search"],
    "should_not": ["all_todo_skeleton", "go_only_tutorial"]
  },
  "success_criteria": [
    "主动检索 CCC / TS/JS 相关资料",
    "优先给现成库和可运行路径",
    "核心 SDK 调用不能全部写成 TODO 占位"
  ],
  "notes": "从真实内测 bad case 整理，去除用户隐私、群聊原文和 token。"
}
```

## 安全边界

不要写入真实 token、私钥、API key、群聊原文、debug log、用户隐私或无法公开的客户信息。真实对话应做脱敏和概括，只保留评测需要的技术诉求、失败模式和验收标准。
