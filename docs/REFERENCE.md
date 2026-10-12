# Music Context v1.0.2 公开参考说明

## 架构与扩展

根 `main.py` 为 AstrBot 入口，包内 `main.py` 管理生命周期。parser 识别候选、链接、卡片与短链；providers 检索原始 Track/Lyrics 并按资源能力路由；models 保存统一数据；lyrics 清洗时间标签与重复行；context 在预算内构建文本与可选辅助分析；integration 注入临时 TextPart；cache 保存音乐缓存和管理历史；webui 提供原生管理接口。Dashboard 为本地 HTML/CSS/JS，无前端构建依赖。

Provider 不生成 Context 或调用 LLM；扩展契约和配置详见[使用指南](../USER_GUIDE.md)。Context Processor 使用 AstrBot 已配置的文本 Provider 与 `text_chat()`，不另设密钥或 HTTP 客户端。默认关闭，未选模型、超时或输出无效时保留事实 Context。辅助分析并非经过验证的知识，`confidence` 无校准评估时为空。Debug 技术快照受配置控制，仅存在运行内存。

临时内容不会改写原有 persona、prompt 或 history。失败时继续普通聊天，不调用 `stop_event()`。缓存命中不代表平台或模型实时健康。管理历史仅表示 Hook 观察；客户端送达由宿主负责。

## 网易云 API

具体实现为 `src/astrbot_plugin_music_context/providers/netease.py`，使用 Python 标准库直接调用网易云旧式单曲详情 `/api/song/detail/` 与歌词 `/api/song/lyric` 端点。详情请求包含歌曲 ID，歌词请求包含 ID 与版本参数，使用 JSON 响应，不要求用户 Cookie 或登录；校验业务状态、歌曲身份及字段类型，限制响应大小和等待时间。短链解析只允许支持的平台/域名并验证落地链接。

这些接口没有稳定性或速率保证；不依赖 NeteaseCloudMusicApi、Enhanced 或 Meting 服务，没有自动替代服务回退。QQ Music 和其他数据源尚未实现。音乐元数据、歌词与翻译由平台返回，不随安装包分发；使用者须遵守平台和内容权利要求。

## 来源、致谢与依赖

特别感谢 [jjf1126/astrbot_plugin_ncm_get](https://github.com/jjf1126/astrbot_plugin_ncm_get) 为早期音乐信息获取方向提供思路参考；该致谢不表示复制其代码或取得其材料授权。也感谢 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供宿主插件与模型接口。其[安装文档](https://docs.astrbot.app/use/plugin.html)说明宿主管理入口。

项目自有代码、文档和原创界面采用 [MIT License](../LICENSE)。Dashboard 的八个 SVG 图标源自 [Lucide](https://github.com/lucide-icons/lucide/tree/70562c1ee1c4fdcf736fe97bc893fb8511927934)，采用 ISC，music/search 的 Feather 来源另适用 MIT；[逐图 notices](../pages/dashboard/assets/icons/NOTICE.md)和[完整许可](../pages/dashboard/assets/icons/LICENSE.txt)随包保留。

宿主 [AstrBot](https://github.com/AstrBotDevs/AstrBot/blob/master/LICENSE) 和 [CPython](https://docs.python.org/3/license.html) 及宿主依赖遵循各自许可证，不由本项目 MIT 重新许可，且不随插件打包。插件使用宿主接口和标准库，无额外 `requirements.txt`、第三方前端框架、CDN、字体或音频文件。根 [NOTICE.md](../NOTICE.md)列出分发边界。

README 图片展示公开范围内的界面与指标，说明见[截图来源](../pages/dashboard/assets/readme/README.md)。歌词、翻译、服务及第三方材料不在项目 MIT 授权范围，安装包不包含其内容样本。

## 贡献与发行范围

公开仓库 main 保存正式代码、用户文档、许可和公开图片，Tag 与 Release 表示正式快照。日常开发、development、测试、研究和内部审查保存在维护者的私有开发仓库。贡献请通过公开 Issue 或 PR 说明最小复现、验证和来源；日志须移除聊天、歌词、账号、追踪参数与凭据。新增依赖、接口或破坏性行为先与维护者讨论。

后续版本按显式文件清单从审核后的正式快照导出，禁止同步整个私有仓库或合入其历史。公开仓库无内部研究、任务书、审计报告、测试脚本、用户缓存或运行配置。安装请使用 README 的 v1.0.2 Release 地址。独立安装冒烟使用 AstrBot 4.28.2 与合成平台/模型响应；真实模型费用、平台服务可用性和完整宿主版本矩阵仍需按部署环境验证。
