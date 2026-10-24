# 服务器部署与维护

本仓库与具体云厂商、SSH 主机和已有服务目录无绑定。以下操作在你选择的 Linux 主机上执行。
需要 Docker Compose 2.30+；用非 root 账户管理项目，按主机既有约定提供 Docker 权限。

## 部署

1. 克隆仓库，运行 `python3 scripts/init-config.py`。密码与 DeepSeek Key 通过隐藏输入填写。
2. 修改不含密钥的 `.env`，设置 `CAD_PUBLIC_ORIGIN=https://你的域名`。
3. 执行 `docker compose up --build -d --wait`。应用仅监听本机 `127.0.0.1:8323`。
4. 将 `config/Caddyfile.example` 中的 CAD 路由合并到已有 HTTPS 站点，替换示例域名并校验配置。
   保留服务器上其他站点和服务的路由。使用 Nginx 时保持相同路径剥离、上传限制和超时。
5. 使用 HTTPS 打开 `/cad/local-qa`，检查登录、示例上传、预览、真实问答和退出。

固定单个 Uvicorn worker：上传会话状态在进程内，多个 worker 不能共享这些临时图纸。
生产使用 `web.backend.production:app`；不要部署保留的 `web.backend.main:app` 或启用开发模式。

`compose.local.yaml` 的 Caddy 只供本机 HTTPS 体验，绑定回环地址并使用本机 CA。
公网应使用已有反向代理或明确配置的公网 TLS；不要把该本机证书方案当作公网受信证书。
没有域名时，可在服务器启用本机 Compose 组合，经 SSH 转发 HTTPS 端口到本机验证，
核对导出的 CA 证书后信任该连接。不要通过公网 HTTP 发送密码。

## 状态、日志和更新

```bash
docker compose ps
curl -fsS http://127.0.0.1:8323/api/health
docker compose logs --tail 100 -f app
docker compose restart app
```

容器自动重启并带健康检查；日志轮转为 3 个 10 MB 文件。内存上限 2 GB，临时目录
512 MB，图纸会话按期限清理。重启后图纸需重新上传，管理员会话库位于独立命名卷。

更新前保留当前提交和镜像标签，并按自己的备份制度备份会话数据卷。
建议在 `.env` 为每个版本设置 `CAD_IMAGE=cad-qa:你的版本号`，然后构建：

```bash
git pull --ff-only
docker compose build
docker compose up -d --wait
```

若使用本机 HTTPS 组合，每条 Compose 命令都加 `-f compose.yaml -f compose.local.yaml`。
不要执行 `down -v`，它会删除数据卷及本机 TLS CA。

## 配置和密码变更

保护 `.secrets/runtime.env`（目录 700，文件 600），仅在服务器编辑 Key。
管理员密码用 `scripts/init-config.py` 所用的 scrypt 算法生成哈希；不要在 shell 参数或历史中传密码。
修改受保护文件后需要重建容器，而不仅是 restart：

```bash
docker compose up -d --force-recreate --wait
```

如需重新生成配置，先把旧文件安全移动到仓库以外的受保护备份位置，再运行初始化脚本。
它不会覆盖已有文件。密码哈希变化会使旧登录会话失效。

## 回滚

把工作目录切回已验收提交，将 `.env` 的 `CAD_IMAGE` 恢复到保留的旧镜像标签，再执行
`docker compose up -d --wait`。配置文件与数据卷独立于代码；本 MVP 暂不包含跨版本数据库迁移框架。
使用回滚提交对应的代理配置，并校验后重载。不要使用清理全部镜像或全部容器的命令。

## 资源与访问边界

单文件 25 MB，代理允许稍大的 multipart 请求体。DWG 转换 60 秒，问答总超时 180 秒；
代理读写超时 210 秒。每次问答最多 6 次模型调用、24 次工具调用、10 张渲染视图；
开场最多预览 4 个非空布局，其余布局由工具按需检查并披露未覆盖范围。
默认 deepseek-flash 必须支持图像输入和工具调用；换用纯文本模型会明确报错，不伪造视觉结果。
模型费用来自你的 DeepSeek 账户，当前没有访客配额和全站费用熔断。
公开引流前应增加独立试用身份、用量限制与费用上限；不要直接公开管理员账号。
