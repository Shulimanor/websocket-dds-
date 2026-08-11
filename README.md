# WebSocket / OpenDDS 调试工具

带 Web 面板的调试工具，支持 WebSocket 和 OpenDDS 两种协议。启动后在浏览器面板中一键启停 Server/Client 或 Publisher/Subscriber，实时查看收发日志。

## 快速开始

```bash
pip install websockets
python run.py
```

浏览器打开 `http://localhost:8765`，即可看到调试面板。

顶部下拉选择 **WebSocket** 或 **OpenDDS** 模式。

## WebSocket 模式

浏览器打开 `http://localhost:8765`，即可看到调试面板。

## 面板操作

```
┌──────────────────────────────────────────────────┐
│  WebSocket 调试工具      [Server] [Client]   ●  │
├──────────────────────────────────────────────────┤
│  Host: [0.0.0.0]  Port: [8765]  □ 广播          │
│  [启动服务器]  [停止]                            │
├──────────────────────────────────────────────────┤
│  [日志区域]                                      │
│  14:30:01  --  系统      控制通道已建立           │
│  14:30:05  --  127.0.0.1:52130  已连接           │
│  14:30:06  <<  127.0.0.1:52130  hello            │
│  14:30:06  >>  127.0.0.1:52130  Echo: hello       │
├──────────────────────────────────────────────────┤
│  [输入消息，Enter 发送________________] [发送]    │
│  就绪                    发送 1 / 接收 1          │
└──────────────────────────────────────────────────┘
```

### Server 标签
- 填写 Host / Port → 点击 **启动服务器**
- 外部设备连接 `ws://你的IP:端口` 即可收发消息
- 勾选"广播"可将客户端消息转发给所有其他客户端（默认 Echo 回显）

### Client 标签
- 填写远程 WebSocket URL → 点击 **连接**
- 收到消息实时显示在日志区

### 发送消息
- Server 启动后，面板可向所有连接的客户端发送消息
- Client 连接后，面板可向远程服务器发送消息
- 支持 JSON，自动缩进美化

### 终端 stdin
启动后在终端输入消息同样会广播/发送到活跃连接；输入 `exit` / `quit` 或 Ctrl+C 关闭。

## OpenDDS 模式

顶部下拉切换到 **OpenDDS**，可同时启动 Publisher 和 Subscriber。

> 需要安装 OpenDDS Python bindings（安装后自动启用，未安装则提示）。

**IDL 定义** ([DdsDebug.idl](DdsDebug.idl))：
```idl
module DdsDebug {
    @topic
    struct DebugMessage {
        string sender;
        string payload;
        long long timestamp;
    };
};
```

**操作流程**：
1. 切换到 OpenDDS → 配置 Domain / Topic
2. 点击 **启动发布** → 输入消息 → Enter 发送
3. 点击 **启动订阅** → 收到的消息实时显示在日志区

**控制协议**：
```
面板 → Python:  {cmd:"start_pub"/"start_sub"/"stop_pub"/"stop_sub"/"dds_send", domain, topic, text}
Python → 面板:  {state:"pub_on"/"pub_off"/"sub_on"/"sub_off"}
```

## 命令行参数

```
usage: run.py [--host HOST] [--port PORT] [--debug] [--quiet] [--no-color]

  --host HOST     面板监听地址，默认 0.0.0.0
  --port PORT     面板端口，默认 8765
  --debug         启用 DEBUG 日志
  --quiet         只显示错误和警告
  --no-color      禁用彩色输出
```

## 架构

```
浏览器                  ws://host:port/__ws__     Python DebugHub
  │   调试面板           (控制通道 JSON)            │
  │                                                 │
  │   点击"启动服务器" ─────────────────────────► 动态创建子 WS 服务器
  │                                                 │  ws://0.0.0.0:自定义端口
  │                                                 │
  │   点击"连接"     ─────────────────────────► 动态创建子 WS 客户端
  │                                                 │  连接到远程服务器
  │                                                 │
  │   收发日志      ◄────────────────────────── 实时推送每条消息
```

- 面板对外部设备完全透明：外部设备连接的是动态子服务器，不知道面板存在
- 面板通过 `/__ws__` 控制通道与中枢通信（JSON 协议）
- 浏览器 HTTP 请求 → 返回调试面板 HTML 页面

## 日志格式

```
-> 绿色 = 发出的消息
<- 蓝色 = 收到的消息
-- 青色 = 统计/系统
!! 黄色 = 警告
XX 红色 = 错误
```
