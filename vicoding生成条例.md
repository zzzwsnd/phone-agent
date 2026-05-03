# 工业园区访客登记系统 — 任务拆分清单

> 改造目标：牙科诊所外呼确认 → 工业园区访客呼入登记
> 基于决策：LiveKit SIP 呼入 + Self-hosted Agent + 纯 LiveKit AgentSession 驱动（无 LangChain/LangGraph）+ 号码预注入回访 + 双模式保留外呼

---

## Phase 1: 数据层

### 1.1 重设计 CallState TypedDict

**文件**: `state/python_state.py`

**改动内容**:
- 移除牙科字段: `phone_number`, `transfer_to`, `customer_name`, `appointment_time`, `available_times`, `confirmed_date`, `confirmed_time`
- 移除 LangGraph 路由字段: `next_action`（不再需要状态图路由）
- 新增访客登记字段:
  - `caller_number: str` — 呼入主叫号码（从 SIP participant attributes 提取）
  - `license_plate: Optional[str]` — 车牌号，如"沪A12345"
  - `visiting_company: Optional[str]` — 来访单位
  - `visitor_phone: Optional[str]` — 访客联系电话
  - `purpose: Optional[str]` — 来访事由
  - `visitor_name: Optional[str]` — 访客姓名
- 新增回访字段:
  - `is_return_visit: bool` — 是否回访
  - `return_visit_summary: Optional[str]` — 预注入的回访摘要
- 新增业务字段:
  - `visitor_record_id: Optional[int]` — 保存后的 DB 记录 ID
  - `call_room_name: Optional[str]` — LiveKit room 名
- 保留字段: `call_status`, `messages`, `turn_count`, `conversation_summary`, `error`
- 更新 `call_status` 取值: `inbound_ringing / connected / saving / ended / transferred`

**测试用例**:
1. 用所有新字段实例化 `CallState`，验证 TypedDict 接受
2. 仅用必填字段 `caller_number` 实例化，验证 Optional 字段可默认 None
3. 验证 `messages` 字段的 `operator.add` 累加行为：合并两条消息列表后长度正确

---

### 1.2 定义 visitor_records 表 DDL

**文件**: 新建 `infra/schema.py`

**改动内容**:
- 定义 `visitor_records` 表 DDL 字符串（含 `IF NOT EXISTS` 保证幂等）
- 表结构:
  ```
  visitor_records
    id               INT AUTO_INCREMENT PRIMARY KEY
    caller_number    VARCHAR(20) NOT NULL
    license_plate    VARCHAR(20)
    visiting_company VARCHAR(100)
    visitor_phone    VARCHAR(20)
    purpose          VARCHAR(50)
    visitor_name     VARCHAR(50)
    call_room_name   VARCHAR(100)
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
  INDEX idx_caller_number (caller_number)
  INDEX idx_created_at (created_at)
  ```
- 提供 `create_tables()` 函数，调用 `infra/mysql.py` 的 `execute()` 执行 DDL

**测试用例**:
1. 对测试 MySQL 实例调用 `create_tables()`，验证表存在且列名正确
2. 再次调用 `create_tables()`，验证无报错（幂等）
3. 执行 `SHOW CREATE TABLE visitor_records`，验证索引存在

---

### 1.3 实现访客 DB 操作

**文件**: 新建 `infra/visitor_db.py`

**改动内容**:
- 依赖 `infra/mysql.py` 的 `query()` 和 `execute()`
- 实现三个函数:
  1. `save_visitor_record(caller_number, license_plate, visiting_company, visitor_phone, purpose, visitor_name, call_room_name) -> int`
     - INSERT 到 `visitor_records`，返回插入行 ID
     - 使用参数化查询防 SQL 注入
  2. `lookup_visitor_by_phone(phone: str) -> list[dict]`
     - SELECT WHERE `caller_number = phone`，ORDER BY `created_at DESC`，LIMIT 5
     - 空结果返回空列表
  3. `list_visitors(limit: int = 50, offset: int = 0) -> list[dict]`
     - SELECT ORDER BY `created_at DESC`，分页查询
