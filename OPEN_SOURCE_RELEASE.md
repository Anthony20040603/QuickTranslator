# QuickTranslator 0.6.6 开源发布说明

本压缩包同时包含源码和 Windows 便携版：

- `source/`：Python 源码、测试、构建及安装脚本。
- `portable/QuickTranslator/`：可直接运行或安装的 Windows 版本。
- `LICENSE`：MIT 开源许可证。

## 使用

直接使用时，进入 `portable/QuickTranslator`，运行 `QuickTranslator.exe`；也可运行
`install.bat` 安装并创建开始菜单和开机启动入口。

从源码运行或开发时，进入 `source`，安装 `requirements.txt` 中的依赖后运行
`python app.py`。运行 `python -m unittest -v test_app.py` 可执行测试，运行
`build_portable.bat` 可重新构建便携版。

## API Key 与隐私

发布包不包含任何用户 API Key、个人设置或翻译记忆。每位使用者应在软件设置中
填写自己的 API Key。运行时配置保存在当前用户的：

`%APPDATA%\QuickTranslator\config.json`

该文件包含明文 API Key，不应提交到代码仓库或分享给他人。选中的文字会发送至
使用者自行配置的模型服务。
