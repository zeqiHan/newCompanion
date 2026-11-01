# newCompanion — 云端大模型接入操作指南

基于 JudgmentOS（AI Builders Space）同一套接入方式整理。  
目标：用最少改动，让 newCompanion 能调用云端 LLM。

---

## 1. 你要接什么

| 项 | 值 |
| --- | --- |
| 协议 | OpenAI Compatible Chat Completions |
| Base URL | `https://space.ai-builders.com/backend/v1` |
| 默认模型 | `grok-4-fast` |
| 鉴权 | Bearer Token（`Authorization: Bearer <key>`） |
| 密钥来源 | 部署时用平台注入的 `AI_BUILDER_TOKEN`；本地用 `LLM_API_KEY` |

Chat 接口等价于：

```
POST {LLM_BASE_URL}/chat/completions
```

---

## 2. 环境变量（照抄即可）

在 newCompanion 的 `.env` / `.env.local` / 部署面板里配置：

```bash
# 必填：密钥（二选一）
LLM_API_KEY=你的key
# 或（AI Builders 部署时平台会自动注入，不要手写进 env_vars）
AI_BUILDER_TOKEN=

# 推荐显式写清
LLM_PROVIDER=openai-compatible
LLM_BASE_URL=https://space.ai-builders.com/backend/v1
LLM_MODEL=grok-4-fast
```

### 本地 vs 云端

| 环境 | 密钥怎么拿 |
| --- | --- |
| 本地开发 | 自己填 `LLM_API_KEY` 到 `.env.local` |
| AI Builders 部署 | **不要**把 `AI_BUILDER_TOKEN` 写进部署 env；平台自动注入 |
| 其他云主机 | 把有效的 `LLM_API_KEY` 配进环境变量 |

---

## 3. 最小可用代码（Node / TypeScript）

### 安装

```bash
npm install openai
```

### 封装一层（推荐，别在业务里硬编码）

```ts
// lib/llm.ts
import OpenAI from "openai";

export function getLlmClient() {
  const apiKey =
    process.env.LLM_API_KEY?.trim() ||
    process.env.AI_BUILDER_TOKEN?.trim() ||
    "";

  if (!apiKey) {
    throw new Error(
      "未配置 LLM 密钥。本地设 LLM_API_KEY；AI Builders 部署用平台注入的 AI_BUILDER_TOKEN。",
    );
  }

  const baseURL =
    process.env.LLM_BASE_URL?.trim() ||
    "https://space.ai-builders.com/backend/v1";

  const model = process.env.LLM_MODEL?.trim() || "grok-4-fast";

  const client = new OpenAI({ apiKey, baseURL });
  return { client, model };
}

export async function chat(messages: { role: "system" | "user" | "assistant"; content: string }[]) {
  const { client, model } = getLlmClient();

  const completion = await client.chat.completions.create({
    model,
    messages,
    temperature: 0.2,
  });

  const content = completion.choices[0]?.message?.content;
  if (!content) throw new Error("模型返回空响应");
  return content;
}
```

### 业务里调用

```ts
import { chat } from "./lib/llm";

const reply = await chat([
  { role: "system", content: "你是 newCompanion 的助手。" },
  { role: "user", content: "你好，介绍一下你自己。" },
]);

console.log(reply);
```

### 需要 JSON 输出时

```ts
const completion = await client.chat.completions.create({
  model,
  messages,
  response_format: { type: "json_object" },
});
```

---

## 4. 纯 HTTP 调用（任意语言）

```bash
curl https://space.ai-builders.com/backend/v1/chat/completions \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "grok-4-fast",
    "messages": [
      {"role": "user", "content": "ping"}
    ]
  }'
```

Python 示例：

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["LLM_API_KEY"],  # 或 AI_BUILDER_TOKEN
    base_url="https://space.ai-builders.com/backend/v1",
)