- 辅助函数 `format_return_visit_summary(records: list[dict]) -> str`
  - 将回访记录格式化为简短中文摘要，如"该号码曾于2025-04-13来访蓝色鲸鱼科技送货，姓名张师傅"
  - 空列表返回空字符串

**测试用例**:
1. 调用 `save_visitor_record` 保存一条记录，验证返回值为正整数 ID
2. 用 `lookup_visitor_by_phone` 查询该号码，验证返回列表包含刚保存的记录且字段匹配
3. 查询不存在的号码，验证返回空列表
4. 调用 `format_return_visit_summary` 传入有记录的列表，验证输出为非空中文字符串
5. 调用 `format_return_visit_summary` 传入空列表，验证返回空字符串
6. 调用 `list_visitors(limit=10)`，验证返回列表长度 ≤ 10

---

## Phase 2: 提示词与工具

### 2.1 重写系统提示词

**文件**: `prompts/llm_prompy.py`

**改动内容**:
- **SYSTEM_PROMPT**: "微笑齿科" → "XX工业园区门卫AI助手"
  - 角色: 自然简洁的门卫，不是机器人问答
  - 采集目标: 车牌号、来访单位、联系电话、来访事由、访客姓名
  - 约束: 3 轮内完成（约 15 秒），25 秒总时限含保存
  - 对话风格: 像真人门卫一次问多个问题（"车牌号多少，找哪家公司，什么事儿？"）
  - 回访处理: 如 `{return_visit_summary}` 非空，确认而非从头采集
  - 信息采集完毕后调用 `save_visitor_record` 工具
  - 用户要求人工时调用 `transfer_call`
  - 完成后调用 `end_call`
  - 不向用户透露工具名称
  - 占位符: `{caller_number}`, `{return_visit_summary}`
- **GREET_INSTRUCTION**: 开场白指令（注入到 Agent instructions）
  - 回访摘要为空: 简洁提问 "您好，请问车牌号多少，今天找哪家公司，什么事儿？"
  - 回访摘要非空: 回访确认 "张师傅您好，今天是不是和上次一样来{公司}送{货}？"
- **END_INSTRUCTION**: "好的，已通知门卫，请稍等放行。再见！"
- **TRANSFER_INSTRUCTION**: "告知访客即将转接给保安，请稍候。"
- 移除 VOICEMAIL_INSTRUCTION、CHAT_INSTRUCTION、GREET_INSTRUCTION 中的 LangGraph 节点逻辑
- 移除所有关键词路由逻辑（不再有 chat_node 的意图判断，由 LLM 自主决策）

**测试用例**:
1. 用 `caller_number="138xxxx1234"`, `return_visit_summary="该号码曾于2025-04-13来访蓝色鲸鱼科技送货"` 格式化 SYSTEM_PROMPT，验证包含"工业园区"，无"齿科"，包含回访摘要
2. 用空 `return_visit_summary` 格式化，验证无回访相关内容
3. 验证 GREET_INSTRUCTION 包含 3 轮约束说明
4. 验证无 LangGraph/节点/路由相关描述

---

### 2.2 重设计 LiveKit function_tool 工具

**文件**: `tool/voice_tool.py`

**改动内容**:
- 完全重写，移除旧的外呼工具集（look_up_availability, confirm_appointment, detected_answering_machine）
- 移除 LangChain `@tool` 的 `tool/llm_tool.py` 引用（该文件不再使用）
- 新工具集（全部为 LiveKit `@function_tool`）:
  1. `save_visitor_record(caller_number, license_plate, visiting_company, visitor_phone, purpose, visitor_name)`
     - 调用 `infra/visitor_db.save_visitor_record` 写入 MySQL
     - 调用 `infra/wechat_push.push_visitor_to_security` 推送微信占位
     - 返回成功消息含 record_id
  2. `transfer_call()` — 转接人工保安
  3. `end_call()` — 结束通话
  4. `hangup()` — 挂断（底层 SIP 操作）
