# DeerFlow 本地访问问题排查计划

## 问题描述
本地 DeerFlow 服务无法访问 - 返回 502 Bad Gateway 错误

## 排查步骤及结果

### 步骤 1: 使用浏览器确认能否正常访问
- **状态**: ✅ 已执行
- **结果**: 返回 HTTP 502 Bad Gateway

### 步骤 2: 检查本地 DeerFlow 服务是否已正确启动
- **状态**: ✅ 已执行
- **结果**: 
  ```
  CONTAINER ID   IMAGE                  STATUS
  13b72a318162   deer-flow-dev-gateway  Up 19 minutes
  d18f136cabbf   deer-flow-dev-langgraph Up 19 minutes
  4695c7193e66   deer-flow-dev-frontend  Up 19 minutes
  ad0ae7936c8f   nginx:alpine           Up 19 hours
  ```
  所有容器都在运行中

### 步骤 3: 确认网络连接状态及相关端口占用情况
- **状态**: ✅ 已执行
- **结果**: 端口 2026 正常监听

### 步骤 4: 查看 DeerFlow 应用日志文件以定位错误信息
- **状态**: ✅ 已执行
- **结果**: 发现关键错误日志
  ```
  [error] connect() failed (111: Connection refused) while connecting to upstream
  upstream: "http://192.168.200.4:3000/"
  ```
  Nginx 尝试连接到一个旧的/错误的 IP 地址

### 步骤 5: 验证配置文件中的网络参数设置是否正确
- **状态**: ✅ 已执行
- **结果**: 
  - Nginx 配置正确 (`server frontend:3000`)
  - 容器内网络正常（测试 `wget http://frontend:3000` 成功）
  - 问题定位：Nginx 的 upstream DNS 缓存问题

### 步骤 6: 尝试重启 DeerFlow 服务及相关依赖组件
- **状态**: ✅ 已执行
- **执行命令**: `docker restart deer-flow-nginx`
- **结果**: **成功！** HTTP 200 响应

---

## 问题根因

**Nginx 的 upstream DNS 解析缓存问题**

在 Docker 网络中，nginx 在启动时解析 `frontend` 服务的 IP 地址并缓存。由于某些原因（可能是容器重启、IP 变更等），nginx 缓存了一个错误的/过期的 IP 地址，导致 502 错误。

虽然前端容器在运行且服务正常，但 nginx 仍然尝试连接到一个不存在的 IP 地址。

## 解决方案

**重启 nginx 容器**：
```bash
docker restart deer-flow-nginx
```

这会强制 nginx 重新解析所有 upstream 的 IP 地址并重建连接。

---

## 验证结果

```bash
$ curl -s -o /dev/null -w "%{http_code}" http://localhost:2026/
200
```

DeerFlow 主页已正常加载！

---

## 后续建议

1. **预防措施**: 如果问题频繁发生，可以考虑：
   - 在 nginx 配置中添加 `resolve` 指令（需要 nginx Plus）
   - 使用 docker-compose 的 `--force-recreate` 选项重启服务
   - 定期检查容器健康状态

2. **监控**: 建议监控 nginx 错误日志：
   ```bash
   docker logs deer-flow-nginx --tail 100 | grep error
   ```
