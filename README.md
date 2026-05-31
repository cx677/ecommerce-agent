# 电商全场景智能体 v2.1

四场景客服坐席助手：退货退款 / 售前咨询 / 订单追踪 / 投诉升级。

- **架构**：Neuro-Symbolic Agent（规则引擎做骨架，LLM 做话术润色）
- **MCP**：支持 MCP 协议，可被 Claude Desktop / Hermes Agent 调用
- **ABL**：规则判"不退" + 用户情绪激烈 → 自动记录冲突 → 人工标注面板裁决

## 项目结构

```
agents/          Agent 编排 + 路由 + 话术生成 + JWT 认证
engine/          规则引擎 + 状态机 + 语义匹配 + ABL 冲突 + 内存存储
rules/           退货规则 YAML
frontend/        坐席工作台（含登录 + 冲突裁决面板）
data/            Mock 数据 + 订单模板
tests/           回归测试
tools/           MCP 工具适配层

server.py        FastAPI HTTP 服务
mcp_server.py    MCP 协议服务（stdio / HTTP）
main.py          CLI 入口
gunicorn_conf.py 生产部署配置
```

## 启动

```bash
# 开发
pip install -r requirements.txt
python server.py

# 生产
pip install gunicorn
gunicorn server:app -c gunicorn_conf.py

# MCP 模式（供外部 Agent 调用）
python mcp_server.py

# CLI 演示
python main.py demo
```

## 默认账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| CSR001 | csr001 | 坐席 |
| admin | admin123 | 管理员 |

## 配置

所有 Key 从环境变量读取，不写死在代码中：
- `VOLCENGINE_API_KEY` — 火山引擎 API Key
- `RAGFLOW_API_KEY` — RAGFlow API Key
- `ARK_API_KEY` — 备用 LLM Key
