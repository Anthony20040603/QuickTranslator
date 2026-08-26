# QuickTranslator

一个轻量的 Windows 划词 AI 翻译工具。选中任意软件中的文字，按住 `Ctrl` 并快速连按两次 `C`，程序会读取选区、调用翻译模型，并在鼠标附近流式显示译文。

本项目基于 [chyThor/QuickTranslator](https://github.com/chyThor/QuickTranslator) 继续开发。Fork 关系和提交历史用于保留原项目来源；后续新增功能、界面调整和发布包由本仓库维护。

## 功能

- Windows 全局划词翻译，默认快捷键为“按住 Ctrl，双击 C”
- 智谱 `glm-4.7-flash` 与阿里云百炼 Qwen-MT
- 精准 Plus / 极速 Turbo 模型切换
- 科研领域提示、术语表和本地翻译记忆
- Windows 原生菜单栏与属性页式设置窗口
- 跟随 Windows、浅色和深色主题
- 可编辑译文、确认译文、复制结果和始终置顶
- 常驻系统托盘，翻译窗口自动适应内容高度

## 下载与使用

推荐从仓库的 [Releases](https://github.com/Anthony20040603/QuickTranslator/releases) 下载最新稳定版便携包，解压后运行 `QuickTranslator.exe`。

首次使用：

1. 打开托盘菜单中的“设置”。
2. 填写智谱 API Key，或填写阿里云百炼 API Key。
3. 在浏览器、PDF 阅读器等软件中选中文字。
4. 按住 `Ctrl`，快速连按两次 `C`。

快捷键可以在设置中改为 `Ctrl + Alt + T`、双击 `Alt` 或双击 `Ctrl`。`Fn` 通常由键盘硬件层处理，Windows 程序无法稳定监听。

## 翻译模型

### 智谱 GLM

- 默认模型：`glm-4.7-flash`
- 备用模型：`glm-4-flash`
- API 地址兼容 OpenAI Chat Completions 格式

### 阿里云百炼 Qwen-MT

- 精准模式：`qwen-mt-plus`
- 极速模式：`qwen-mt-turbo`
- 未配置百炼或调用失败时自动回退到 GLM

程序会自动判断中译英或英译中，并将科研领域、术语表和相关翻译记忆传给 Qwen-MT。

## 从源码运行

需要 Windows 和 Python 3.10 或更高版本：

```powershell
python -m pip install -r requirements.txt
python app.py
```

运行测试：

```powershell
python -m unittest -v test_app.py
```

构建便携版：

```powershell
.\build_portable.bat
```

## 版本说明

| 版本 | 定位 |
| --- | --- |
| 0.5.2 | 早期公开基线 |
| 0.5.3 | 默认快捷键改为按住 Ctrl 双击 C，减少系统功能冲突 |
| 0.6.0 | Fluent 风格界面与模型服务预设 |
| 0.6.1 | Fluent 界面、窗口尺寸与长文本显示改进 |
| 0.6.3 | 回归轻量 Tkinter 与记事本式布局 |
| 0.6.4 | 使用 Windows 原生菜单栏，修复流式长文本闪烁 |
| 0.6.5 | 在原生菜单中加入可持久化的始终置顶开关 |
| 0.6.6 | 设置窗口改为 Windows 属性页式原生控件，并适配高 DPI |
| 0.6.7 | 修复高 DPI 下勾选标记遮挡菜单文字，当前稳定版 |
| 0.7.0 | ZenNotes / Qt Fluent 界面实验版，体积较大，作为预览保留 |

完整变化见 [CHANGELOG.md](CHANGELOG.md)。仓库没有 0.6.2 源码或发布产物，因此版本号保持空缺。

## 隐私与安全

- API Key 保存在 `%APPDATA%\QuickTranslator\config.json`，不会写入源码仓库或发布包。
- 翻译记忆保存在 `%APPDATA%\QuickTranslator\translation_memory.json`，仅供本机使用。
- 选中的文字会发送到你配置的翻译服务，请勿翻译不应离开本机的敏感内容。
- `.gitignore` 会排除配置、翻译记忆、环境变量文件、构建目录和压缩包。
- 提交前可运行 `package_open_source.ps1`；脚本会检查常见私密文件与疑似密钥。

安全注意事项与漏洞报告方式见 [SECURITY.md](SECURITY.md)。

## 许可证与来源

项目使用 MIT License，详见 [LICENSE](LICENSE)。二次开发请保留许可证和原项目来源说明。更多信息见 [NOTICE.md](NOTICE.md)。
