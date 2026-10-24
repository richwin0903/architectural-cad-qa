# 试用账号与访问 key

管理员通过命令行预分配账号，用户在现有登录页填写账号及访问 key。
访问 key 就是此账号的随机登录密码，不是 DeepSeek Key，也不是通用 API Bearer token。
没有开放注册或账号管理接口；管理员原有密码和权限保持独立。

默认限制：首次登录后 7 天有效，导入后 30 天内激活；每天 10 次问答、累计 30 次；
每天 10 次上传、每个文件最大 25 MB。每日额度按北京时间零点刷新。
请求开始处理即计入额度，模型或转换失败也计次；参数/归属校验失败、服务繁忙不扣。
同账号一次只能处理一个任务，全站一次只处理一个问答。管理员不受试用次数配额限制。

每个试用账号只有一个有效登录；新登录撤销旧登录及其图纸。每次成功上传替换该登录的旧图纸。
图纸、预览、下载、实体检索和问答均验证登录归属；知道其他人的图纸 ID 也不能读取。
退出、到期、停用后不能再访问图纸；图纸临时文件由清理任务回收。

## 生成和导入

先在本机源码目录使用已安装的 Python 环境生成凭证，输出目录必须不存在：

```bash
PYTHONPATH=src:. .venv/bin/python -m web.backend.trial_accounts generate \
  --count 10 --output-dir .local/trial-accounts
```

目录权限 700、文件权限 600。`accounts.txt` 包含明文 key，供管理员逐个发放；
`accounts-import.json` 只有账号、密码哈希和限制。两个文件都被 Git/Docker 忽略，发布扫描也拒绝它们。
不要将完整凭证表贴到帖子、聊天群或仓库。默认生成 `trial01` 至 `trial10`。

通过 SSH 标准输入传输哈希清单（替换主机与容器名）：

```bash
ssh your-server 'docker exec -i cad-qa-app-1 python -m web.backend.trial_accounts import' \
  < .local/trial-accounts/accounts-import.json
```

清单重试导入是幂等的，不会重置额度、重新启用账号或延长试用。已有同名但不同配置的账号会拒绝导入。
明文 key 不上传到服务器、不进入镜像层或服务日志。用户在 HTTPS 登录表单中填写 key。

## 查看、停用、调整额度、轮换

在服务器执行（需要容器管理权限）：

```bash
docker exec cad-qa-app-1 python -m web.backend.trial_accounts list
docker exec cad-qa-app-1 python -m web.backend.trial_accounts disable trial01
docker exec cad-qa-app-1 python -m web.backend.trial_accounts enable trial01
docker exec cad-qa-app-1 python -m web.backend.trial_accounts limits trial01 --daily 10 --total 30 --uploads 10
```

`list` 不输出哈希或 key，只列启用状态、激活/到期时间、限制与剩余额度。
启用不会重置到期时间和累计用量；调整配额只改变上限，不删除已使用次数。

密钥轮换时，在受保护的本地文件生成新 key 和 scrypt 哈希，通过 SSH stdin 将哈希传给：

```bash
docker exec -i cad-qa-app-1 python -m web.backend.trial_accounts rotate trial01
```

不要在命令参数里放 key。轮换会撤销所有旧登录，保留额度与原到期时间。

## 存储和回滚

账号哈希、额度、短期任务租约保存在 `CAD_AUTH_DB`，沿用持久数据卷和文件权限 600。
发布前用 SQLite backup API 备份此文件，不复制正在写入的数据库文件。
服务重启保留账号和用量；图纸需要重新上传。意外终止留下的任务租约最长 10 分钟后释放。
新表为增量创建，不改写管理员会话表结构；回滚到仅管理员版本会停止试用登录，
不会删除试用账号及额度表。不要用 `down -v` 清除账号数据卷。