- 重命名 `create_voice_tools(dial_info, participant)` → `create_voice_tools(visitor_context, participant)`
  - `visitor_context: dict` 含 `caller_number`, `transfer_to`, `call_room_name`
- 返回工具列表: `[save_visitor_record, transfer_call, end_call]`

**测试用例**:
1. 调用 `create_voice_tools(visitor_context={"caller_number": "138xxxx1234", "transfer_to": "010-12345678", "call_room_name": "room_test"}, participant=mock)`，验证返回 3 个 function_tool 对象
2. 验证 `save_visitor_record` 工具 schema 有 6 个字符串参数
3. 验证 `transfer_call` 工具内部引用 `visitor_context["transfer_to"]`

---

### 2.3 删除 LangChain 工具文件

**文件**: 删除 `tool/llm_tool.py`

**改动内容**:
- 删除 `tool/llm_tool.py`（LangChain `@tool` 工具，不再使用）
- 删除 `agents/llm_agent.py` 中对该文件的所有 import 和引用
- 对话逻辑和工具调用完全由 LiveKit AgentSession + `@function_tool` 驱动

**测试用例**:
1. 验证 `tool/llm_tool.py` 文件不存在
2. 验证 `agents/llm_agent.py` 中无 `from tool.llm_tool` import
3. 验证 Agent 仍可正常实例化（无缺失依赖）

---

### 2.4 创建微信推送占位

**文件**: 新建 `infra/wechat_push.py`

**改动内容**:
- 异步函数 `push_visitor_to_security(record: dict) -> bool`
  - 记录日志: "WeChat push placeholder: would send visitor info to security guard" + 记录详情
  - 返回 True（占位）
  - 包含 TODO 注释说明未来实现
  - async 以匹配未来真实 HTTP 调用
- 辅助函数 `format_wechat_message(record: dict) -> str`
  - 格式化为微信消息:
    ```
    【访客登记通知】
    车牌：沪A12345
    来访单位：蓝色鲸鱼科技
    访客姓名：张师傅
    联系电话：138xxxx1234
    来访事由：送货
    时间：2025-04-13 14:30
    ```

**测试用例**:
1. 调用 `push_visitor_to_security` 传入样例记录，验证返回 True 且日志含 "placeholder"
2. 调用 `format_wechat_message` 传入含全部字段的记录，验证输出包含所有字段值

---

## Phase 3: Agent 逻辑（纯 LiveKit，无 LangGraph）

### 3.1 创建 InboundAgent 类，删除 LangGraph 代码

**文件**: `agents/llm_agent.py`

**改动内容**:
- **删除**所有 LangGraph 相关代码:
  - 删除 `greet_node`, `chat_node`, `route_after_chat`, `build_call_graph` 函数
  - 删除 LangGraph 的 `StateGraph`, `END` 等 import
  - 删除 `langchain-openai`, `langgraph` 相关 import
- **删除** `OutboundCaller` 类（外呼代码暂不保留到 Agent 类中，如需外呼可在 Phase 5 恢复）
- **新建** `InboundAgent(Agent)` 类:
  - 构造函数: `__init__(self, *, caller_number: str, return_visit_summary: str = "", transfer_to: str = "")`
    - 用 SYSTEM_PROMPT + GREET_INSTRUCTION 生成 instructions，填充 `caller_number` 和 `return_visit_summary`
    - 初始化 `self.visitor_context: dict` 含 `caller_number`, `transfer_to`, `call_room_name`
  - Function tools（直接定义在类上）:
    1. `save_visitor_record(self, ctx, caller_number, license_plate, visiting_company, visitor_phone, purpose, visitor_name)` — 写 DB + 推微信
    2. `end_call(self, ctx)` — 结束通话
    3. `transfer_call(self, ctx)` — 转接，号码从 `self.visitor_context["transfer_to"]` 读取
  - 保留 `hangup()` 辅助方法
