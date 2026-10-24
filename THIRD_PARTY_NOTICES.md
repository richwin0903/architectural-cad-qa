# 第三方来源、许可证与再分发

本说明列出本仓库直接携带的主要第三方源代码、字体和测试素材；安装的 Python、
npm 与系统依赖仍适用各自随包提供的许可证。本文件不将所有依赖改为同一种许可证。

| 内容 | 来源 | 适用许可与本地文件 |
| --- | --- | --- |
| CAD 应用基础代码 | [jeremylongshore/cad-ai-agent](https://github.com/jeremylongshore/cad-ai-agent)，输入快照标记 `0.12.0` | Apache-2.0；[LICENSE](LICENSE)、[NOTICE](NOTICE)、[改动说明](docs/UPSTREAM.md) |
| GNU LibreDWG 0.14 源码与独立 `dwg2dxf` 转换程序 | [GNU 官方源码](https://ftp.gnu.org/gnu/libredwg/libredwg-0.14.tar.xz) | GPL-3.0-or-later；源码中 `COPYING`，以及逐字复制的 [GPL-3.0.txt](licenses/GPL-3.0.txt) |
| GNU 转换结果回归样例 | LibreDWG 源码中的 `test/test-data/2018/Line.dwg`，转换后压缩 | 按上游 GPL-3.0-or-later 保留并标注来源；[SOURCE.md](tests/deployment/fixtures/SOURCE.md) |
| Noto Sans SC Regular 字体 | Google Fonts 提供的 Noto Sans SC；字体内部版权为 Adobe 2014–2021，保留名称 `Source` | SIL OFL 1.1；[完整许可](fonts/OFL-NotoSansSC.txt)、[来源](fonts/README.md) |
| Noto Sans Regular 字体 | Google Noto；字体内部版权为 Google LLC 2015–2021 | SIL OFL 1.1；[完整许可](web/frontend/public/fonts/LICENSE-OFL.txt)、[来源](web/frontend/public/fonts/SOURCE.md) |

两个字体文件均原样分发，没有重新命名字体内部的 family/name。预览代码的字形映射
调整发生在运行时，不修改字体二进制。字体可以随应用一起分发，但需要保留版权和 OFL；
OFL 对字体本身单独售卖、改名和再许可有具体条件，以完整许可文本为准。

## 问答框架依赖

问答使用 [LangChain Deep Agents](https://github.com/langchain-ai/deepagents) 及
[langchain-deepseek](https://github.com/langchain-ai/langchain)（MIT 许可），
通过 Python 包安装，许可证随各包保留。模型 API 使用仍适用服务提供商的条款。

## GNU LibreDWG 的构建与分发

`vendor/` 保留官方压缩源码、签名和验证用 GNU 公钥环。版本、来源、SHA-256 和
构建方式见 [vendor/README.md](vendor/README.md) 与 Dockerfile。容器内转换器作为
独立子进程调用；Python 应用不链接 LibreDWG 库。独立进程的实现描述本身，不构成
对所有组合或分发方式的法律结论。

如果向学员或客户交付含 `dwg2dxf` 的镜像或二进制，应同时按 GPL 的适用要求提供
对应版本的完整源码、构建/安装脚本、许可证和所做修改。本仓库包含本次使用的 GNU
源码与构建脚本；交付时应让接收者能够取得这些材料，不能仅给一个未来可能失效的
上游链接。修改转换器后也应同步相应源码。仅提供网页服务与分发镜像是不同交付形式，
需要按实际方式核对义务。

## 合成示例与图片

公开示例均为该原型的合成测试图，不包含客户建筑图纸。示例的来源、生成方法和
哈希见 [local_examples/SOURCE.md](local_examples/SOURCE.md)。截图只展示合成图纸、
界面和依据定位，见 [docs/images/SOURCE.md](docs/images/SOURCE.md)。

本发布目录排除了来源未确认的 PDF、真实工程图纸和上游 `tests/fixtures/sourced/`。
新增图纸前仍应单独核对上传者是否有分享权，不能由本项目的软件许可证推定图纸授权。

## 用于课程、辅导或商业项目

Apache-2.0、GPL 和 OFL 并不因为收取课程或服务费用而自动禁止使用；相关条件仍须遵守。
应用源码的 Apache 许可不覆盖独立转换器和字体，也不能取消接收者依据这些开源许可
享有的权利。对上游代码应保留归属及修改说明，不宜包装成全部独立原创。

自行创作并有权使用的课程录屏、讲义、作业点评、辅导服务可以约定自己的交付范围
和使用条款；若材料复制了第三方代码、图片、图纸等，还应继续标注来源并遵守对应许可。
本仓库的公开文档并未自动为仓库外的个人教学材料授予许可。若要发布镜像、闭源改造
或签订企业交付合同，需针对实际交付物核对依赖和条款，本清单不是完整法律审计。
