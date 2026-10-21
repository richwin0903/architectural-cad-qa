# 上传 GitHub

此目录是独立项目，不要上传它的父目录。不要上传线上服务配置、受保护的 runtime.env、
私有图纸、会话数据库、日志或浏览器 Cookie。

## 检查

```bash
git status --short
python3 scripts/check-release.py --tree
git add .
python3 scripts/check-release.py
git diff --cached --stat
```

检查脚本会核对即将提交的内容、密钥模式、禁止路径和文件大小；它不能替代对新增文件的人工审核。
不要通过 `git add -f` 绕过敏感文件的忽略规则。

## 首次发布

在 GitHub 新建空仓库，不预先添加 README 或 LICENSE。仓库名称可用
`architectural-cad-qa`。本地已初始化和提交时，直接绑定你创建的仓库地址：

```bash
git remote add origin <你的GitHub仓库地址>
git branch -M main
git push -u origin main
```

如果这是一份下载的源码包且没有 `.git`，先执行 `git init`、`git add .` 和首次提交。
仓库内不保存 GitHub Token；使用 GitHub CLI、SSH 或系统凭证管理器认证。

当前上传未启用自动 CI，工作流保留在 `.github/ci.yml.example`。
需要启用时，先给 GitHub 凭证授予 `workflow` 权限，然后执行并提交：

```bash
mkdir -p .github/workflows
cp .github/ci.yml.example .github/workflows/ci.yml
```

启用后在 Actions 查看实际结果。可以把 README 中的合成图纸截图作为项目介绍，
并在 About 写明“React + FastAPI + ezdxf + DeepSeek，只读建筑 CAD 问答 MVP”。
试用站点账号与 API Key 不应发布到 README。

## 后续发布

对功能和配置修改运行 README 中的测试，提交前执行发布检查。新增图纸样本必须是合成文件，
或具有明确可再分发许可证并附来源。发布容器镜像时须同时满足 LibreDWG 的 GPLv3+ 源码义务；
不能把捆绑转换器的镜像仅声明为 Apache-2.0。