r = client.chat.completions.create(
    model="grok-4-fast",
    messages=[{"role": "user", "content": "你好"}],
)
print(r.choices[0].message.content)
```

---

## 5. 操作步骤（按顺序做）

### Step A — 本地先打通

1. 在 newCompanion 根目录创建 `.env.local`
2. 填入 `LLM_API_KEY`（以及上面的 `LLM_BASE_URL` / `LLM_MODEL`）
3. 接入第 3 节的 `getLlmClient()` / `chat()`
4. 跑一个最小接口或脚本，确认有返回

### Step B — 接进产品逻辑

1. 所有模型调用只走 `lib/llm`（或你们项目的统一入口）
2. **不要**在业务代码里写死 `apiKey` / `baseURL`
3. System prompt、温度、是否 JSON，按功能分别配置

### Step C — 部署到 AI Builders（若用同一平台）

1. 推代码到部署分支
2. 部署参数里只配业务需要的变量（如数据库等）
3. **不要**手动配置 `AI_BUILDER_TOKEN`（平台注入）
4. 可选：显式设 `LLM_MODEL=grok-4-fast`
5. 部署后打一次健康检查接口，确认 chat 通

### Step D — 验收

- [ ] 本地 `chat("ping")` 有正常文本返回
- [ ] 部署环境不报「未配置模型密钥」
- [ ] 换 `LLM_MODEL` 后仍可调用（说明没写死模型）
- [ ] 密钥未进 git（检查 `.gitignore` 含 `.env.local`）

---

## 6. 常见坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `未配置模型密钥` | 没设 `LLM_API_KEY` / `AI_BUILDER_TOKEN` | 本地补 `.env.local`；部署确认平台注入 |
| 401 Unauthorized | key 错或过期 | 换新 key / 确认用的是平台 token |
| 404 / 连接失败 | `baseURL` 写错 | 必须是 `.../backend/v1`，不要多加 `/chat` |
| 空响应 | 模型拒答或超时 | 看 status / 重试；检查 messages 是否合法 |
| 本地有 key、部署挂 | 把 token 写进了错误变量名 | 部署读 `AI_BUILDER_TOKEN`，本地读 `LLM_API_KEY` |
| 直接用 `OPENAI_API_KEY` | 和这套平台约定不一致 | 统一改成 `LLM_API_KEY` 或 `AI_BUILDER_TOKEN` |

---

## 7. 安全注意事项

1. **永远不要**把 key commit 进仓库  
2. `.gitignore` 至少包含：`.env`、`.env.local`、`.env.*.local`  
3. 前端浏览器代码里 **不要**放密钥；只在服务端调用  
4. 需要给前端用时，走你们自己的后端 API 代理

---

## 8. 和 JudgmentOS 的对应关系（方便对照）

JudgmentOS 里已经跑通的文件：

- `judgment-os/src/lib/llm/index.ts` — 工厂 / 读环境变量  
- `judgment-os/src/lib/llm/openai-compatible.ts` — OpenAI SDK 封装  
- `judgment-os/.env.example` — 环境变量模板  
- `DEPLOY.md` — AI Builders 部署说明  

newCompanion 建议直接复用同一套约定：

```
LLM_API_KEY / AI_BUILDER_TOKEN
LLM_BASE_URL
LLM_MODEL
```

这样两个项目可以共用同一把云端钥匙和同一套运维习惯。

---

## 9. 30 秒自检命令

```bash
# 把 YOUR_KEY 换成真实 key
export LLM_API_KEY=YOUR_KEY

curl -sS https://space.ai-builders.com/backend/v1/chat/completions \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"grok-4-fast","messages":[{"role":"user","content":"只回复：ok"}]}'
```

看到返回 JSON 里有 `"content":"ok"`（或类似）即打通。

---

## 10. 最小 checklist（给实施的人）

1. [ ] 拿到可用的 `LLM_API_KEY`（或确认部署有 `AI_BUILDER_TOKEN`）  
2. [ ] 配 `LLM_BASE_URL` + `LLM_MODEL`  
3. [ ] 封装统一 `getLlmClient()` / `chat()`  
4. [ ] 业务只调用封装层  
5. [ ] 本地 curl / 脚本验证  
6. [ ] 部署后验证  
7. [ ] 确认密钥未进 git  

做完这 7 步，newCompanion 就算正式接入云端大模型了。
