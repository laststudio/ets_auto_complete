# Local Study Workflow CLI

这是从原 PHP 程序迁移出的独立 Python CLI。程序不包含登录实现，不内置任何作业平台
域名，也不保存手机号、密码、设备码或平台 token。

保留的部分包括：

- 原 Base64 请求 envelope、MD5 签名和响应解码格式
- 作业列表、结构、开始、资源映射、预上传、逐题同步、用时和汇总接口方法
- 加密资源 ZIP 密码推导和题目提取
- 朗读、跟读、听选、填空、角色扮演和信息转述题型解析
- Edge TTS、可配置声音克隆和 ffmpeg 音频转换
- 讯飞 ISE WebSocket 评测
- 本地评分 XML、统一分数和逐题分数覆盖
- 八阶段批处理、进度输出、音频/XML 落盘和 JSON 报告
- PK、词汇训练、模拟考试和作文分支
- 可运行的本机 mock interface server

## 环境

```fish
source /home/shuaiqiu/.venvs/study/bin/activate.fish
cd /home/shuaiqiu/Projects/ets_auto_complete
python -m pip install -e ".[dev]"
ets-demo doctor
```

系统需要 `ffmpeg`。CLI 也可以通过模块启动：

```fish
python -m ets_demo --help
```

## 本地作业

示例清单位于 `examples/homework.json`：

```fish
ets-demo inspect examples/homework.json
```

清单结构：

```json
{
  "id": "demo-001",
  "name": "Local practice",
  "kind": "normal",
  "total_point": 30,
  "resource_id": "local-resource",
  "set_id": "local-set",
  "questions": [
    {
      "entity_id": "read-1",
      "order": 0,
      "text": "Hello world.",
      "category": "read_sentence",
      "structure_type": "collector.read"
    }
  ]
}
```

`kind` 支持：

```text
normal       普通听说，逐题 audio/sync-v2 + report-score
mock         模拟考试，exam/sync + exam/submit
pk           单词 PK，pk-info/start-word-pk/submit-word + report-score
vocab        词汇训练，homework/score-sync + report-score
composition  作文，save-draft/mark/homework-submit
```

也可以直接读取加密资源 ZIP：

```fish
ets-demo parse-zip ./resource.zip
ets-demo inspect ./resource.zip
```

ZIP 密码推导保留原算法：读取末尾 336 字节，取 `16:144` seed，密码为
`MD5(seed).upper() + MD5(first).upper()`。

## 完整本地流程

默认使用 Edge TTS 和本地 XML 评分：

```fish
ets-demo run examples/homework.json --output-dir output/demo
```

输出：

```text
output/demo/
├── audio/       每道朗读题的 MP3
├── xml/         每道题的评分 XML
└── report.json  作业、阶段、逐题结果和汇总
```

设置统一评分维度：

```fish
ets-demo run examples/homework.json \
  --output-dir output/scored \
  --score 4.8 \
  --accuracy 4.7 \
  --fluency 4.9 \
  --integrity 5.0 \
  --stress 4.6
```

使用逐题覆盖：

```fish
ets-demo run examples/homework.json \
  --score-overrides examples/score_overrides.json
```

覆盖文件以零起始题号为 key：

```json
{
  "0": {"total": 4.8, "accuracy": 4.7, "fluency": 4.9},
  "2": {"ise_score": 4.5, "integrity": 4.8}
}
```

`--target-total` 可以单独控制最终本地汇总分：

```fish
ets-demo run examples/homework.json --target-total 26.5
```

其他完整示例：

```fish
ets-demo run examples/pk_homework.json --target-total 85
ets-demo run examples/vocab_homework.json --target-total 90
ets-demo run examples/mock_homework.json
ets-demo run examples/composition_homework.json
```

## TTS 与声音克隆

单独生成 Edge TTS：

```fish
ets-demo tts "Hello world" --output output/hello.mp3
```

完整流程使用声音克隆时，URL 和 key 必须由环境显式提供，源码中没有默认供应商地址：

```fish
set -x CLONE_TTS_URL "https://your-provider.example/clone"
set -x CLONE_TTS_RESULTS_URL "https://your-provider.example/results"
set -x CLONE_TTS_API_KEY "..."

ets-demo run examples/homework.json \
  --voice-mode clone \
  --clone-reference ./reference.wav
```

克隆实现支持直接音频响应或 `uuid + results` 轮询，并使用 ffmpeg 做响度归一化。

### 通用 HTTP TTS