- **对话逻辑**: 无状态图，AgentSession 驱动 STT→LLM→TTS 循环，LLM 根据 system prompt 自主决定何时调用哪个 tool

**测试用例**:
1. 实例化 `InboundAgent(caller_number="138xxxx1234")`，验证 `agent.instructions` 含"工业园区"，无"齿科"
2. 实例化时传入非空 `return_visit_summary`，验证摘要文本出现在 instructions 中
3. 验证 Agent 类有 `save_visitor_record`, `end_call`, `transfer_call` 三个 function_tool 方法
4. 验证文件中无 `StateGraph`, `build_call_graph`, `greet_node`, `chat_node` 等残留

---

### 3.2 创建呼入 entrypoint

**文件**: `agents/llm_agent.py`

**改动内容**:
- 新增 `inbound_entrypoint(ctx: JobContext)`:
  1. `await ctx.connect()` 连接房间（房间由 SIP trunk 自动创建）
  2. `participant = await ctx.wait_for_participant()` 等待来电方加入
  3. 从 SIP participant attributes 提取主叫号码（调用 `extract_caller_number`）
  4. 查询 DB 回访信息: `lookup_visitor_by_phone` → `format_return_visit_summary`
  5. 创建 `InboundAgent(caller_number=..., return_visit_summary=..., transfer_to=...)`
  6. 配置 `AgentSession`（STT/LLM/TTS 配置）
  7. `await session.start(agent=agent, room=ctx.room)` 启动对话
  8. **无 `dial_sip` 调用** — 来电方已在房间中

**测试用例**:
1. Mock `JobContext`（含 room 和带 `sip.caller_number` 属性的 participant），调用 `inbound_entrypoint`，验证:
   - Agent 以正确 `caller_number` 创建
   - `lookup_visitor_by_phone` 被调用
   - 若有历史记录，agent instructions 含回访信息
   - `session.start` 被调用
   - 无 `create_sip_participant` 调用

---

### 3.3 提取主叫号码工具函数

**文件**: `agents/llm_agent.py`

**改动内容**:
- `extract_caller_number(participant: rtc.RemoteParticipant) -> str`
  - 优先读 `participant.attributes.get("sip.caller_number", "")`
  - 空则回退解析 `participant.identity`（LiveKit SIP identity 格式常为 `sip_<number>`）
  - 去除 "sip_" 前缀
  - 返回清洗后的号码字符串

**测试用例**:
1. Mock participant `attributes={"sip.caller_number": "138xxxx1234"}`，验证返回 "138xxxx1234"
2. Mock participant `attributes={}`, `identity="sip_138xxxx1234"`，验证返回 "138xxxx1234"
3. Mock participant 同时有 attributes 和 identity，验证 attributes 优先

---

### 3.4 更新 Worker 启动逻辑

**文件**: `agents/llm_agent.py`

**改动内容**:
- 更新 `__main__` 块:
  ```python
  if __name__ == "__main__":
      cli.run_app(
          WorkerOptions(
              entrypoint_fnc=inbound_entrypoint,
              agent_name="park-visitor-agent",
          )
      )
  ```
- 删除旧的外呼 Worker 配置（`outbound-caller` agent_name）
- 删除 LangGraph 相关的 `fast_llm` 引用

**测试用例**:
1. 运行 `python agents/llm_agent.py dev`，验证 Worker 以 `inbound_entrypoint` 启动
2. 验证 agent_name 为 "park-visitor-agent"
3. 验证无 LangGraph/LangChain 相关 import 报错

---

## Phase 4: API 与配置

### 4.1 重写 API 端点

**文件**: `api/pthon_api.py`

**改动内容**:
- 移除或标记废弃 `POST /call` 外呼端点（双模式待定，先保留但标注为 outbound-only）
- 新增 `GET /visitors` — 列出访客记录
  - Query params: `limit: int = 50`, `offset: int = 0`
  - 调用 `infra/visitor_db.list_visitors(limit, offset)`
