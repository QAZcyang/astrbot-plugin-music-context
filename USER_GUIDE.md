# Music Context 使用与开发指南

Music Context v1.0.2 支持网易云单曲链接、App 短链及已知 JSON/XML 音乐卡片。QQ Music 与其他音乐数据源只有扩展接口，尚未实现。

## 宿主与消息边界

声明 AstrBot `>=4.24.0,<5`，验证环境为 AstrBot 4.28.2 / Python 3.12；完整最低版本矩阵尚未完成。插件无额外运行依赖或前端构建步骤。

QQ/OneBot 是输入通道，QQ Music 是音乐数据源。受支持的纯卡片保留 JSON 原文，并将已验证的导航链接投影为事件文本；是否触发 LLM 仍取决于宿主唤醒、会话与其他插件策略。真实微信/XML 最终交付未完成验收。

音乐处理不会调用 `stop_event()`；歌曲检索失败跳过增强，歌词失败保留可用歌曲信息，普通聊天继续。短链和网络超时无法强行终止已经启动的工作线程。

## 配置完整说明


AstrBot 原生插件配置与 Dashboard「配置与处理器」共用同一个配置文件。原生入口保存后按宿主流程重载；Dashboard 保存立即对新请求生效，执行中的请求可以按原设置完成。全部字段以 [`_conf_schema.json`](_conf_schema.json) 为准。

| 分组 | 主要配置与新安装默认值 | 用途 |
| --- | --- | --- |
| Provider | 自动发现/注册 `netease` | 当前没有音乐 Provider 切换选项；新增数据源按下面的扩展契约接入 |
| Card parser | `enable_card_parser=true` | 主动解析 JSON/XML 音乐卡片；关闭时跳过卡片，文本音乐链接仍正常处理；旧配置缺失此字段时默认开启 |
| Cache | `enable_cache=true`；`cache_ttl=604800`；`max_cache_items=256` | 默认七天，每类分别限制；TTL 影响新写入，关闭处理缓存仍可检查已有缓存 |
| Lyrics preview | `show_full_lyrics_preview=false` | 默认最多 400 字符；开启展示全部清洗歌词，仅影响 UI |
| Context | `enable_context_injection=true`；`max_context_length=8000` | 包含提示前缀的字符预算，范围 512–32000，非精确 token 数 |
| Processor | `enable_context_processor=false`；`processor_model=""` | 从下拉框选 AstrBot 已配置的文本模型，不手填模型名称、密钥或 API 地址 |
| Processor parameters | `context_processor.temperature=0.2`；`context_processor.max_tokens=1000`；`context_processor.timeout=30` | 随机性 0–2；辅助输出上限 128–4096 tokens；总等待 1–60 秒，超时回退；不修改主对话生成参数 |
| History | `history_enable=true`；`max_history_items=50` | Context 与回复分别最多 50 条，写入插件数据目录的 `history.json` |
| Reply history | `store_llm_history=true`；`llm_history_limit=50` | 回复捕获须两个历史开关均开启；回复上限取两个数量配置的较小值 |
| Debug | `debug_mode=false` | 原始技术快照仅保留在本次运行内存最近 20 次注入，关闭时清除 |

Processor 未选择模型、调用失败、超时或结构不合格时保留事实 Context。六个辅助字段是歌曲文本推断，歌手信息也不是已核实知识；结构校验不证明语义正确。连接与认证由 AstrBot 管理；启用后歌曲文本会交给选定模型，「测试模型连接」会发送固定请求，可能产生费用。

历史不额外复制完整歌词、完整 Context 或 Debug 数据。模型回复按回调原文完整保存，保留 Markdown、链接和换行，不做截断、摘要或脱敏；回复本身可能包含引用歌词或个人信息。用户消息仍做规则脱敏并最多保留 2000 字符。历史按配置条数保留，不再限制文件字节数。关闭捕获保留已有记录，升级保留旧配置的显式开关和数量。管理页面向已登录 WebUI 管理员，没有聊天用户级隔离。

## Context、缓存和管理历史

音乐数据在 `on_llm_request` 通过标记为临时内容的 `TextPart` 添加到请求，保留既有 persona、prompt 和 history。长度预算包含提示前缀，不是精确 token 数。

音乐缓存保存 Track、原始 Lyrics 与 MusicContext JSON；成功的 Processor 分析作为独立生成变体保存在 `music_context.json`，与事实项共用容量。歌曲身份、歌词证据、模型/生成设置和 TTL 匹配时可以复用，重启后仍可读取。歌词变化、设置变化或过期会重新生成；上游变化在刷新或 TTL 到期后获知。

`history.json` 位于插件数据目录，与缓存分开。历史不额外复制完整歌词、完整 Context 或 Debug 快照；完整模型回复可能包含个人信息或歌词，用户消息规则脱敏不保证语义匿名。管理员可读取历史，没有聊天用户级隔离。关闭捕获保留已有记录；清空回复记录会使旧请求迟到的回复失效。清空缓存同时清除生成缓存，不清除历史或宿主对话。

Debug 原始快照只保存在运行内存，关闭后清除。管理 GET 不调用音乐或模型服务；显式模型连接测试会调用选定模型。Hook 记录不证明最终客户端送达。

## API 来源与公开材料

网易云 Provider 使用标准库直接请求 `music.163.com`，无需登录或独立 API 服务。接口、架构、许可和贡献说明见[公开参考说明](docs/REFERENCE.md)。截图范围见[截图说明](pages/dashboard/assets/readme/README.md)。音乐内容的版权不因插件 MIT License 而转移。

## Provider 扩展契约


1. **实现接口**：在 `providers/` 的直接 Python 模块中实现 `MusicProvider`，使用唯一 `name` 和按实际能力声明的 `ProviderCapability`。提供本地 `parse_url`/`supports`；导入、构造与匹配不得联网。
2. **返回统一结构**：实现 `async fetch(candidate, *, resource="track")`，或兼容的 `fetch_metadata(track_id)` / `fetch_lyrics(track_id)`。`track` 返回 `MusicTrack`，`lyrics` 返回原始 `MusicLyrics`；保证平台/歌曲身份一致。Provider 不清洗歌词、不构建 Context、不调用 LLM。
3. **注册 Provider**：`registry.register(ProviderClass)` 或注册已配置实例。默认入口 `discover()` 仅发现当前安装包内的具体类，并通过 `create_default()` 构造；它不会下载外部插件或服务。
4. **添加测试与来源记录**：覆盖 URL/ID 映射、能力路由、资源返回、缺歌词、失败/取消、缓存及完整临时注入。更新公开来源、API 和依赖说明，完成真实平台 API 验证后再修改平台状态。

现有注册 API 可用网易云实现演示：

```python
from astrbot_plugin_music_context.providers import ProviderRegistry
from astrbot_plugin_music_context.providers.netease import NetEaseProvider

registry = ProviderRegistry()
registry.register(NetEaseProvider)
provider = registry.get("netease")
```

资源/输入能力过滤先于优先级选择；请求失败不会自动切换另一个 Provider。Registry 的类型校验不代替新 Provider 的身份校验，新卡片布局与短链域名也需单独验证。完整实现见 `src/astrbot_plugin_music_context/providers/`。
