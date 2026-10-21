# 源码与素材来源

记录日期：2026-10-21。

## 源码基础

本发布副本以已经完成并部署验证的“建筑 CAD 图纸问答”原型为基础，
从部署工作目录中的应用源码筛选复制而来。原型基于
[jeremylongshore/cad-ai-agent](https://github.com/jeremylongshore/cad-ai-agent)，
保留其 Apache-2.0 许可、源码归属及相关模块。

这是一份面向只读问答部署的派生源码包，不是整个项目从零原创，
也不是上游仓库全部历史、文档、桌面应用及示例素材的镜像。
现有包元数据中的版本号来自上游；本发布未据此宣称一个尚未核实的上游
Git commit 或发行标签。

[UPSTREAM.md](UPSTREAM.md) 保留来源项目的说明文档，供辨认项目来历。
其中描述的上游完整能力与历史指标，不代表本发布启用了相同的全部界面、
接口或工作流。当前发布入口、运行方式和验收范围以仓库 README 为准。

## 原型及部署阶段的主要新增内容

- React/Vite 的 `/local-qa` 问答页面，以及复用 CAD 图元依据定位的交互预览。
- FastAPI 的真实 DeepSeek 问答、图元检索、图块统计、闭合轮廓量取及数据发送提示。
- 生产只读入口、单管理员会话、退出及过期、登录限速、CSRF 与接口白名单。
- GNU LibreDWG 在服务器本地将 DWG 归一化为 DXF，保留兼容性提示和转换边界。
- 可分发的 Noto 中文字体、交互字体别名修正，以及 Linux 静态渲染的字体回退。
- 容器构建、反向代理接入及实际部署验收脚本。

这些内容在既有上游架构及原型之上实现。原有解析、几何、显示和其他复用代码
仍应保留其原作者及许可归属；第三方组件的边界见
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

## 发布的图纸与图片

| 路径 | 来源与用途 |
| --- | --- |
| `local_examples/architectural-demo.dxf` | 由合成样例生成脚本创建的测试平面图，不是真实建筑项目。 |
| `local_examples/architectural-demo.dwg` | 使用独立测试生成器和 GNU LibreDWG 创建的合成 DWG，用于中文、图块及几何回归。 |
| `tests/deployment/fixtures/libredwg-line-2018.dxf.gz` | GNU LibreDWG 0.14 官方发行包中 `test/test-data/2018/Line.dwg` 的转换输出；来源、许可及校验值见相邻 `SOURCE.md`。 |
| `docs/images/preview.png`、`docs/images/evidence.png` | 合成样例的预览及问答验收展示，不作为真实项目图纸或建筑面积证明。 |
| `docs/images/workspace.png`、`docs/images/login.png` | 本项目工作台、登录页及建筑示意插画。 |

合成样例中的门图块实例数量为 2，会议室闭合几何轮廓为 16㎡、周长 16m。
这是测试输入的几何结果，不等于实际建筑净面积，也不表示已经完成完整建筑语义识别。

发布副本不包含来源未核实的建筑 PDF、外部 DXF 样例集合、私人上传图纸，
或部署工作目录中的完整验收截图、原始问答日志和运行证据目录。
随 GNU LibreDWG 官方源码归档保留的上游文件，按该独立发行包及其许可管理，
不作为本应用自有素材。

## 回归素材的范围

本发布的应用回归素材使用合成数据；需要保留真实转换元数据的回归，
使用明确标注来源和许可的 GNU LibreDWG 测试转换输出。
未复制原型中的来源不明 PDF、第三方 DXF 示例目录或工作中的私有图纸。

本文件记录来源和发布范围。可复现的检查命令、最新验收结果及仍存在的
兼容性限制应以仓库 README、测试和部署文档为准。
