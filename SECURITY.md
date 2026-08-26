# 安全说明

## API Key

QuickTranslator 需要用户自行提供翻译服务 API Key。运行时设置保存在：

```text
%APPDATA%\QuickTranslator\config.json
```

该文件是本机私密配置，已由 `.gitignore` 和发布脚本排除。请不要将它上传到 GitHub、问题报告、聊天记录或公开日志中。

如果 API Key 曾经出现在公开提交中，仅删除文件不能彻底清除 Git 历史。请立即到相应服务商控制台撤销旧 Key、创建新 Key，并清理仓库历史。

## 数据传输

用户选中的文字会发送到其配置的智谱、阿里云百炼或兼容服务。请勿使用本工具处理不应发送到第三方服务的敏感材料。

翻译记忆保存在本机的 `translation_memory.json`，不会主动上传到仓库。

## 报告问题

可以通过 GitHub Issues 报告安全问题，但请勿附带真实 API Key、私人文档、个人信息或包含上述内容的日志。若问题无法安全地公开复现，请先提交不含敏感数据的简要说明。
