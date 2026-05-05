# 提示词与 Tool 优化开发计划

## 背景

当前 `InboundAgent` 的 tools 设计存在职责耦合、状态管理混乱、死代码等问题。需要重构 tools 体系、统一状态管理、优化回访对话流。

## 改动概览

| # | 改动 | 涉及文件 |
|---|------|----------|
| 1 | 移除转接工具，添加辱骂挂断逻辑 | `tool/voice_tool.py`, `prompts/llm_prompy.py` |
| 2 | 去掉保存校验，直接落库后挂断 | `tool/voice_tool.py` |
| 3 | 强化 `update_visitor_info` 返回值，引导 LLM 追问 | `tool/voice_tool.py` |
| 4 | 用 CallState 替代 `self.collected` | `tool/voice_tool.py`, `agents/llm_agent.py` |
| 5 | 去掉 visitor_phone 采集，直接用 caller_number | `tool/voice_tool.py`, `prompts/llm_prompy.py`, `state/python_state.py` |
| 6 | tools 从 Agent 类方法迁回 `voice_tool.py` 工厂模式 | `tool/voice_tool.py`, `agents/llm_agent.py` |
| 7 | 回访客不追问，确认即保存 | `prompts/llm_prompy.py`, `agents/llm_agent.py` |

## 详细方案

### 改动 1：移除转接，添加辱骂挂断

**voice_tool.py**：
- 删除 `transfer_call` 工具
- 保留 `end_call` 工具（告别 + 挂断），辱骂场景也用它

**llm_prompy.py**：
- 在 SYSTEM_PROMPT 中增加辱骂处理规则：
  ```
  - 访客辱骂或恶意骚扰：礼貌说"感谢来电，再见"后调用 end_call 挂断，不要对骂或纠缠
  ```

### 改动 2：去掉保存校验，直接落库后挂断

**voice_tool.py**：
- 将 `confirm_and_save` 拆为 `save_visitor_record`：
  - 不校验必填字段，直接把 CallState 中已有字段写入 DB
  - 落库后调用微信推送（异步，不阻塞）
  - 生成告别语 → 等播完 → 挂断
  - 微信推送失败只记日志，不影响保存结果

### 改动 3：强化 update_visitor_info 返回值

**voice_tool.py**：
- 返回值改为结构化字符串，让 LLM 更难忽略：
  ```
  已采集: {字段列表}
  待采集: {缺失必填字段} ← 追问这些字段
  全部必填已齐 → 调用 save_visitor_record 保存
  ```
- 缺失字段时明确加一句 `"请立即追问待采集字段，不要调用 save_visitor_record"`

### 改动 4：用 CallState 替代 self.collected

**state/python_state.py**：
- 删除 `visitor_phone` 字段
- 确保 `license_plate`、`visiting_company`、`purpose`、`visitor_name` 字段与 tool 参数一致

**voice_tool.py**：
- `create_voice_tools()` 接收 `CallState` 实例（而非 visitor_context dict）
- `update_visitor_info` 写入 `CallState` 的对应字段
- `save_visitor_record` 从 `CallState` 读取所有字段构造 record_data

**agents/llm_agent.py**：
- `InboundAgent.__init__` 创建 `CallState` 实例（替代 `self.collected` 和 `self.visitor_context`）
- 传给 `create_voice_tools()` 使用
- Agent 构造时通过 `tools=` 参数传入工厂返回的 tool 列表

### 改动 5：去掉 visitor_phone 采集

**voice_tool.py**：
- `update_visitor_info` 删除 `visitor_phone` 参数
- `save_visitor_record` 构造 record_data 时，`visitor_phone` 直接填 `caller_number`

**prompts/llm_prompy.py**：
- 采集字段说明中删除"联系电话"

**state/python_state.py**：
- 删除 `visitor_phone` 字段

### 改动 6：tools 迁回 voice_tool.py

**tool/voice_tool.py**：
- 重写 `create_voice_tools(state: CallState)` 工厂，返回 3 个工具：
  1. `update_visitor_info` — 增量采集
  2. `save_visitor_record` — 落库 + 推送 + 挂断
  3. `end_call` — 告别 + 挂断（辱骂/异常/信息不齐超时）
- 删除旧的 `save_visitor_record`、`transfer_call`、`end_call` 定义

**agents/llm_agent.py**：
- 删除 `InboundAgent` 上的 `update_visitor_info`、`confirm_and_save`、`_hangup` 方法
- 在 `inbound_entrypoint` 中调用 `create_voice_tools(state)` 获取 tool 列表
- 创建 Agent 时传入 `tools=tools`

### 改动 7：回访客不追问，确认即保存

**prompts/llm_prompy.py**：
- 修改 SYSTEM_PROMPT 增加回访专用规则：
  ```
  ## 回访对话策略
  - 如果提供了回访信息，不要逐字段追问！
  - 开场直接确认："张师傅您好，今天还是来XX送货吗？"
  - 访客确认（"对"/"嗯"/"是"）→ 立即调用 update_visitor_info 填入回访字段 → save_visitor_record
  - 访客说有变化 → 只追问变化的部分
  ```

**agents/llm_agent.py**：
- 删除 L380-381 硬编码的 `session.say(greeting)` — 让 LLM 根据 prompt 自行开场
- 回访客的开场白由 LLM 根据 return_visit_summary 自主生成，不再硬编码

注意点：如果访客说不是的话，那请勿调用 save_visitor_record，并且请视为未访问过

### 顺带修复

- 删除 `on_tts_audio` 事件监听中调用的不存在的 `play_audio` 函数（L367）
- 删除 `InboundAgent.set_participant()` 方法（不再需要，participant 信息存入 CallState）

## 修改文件清单

| 文件 | 操作 |
|------|------|
| `tool/voice_tool.py` | 重写：工厂模式，3 个 tool，接收 CallState |
| `agents/llm_agent.py` | 删除类方法 tool，改用工厂；删除硬编码开场白；修复 play_audio |
| `prompts/llm_prompy.py` | 增加辱骂规则、回访策略、删除联系电话采集 |
| `state/python_state.py` | 删除 visitor_phone 字段 |

## 验证方式

1. `python agents/llm_agent.py dev` 启动 Worker 无报错
2. 检查日志确认 tools 正确注册（3 个：update_visitor_info、save_visitor_record、end_call）
3. 模拟新访客呼入 → LLM 逐字段采集 → 保存 → 挂断
4. 模拟回访客呼入 → LLM 确认式开场 → 快速保存
5. 模拟辱骂 → LLM 礼貌挂断
