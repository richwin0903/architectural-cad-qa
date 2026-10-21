# 安全与数据说明

生产入口仅为 `web.backend.production:app`。`main:app` 保留用于上游兼容和测试，
含开发、编辑和调试功能，不应部署为公网应用。生产服务显式拒绝开发鉴权模式。

- 密码只保存 scrypt 哈希；会话 Cookie 为 HttpOnly、Secure、SameSite=Strict。
- 登录失败限速；写请求校验 Origin 和 CSRF；退出和过期会撤销会话。
- 后端仅供本机或内部网络访问，公网通过 HTTPS 反向代理。
- 密钥由受保护的运行时文件提供，不传给前端或写入镜像。
- 上传文件和渲染均为临时数据，退出、过期或重启后可能删除。
- DWG 转换器是原生程序；在非 root、资源受限的隔离容器内执行。

问答会把相关结构化图元、文字、属性、工具结果及对话发送给配置的 DeepSeek API；
目前不发送原始 DWG/DXF 或图像。请先确认图纸可用于这一处理方式。

当前为单管理员 MVP，没有公众试用额度或模型费用上限。请勿公开管理员密码。

发现漏洞时，请通过仓库维护者在 GitHub 个人资料中提供的私密渠道联系；如果该仓库
启用了 Private vulnerability reporting，可使用 Security 页面的私密报告入口。
请勿在公开 Issue 附加密码、Key、Cookie 或真实项目图纸。
