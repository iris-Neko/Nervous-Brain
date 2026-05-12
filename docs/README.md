# Nervos Brain 工程文档

这组文档面向交付和部署方，目标是让新机器 clone 仓库后能快速完成部署、数据重建、Bot 启动、日常运维和基础验收。

## 推荐阅读顺序

1. [新服务器部署](deployment.md): 从 fresh clone 到 Qdrant 和 Bot 启动。
2. [配置说明](configuration.md): `config.yaml.example` 各区块含义和必填项。
3. [检索数据与 Qdrant 重建](retrieval-data.md): 三库数据、Git LFS、archive DB 和 Docker Qdrant 的关系。
4. [运行与运维](runtime-operations.md): Telegram/Discord/Qdrant/Talk 增量更新的日常操作。
5. [测试与验收](testing-and-acceptance.md): 部署后该跑哪些检查。
6. [故障排查](troubleshooting.md): 常见部署和运行问题。

## MCP 相关

- [MCP 服务](mcp.md): Telegram MCP 和 Nervos Talk MCP 的定位、启动方式和环境变量。

## 安全边界

公开文档只记录通用流程和占位符，不记录真实密钥、Bot token、群聊原文、debug events、feedback、memory DB 或当前测试服务器的私密路径。