- 新增 `GET /visitors/{phone}` — 按号码查询回访
  - 调用 `infra/visitor_db.lookup_visitor_by_phone(phone)`
- 新增 Pydantic 模型: `VisitorRecord`, `VisitorListResponse`, `VisitorLookupResponse`
- 更新 FastAPI app title 为"工业园区访客登记 API"

**测试用例**:
1. `GET /visitors` 返回 200 和列表
2. `GET /visitors/138xxxx1234` 无记录时返回 `is_return_visit: false`
3. 保存记录后 `GET /visitors/138xxxx1234` 返回 `is_return_visit: true` 且含该记录

---

### 4.2 新增呼入 SIP 配置

**文件**: `config/livekit_config.py`

**改动内容**:
- 新增环境变量:
  ```python
  SIP_INBOUND_TRUNK_ID = os.getenv("SIP_INBOUND_TRUNK_ID", "")
  SECURITY_TRANSFER_NUMBER = os.getenv("SECURITY_TRANSFER_NUMBER", "")
  INBOUND_AGENT_NAME = os.getenv("INBOUND_AGENT_NAME", "park-visitor-agent")
  ```
- 保留 `SIP_OUTBOUND_TRUNK_ID`（双模式待定）

**测试用例**:
1. 设置 `SIP_INBOUND_TRUNK_ID=trunk_xxx`，验证读取正确
2. 未设置时验证默认值为空字符串
3. 验证 `SIP_OUTBOUND_TRUNK_ID` 未受影响

---

### 4.3 清理依赖

**文件**: `requirements.txt`, `pyproject.toml`

**改动内容**:
- **移除**: `langchain-openai`, `langchain-core`, `langgraph`（不再使用）
- **新增**: `mysql-connector-python~=9.0`（`infra/mysql.py` 依赖）
- 保留: `livekit`, `livekit-agents`, `fastapi`, `uvicorn` 等核心依赖

**测试用例**:
1. 在干净 venv 中 `pip install -r requirements.txt`，验证无冲突
2. `python -c "from infra.mysql import query, execute; print('OK')"` 验证导入成功
3. 验证 `import langgraph` 报 ImportError（确认已移除）

---

## Phase 5: 集成与文档

### 5.1 串联完整呼入流程

**文件**: `agents/llm_agent.py`, `main.py`

**改动内容**:
- `main.py` 启动时调用 `infra.schema.create_tables()` 确保 DB schema 存在
- 验证完整调用链: SIP 呼入 → Room 创建 → Agent dispatch → 提取主叫号码 → 查 DB 回访 → 创建 InboundAgent → AgentSession 启动 → LLM 对话 → function_tool 保存/转接/挂断

**测试用例**:
1. 启动 API 服务，验证 `create_tables()` 执行且 `visitor_records` 表存在
2. 端到端手动测试: 拨打呼入号码，验证 AI 应答并采集信息，挂断后 DB 有记录

---

### 5.2 更新文档

**文件**: `CLAUDE.md`

**改动内容**:
- 项目描述: "牙科诊所外呼确认" → "工业园区访客呼入登记"
- 架构描述: 移除 LangGraph/LangChain 相关内容，强调纯 LiveKit AgentSession 驱动
- 更新核心文件列表: 移除 `tool/llm_tool.py`，新增 `infra/visitor_db.py`, `infra/wechat_push.py`, `infra/schema.py`
- 更新对话流: prompt 驱动 → LLM 自主决策 → function_tool 执行（无状态图路由）
- 新增环境变量: `SIP_INBOUND_TRUNK_ID`, `SECURITY_TRANSFER_NUMBER`, `AGENT_MODE`, MySQL 变量
- 移除"双层工具系统"章节（只剩一层 LiveKit function_tool）
- 移除"LangGraph 对话流"章节

**测试用例**:
1. 验证 CLAUDE.md 中所有文件路径准确
2. 验证无"齿科"、无"LangGraph"、无"LangChain"残留
3. 验证环境变量列表完整