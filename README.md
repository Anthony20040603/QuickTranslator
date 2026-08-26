# 划词翻译

一个轻量的 Windows 划词翻译工具。选中任意软件中的文字，按住 `Ctrl` 并快速连按两次 `C`，程序会读取选区、调用 AI 翻译，并在光标附近流式显示译文。

## 快速开始

1. 安装 Python 3.10 或更高版本（安装时勾选 **Add Python to PATH**）。
2. 双击 `run.bat`。
3. 点击“设置”，填入 API Key 后保存。
4. 在浏览器、PDF 阅读器或聊天软件中选中文字，按住 `Ctrl` 并快速连按两次 `C`。

默认快捷键可在“设置 → 快捷键”中更改，可选 `Ctrl + Alt + T`、双击 `Alt` 或原来的双击 `Ctrl`。`Fn` 通常由键盘硬件层处理，Windows 程序无法稳定监听。

## 外观与主题

界面采用轻量的 Windows 记事本式布局：系统原生标题栏下面是 Windows 原生的“模型、主题、窗口、设置”菜单栏，展开和横向切换行为与 Everything 等传统 Windows 软件一致。“窗口”菜单可开启始终置顶，并会保存选择。主体是不带卡片和边框的纯色文本编辑区，底部保留确认与复制按钮。程序图标用于 EXE、窗口标题栏和系统托盘。

设置窗口使用 Windows 属性页式布局，由原生标签页、输入框、下拉框以及“确定、取消、应用”按钮组成；程序会启用高 DPI 感知并使用 Windows 菜单字体，避免高缩放比例下的菜单文字裁切。

在“设置 → 外观”中可选择“跟随 Windows”“浅色”或“深色”。默认跟随 Windows；系统切换应用深浅色后，正在运行的 QuickTranslator 会自动更新并记住设置。

## 在另一台 Windows 电脑使用

运行 `build_portable.bat` 后，将 `dist\QuickTranslator` 整个文件夹复制到另一台电脑，双击其中的 `QuickTranslator.exe`。不能只复制 EXE，因为文件夹内还包含程序运行库。也可以直接发送项目生成的 `QuickTranslator-portable.zip`，解压后运行。

API Key 不会打进便携包；请在新电脑的托盘菜单“设置”中重新填写。若要迁移术语、设置和翻译记忆，可复制 `%APPDATA%\QuickTranslator`，但其中包含明文 API Key，只应通过可信的加密方式传输。

### 安装、开机启动和开始菜单

解压完整便携包后，双击 `install.bat`。程序会先校验发布清单和全部文件，随后安装到 `%LOCALAPPDATA%\Programs\QuickTranslator`，并自动创建开始菜单入口和当前用户的开机启动快捷方式。若需要固定到开始屏幕，在开始菜单搜索“QuickTranslator”，右键选择“固定到开始屏幕”。

拿到新版本后重新运行其中的 `install.bat` 即可升级。升级器会先在暂存目录运行自检，保留一个上一版本，并在新版本启动失败时自动回滚；不会删除 `%APPDATA%\QuickTranslator` 中的设置和翻译记忆。卸载时运行安装目录中的 `uninstall.ps1`。

程序启动后会常驻 Windows 系统托盘，不占用任务栏。关闭翻译窗口只会隐藏程序；在托盘图标上右键可以重新显示、打开设置或彻底退出。

翻译窗口会在翻译完成后根据译文长度一次性调整高度；流式输出期间不再反复缩放，长内容使用细滚动条浏览。标题栏使用 Windows 原生的最小化、最大化和关闭按钮，窗口四边可按正常 Windows 窗口方式缩放。快速双击 `Shift` 可以立即找回翻译窗口。

科研翻译可在“设置”中填写研究领域和术语表。术语表使用 `原文=指定译文` 格式，多项之间用分号分隔，例如：`cell culture=细胞培养; power=功效（统计学）`。选取完整句子或段落通常比只选单个词更容易得到符合语境的译法。

程序默认使用智谱开放平台：

- API 地址：`https://open.bigmodel.cn/api/paas/v4/chat/completions`
- 模型：`glm-4.7-flash`（免费文本模型）
- 备用模型：`glm-4-flash`（首选模型拥堵时自动切换）

## 专业 Qwen-MT 模式

设置中填写阿里云百炼 API Key 后，程序会优先使用专用翻译模型：

- 精准模式：`qwen-mt-plus`
- 极速模式：`qwen-mt-turbo`
- 百炼未配置或请求失败：自动切回现有 GLM 通道

建议在百炼控制台复制北京区域 Workspace 专属的完整 Chat Completions 地址。主窗口的“模型”菜单可切换“精准 Plus / 极速 Turbo”。程序会自动识别中译英或英译中，并把科研领域、术语表和相关翻译记忆通过 Qwen-MT 的原生参数传入。

译文允许直接编辑。人工修订后点击“确认译文”，原文和最终译文会保存到 `%APPDATA%\QuickTranslator\translation_memory.json`；以后翻译相似内容时会选取最相关的 5 条作为翻译记忆。该文件仅保存在本机。

也可使用任何兼容 OpenAI Chat Completions 格式的服务，例如 DeepSeek、通义千问等；填写对应的 API 地址和模型名即可。

## API Key 与隐私

设置保存在 `%APPDATA%\QuickTranslator\config.json`。API Key 不会发送给翻译服务以外的任何地方。选中的文字会发送给你配置的模型服务，敏感内容请谨慎使用。

也可以不在配置文件中保存密钥，启动前设置环境变量 `TRANSLATOR_API_KEY`；只有配置文件中尚无密钥时才会读取它。

## 当前限制

- 默认触发方式为按住 `Ctrl` 后快速连按两次 `C`，也可在设置中更改。
- 依赖目标软件支持 `Ctrl+C` 复制选区。
- MVP 仅支持 Windows。