支持 OpenAI-compatible 和 Gradium 两种请求格式，源码同样不包含默认域名：

```fish
set -x HTTP_TTS_URL "https://your-provider.example/v1/audio/speech"
set -x HTTP_TTS_API_KEY "..."
set -x HTTP_TTS_STYLE openai
set -x HTTP_TTS_MODEL kokoro
set -x HTTP_TTS_VOICE af_bella

ets-demo http-tts "Hello world" --output output/http.mp3
ets-demo run examples/homework.json --voice-mode http
```

Gradium 样式：

```fish
set -x HTTP_TTS_STYLE gradium
set -x HTTP_TTS_URL "https://your-provider.example/tts"
```

响应支持直接音频、SSE Base64 `chunk`、JSON Base64 音频和 `uuid + results URL` 轮询，
最终统一转为 24 kHz 单声道 MP3。

## ISE

配置：

```fish
set -x ISE_APP_ID "..."
set -x ISE_API_KEY "..."
set -x ISE_API_SECRET "..."
set -x ISE_WS_URL "wss://your-authorized-ise-endpoint/path"
```

评测已有音频：

```fish
ets-demo ise output/hello.mp3 "Hello world" --xml-output output/result.xml
```

完整流程使用真实 ISE 结果：

```fish
ets-demo run examples/homework.json --evaluator ise
```

音频会转换为 16 kHz、单声道、16-bit PCM，添加前导静音并以 10240 字节分块发送。

只生成本地评分 XML：

```fish
ets-demo score-xml "Hello world" \
  --score 4.8 \
  --accuracy 4.7 \
  --output output/score.xml
```

## 保留接口

接口客户端位于 `src/ets_demo/interface.py`，保留以下路径：

```text
/g/homework/list
/g/homework/struct
/g/homework/checkstart
/g/set/ppr-res
/m/file/pre-upload
/system/oss-config
/m/audio/sync-v2
/m/homework/set-use-time
/g/homework/report-score
/g/homework/score-drop
/g/homework/pk-info
/g/pk/start-word-pk
/g/pk/submit-word
/g/homework/score-sync
/m/exam/sync
/m/exam/submit
/m/composition/save-draft
/m/composition/mark
/m/homework/submit
```

没有默认域名。启动内置 mock server：

```fish
set -x API_BASE_URL http://127.0.0.1:8765
set -x LOCAL_API_PID local-demo
set -x LOCAL_API_SECRET local-demo

ets-demo mock-server examples/homework.json --port 8765
```

在另一个终端调用：

```fish
source /home/shuaiqiu/.venvs/study/bin/activate.fish
cd /home/shuaiqiu/Projects/ets_auto_complete
set -x API_BASE_URL http://127.0.0.1:8765

ets-demo interface-list
ets-demo interface-struct demo-english-001
ets-demo run examples/homework.json --send-interface --output-dir output/interface-run
```

四类本地分支也能发送给同一个 mock server：

```fish
ets-demo run examples/pk_homework.json --send-interface --target-total 85
ets-demo run examples/vocab_homework.json --send-interface --target-total 90
ets-demo run examples/mock_homework.json --send-interface
ets-demo run examples/composition_homework.json --send-interface
```

调用未封装但使用相同 envelope 的本地接口：

```fish
ets-demo interface-call \
  /g/homework/checkstart \
  g/homework/checkstart \
  --params '{"token":"local-session","homework_id":"demo-english-001"}'
```

`--send-interface` 会执行完整接口序列：开始、PPR 映射、每题两个预上传 ID、逐题
`sync-v2`、设置用时和最终汇总。mock server 将所有调用保存在进程内存中，不向外转发。

## 测试

```fish
python -m pytest
```

测试覆盖签名 envelope、Base64 解码、三状态列表合并、ZIP 密码、题目
解析、评分 XML、分数换算、ISE 鉴权、Edge/HTTP/克隆 TTS 适配和所有本地 workflow
分支。

## 结构

```text
src/ets_demo/
├── cli.py          CLI
├── config.py       本地接口、声音克隆和 ISE 配置
├── interface.py    保留的接口契约和签名协议
├── mock_server.py  本机 mock interface server
├── models.py       本地作业和题目模型
├── homework.py     ZIP 与题型解析
├── audio.py        Edge TTS、声音克隆、ffmpeg
├── ise.py          ISE WebSocket
├── pk.py           PK/词汇答案构造
├── scoring.py      分数换算、score_detail 和 XML
└── workflow.py     完整八阶段本地处理流程
```
