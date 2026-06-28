# 树莓派 4B 智能就医导向 Web 原型

这是一个面向社区就医咨询场景的轻量 Web 原型。用户输入自然语言症状描述后，系统调用 DeepSeek API 解析症状，查询本地 Neo4j 医疗知识图谱，并输出可能病名、建议药物和推荐科室。

> 提醒：本项目只做就医导向演示，不提供确诊或处方。

## 功能

- 单页 Web 交互界面，适合树莓派触屏或浏览器演示
- `POST /api/consult` 咨询接口
- DeepSeek 症状解析和答案组织
- Neo4j 本地知识图谱检索
- 内置最小示例知识库兜底，便于没有 Neo4j 时快速演示

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

在树莓派 Linux 上：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`：

```env
DEEPSEEK_API_KEY=你的 DeepSeek Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
NEO4J_TIMEOUT=2
USE_SAMPLE_FALLBACK=true
```

## 导入 Neo4j 示例数据

先启动 Neo4j，然后执行：

```bash
python import_data.py
```

导入脚本使用 `MERGE` 和唯一约束，可重复运行。

## 启动

```bash
python app.py
```

或：

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

浏览器访问：

```text
http://localhost:8000
```

树莓派同一局域网设备可访问：

```text
http://树莓派IP:8000
```

按当前树莓派地址访问：

```text
http://100.88.185.67:8000
```

## 接口示例

```bash
curl -X POST http://localhost:8000/api/consult \
  -H "Content-Type: application/json" \
  -d '{"query":"发烧、咳嗽、嗓子痛两天"}'
```

返回结构：

```json
{
  "diseases": ["普通感冒"],
  "medicines": ["对乙酰氨基酚"],
  "departments": ["全科", "呼吸内科"],
  "advice": "根据本地知识库匹配...",
  "matched_evidence": [],
  "symptoms": ["发热", "咳嗽", "咽痛"],
  "warnings": []
}
```

## 测试

```bash
pytest
```

## ReSpeaker 语音服务

本项目已适配 ReSpeaker 2-Mics Pi HAT v2。树莓派 Ubuntu 20.04 的设备树没有 Raspberry Pi OS 新版里的 `i2s_clk_consumer` 标签，因此不能直接照搬官方 overlay；本机使用了兼容 Ubuntu 5.4 内核的 `respeaker-2mic-v2_0-ubuntu54.dtbo`，实际启用位置为：

```text
/boot/firmware/usercfg.txt
```

常用检查命令：

```bash
arecord -l
aplay -l
systemctl status airag-web airag-voice neo4j
curl http://127.0.0.1:8000/api/voice/status
```

当前语音服务配置在 `.env`：

```env
VOICE_INPUT_DEVICE=1
ASR_MODEL_PATH=models/vosk-model-small-cn-0.22
WAKE_WORD=小医小医
WAKE_WORDS=小医小医,你好医生,开始问诊
TTS_ENGINE=espeak-ng
```

语音服务流程：等待热词，录制问诊语音，本地 Vosk 中文识别，调用 `/api/consult`，再用本地 TTS 播报结果。

## Google Health / Fitbit API 申请与配置

本项目通过 Google Health / Fitbit 相关 OAuth 数据读取心率、睡眠等健康指标。真实 token 文件只允许保存在本地或树莓派运行环境中，不要提交到 GitHub。

申请流程：

1. 进入 [Google Cloud Console](https://console.cloud.google.com/) 创建或选择一个项目。
2. 在 `APIs & Services` 中启用健康数据相关 API，并确认 Fitbit/Google Health 数据访问权限符合你的账号和设备绑定状态。
3. 在 `OAuth consent screen` 配置应用名称、测试用户和需要的健康数据 scope。开发阶段建议使用测试发布状态，只添加自己的 Google 账号为测试用户。
4. 在 `Credentials` 中创建 `OAuth Client ID`，应用类型选择 `Web application`。
5. 添加回调地址：

```text
http://127.0.0.1:8765/callback
```

6. 将获得的 `Client ID` 和 `Client Secret` 写入本机 `.env`：

```env
HEALTH_ENABLE=true
HEALTH_PROVIDER=google_health
GOOGLE_HEALTH_CLIENT_ID=你的 Client ID
GOOGLE_HEALTH_CLIENT_SECRET=你的 Client Secret
GOOGLE_HEALTH_REDIRECT_URI=http://127.0.0.1:8765/callback
GOOGLE_HEALTH_TOKEN_PATH=data/google_health_token.json
GOOGLE_HEALTH_LOOKBACK_DAYS=2
```

7. 运行 OAuth 初始化脚本，按浏览器提示授权：

```powershell
python scripts/google_health_oauth_setup.py
```

授权完成后会生成：

```text
data/google_health_token.json
```

该文件包含 OAuth access token / refresh token，已被 `.gitignore` 排除。若需要重新授权，可以删除本地 token 文件后重新运行 OAuth 初始化脚本。

树莓派无法稳定直连 Google 服务时，可以让树莓派通过电脑端代理获取健康指标，在树莓派 `.env` 中配置：

```env
HEALTH_PROXY_CONSULT_URL=http://电脑端TailscaleIP:8000/api/consult
```

## 开源医疗数据集导入

项目可导入 Hugging Face 上 MIT 许可证的 `nlp-guild/medical-data` 数据集。该数据集包含疾病、症状、科室、常用药、推荐药等字段。

```bash
cd /home/yy-notabot/AI_RAG
. .venv/bin/activate
python import_open_medical_dataset.py
```

导入脚本会分页下载数据到 `data/medical-data.jsonl`，然后用 `MERGE` 写入 Neo4j，可重复执行。

导入后系统会同时查询 `TREATED_BY` 和 `RECOMMENDED_DRUG` 两类药品关系。
