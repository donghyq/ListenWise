---
name: lawflow
description: Use a local LawFlow app to turn legal materials and selected public news into reviewable legal explainers, training notes, or audio scripts. Use when the user asks an Agent to operate an existing LawFlow project; do not use for general legal advice without a LawFlow project.
---

# LawFlow 法律内容工作流

通过 LawFlow 本地服务处理已导入的法律材料，或将成果回写至工作台继续审阅、生成音频和导出。默认服务地址为 `http://127.0.0.1:8080`。

开始前调用 `GET /api/skill/status`。服务不可用时，请用户先启动 LawFlow App；不要自行启动长期服务。

## 选择运行方式

- 用户已在 App 中选择 OpenAI、DeepSeek 或企业网关，并明确允许发送所选素材时，使用 App API 模式。
- 用户要求直接使用当前 Agent 的模型，或不希望额外配置 API Key 时，使用宿主模型模式。

## App API 模式

1. 读取 `GET /api/skill/projects/{project_id}/context`，确认项目偏好、可用画像与材料块。
2. 对外培训、播客和 Speak Note 项目：先检查核验任务。未完成或未关闭的任务不能绕过。
3. 调用 `POST /api/projects/{project_id}/narrative-outlines` 生成大纲。除非用户明确要求跳过，否则先展示大纲并等待确认。
4. 调用 `PUT /api/narrative-outlines/{outline_id}/confirm`，再调用 `POST /api/narrative-outlines/{outline_id}/contents` 生成讲稿。
5. 成稿由用户确认后，调用 `POST /api/projects/{project_id}/audio-scripts` 生成口播脚本；最终 MP3 只能基于已确认讲稿生成。若用户希望按章节碎片时间收听，在同一接口传入 `section_heading`（讲稿中的二级标题），只整理该章节。
6. 通过 `POST /api/narrative-contents/{content_id}/export` 导出 Markdown 与 DOCX。
7. 如果用户指出某一章不合适，先从项目详情的 `narrative_contents[].chapters` 读取稳定章节 `id`、正文和 `version`。调用 `POST /api/narrative-contents/{content_id}/sections/{section_id}/revision`，传入用户的 `instructions` 与 `base_version`，取得候选稿。展示修改前后内容，用户同意采用后再调用 `PUT /api/narrative-contents/{content_id}/sections/{section_id}`，传入候选稿 `body`、`base_version` 和 `instructions`。正文修改后需要重新确认讲稿，改动章节的音频也需要更新。同路径的 `/export` 可单独导出该节。
8. 写作画像可通过 `GET /api/narrative/profiles/{profile_id}/export` 导出 Markdown，也可通过 `POST /api/narrative/profiles/import` 导入为新的自定义画像。

## 宿主模型模式

1. 调用 `GET /api/skill/projects/{project_id}/context` 读取受控材料块。
2. 仅使用返回材料支持的事实、规则、日期和观点。不得加入材料外法规、案例、数字、机构观点或个案结论。
3. 先给出详细大纲；用户确认后撰写 Markdown 成稿。
4. 调用 `POST /api/projects/{project_id}/skill-host-contents` 回写成稿。请求必须包含实际引用的 `source_block_ids`。
5. 回写后由用户在 App 中审阅、确认和导出。

## 写作要求

- 定位为知识转译或专业表达，不是正式法律意见。
- 从材料中已有的问题、变化或业务情境切入。
- 按背景或问题、规则或事实、重要性、实务含义组织内容。
- 解释术语应给出必要背景和具体场景，不堆砌法条。
- 不使用“核心提示”“对企业的影响”“建议动作”等泛化模板标题。
- 不使用“作为 AI”“根据材料显示”等元话语。
- 根据场景保留关键观点、专家/机构、数据、案例、时间和规则适用边界；日常速听可以压缩，但培训和对外播客不得把这些内容概括成无依据的泛泛判断。

## 每日速听

用户要求订阅资讯时，使用 App 的每日速听功能：

- `POST /api/daily-brief-subscriptions` 创建公开 RSS 或 Atom 订阅；
- `POST /api/daily-brief-subscriptions/{subscription_id}/run` 可立即拉取并创建待审速听任务；
- 自动生成待审稿与音频脚本只能在 App 已配置模型且用户明确启用后进行；不会自动发布或生成最终 MP3。

删除 LawFlow 项目会删除项目中的材料和内部记录，不会删除已导出的成果包。执行删除前必须要求用户明确确认项目名称或 ID。
