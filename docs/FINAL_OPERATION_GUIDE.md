# 智能就医向导最终操作指南

## 1. GitHub 新建仓库选项

创建 GitHub 仓库时：

- `.gitignore`：选择 `None / 不添加`
- README：可以不勾选，本项目已有 `README.md`
- License：按需要选择；如果只是个人展示，可先不选

原因：本项目已经维护了定制 `.gitignore`，会忽略 `.env`、模型、数据、OAuth token、语音 WAV、日志、release staging 等敏感或大体积文件。GitHub 自动模板可能与本地规则冲突。

## 2. 项目目录说明

核心目录：

- `app.py`：FastAPI 主问诊服务，包含 Graph RAG、DeepSeek 生成、健康数据融合。
- `voice_service.py`：树莓派 ReSpeaker 语音交互服务。
- `health_google.py`：Google Health / Fitbit 指标读取与健康建议融合。
- `asr_server/`：电脑端 Whisper-large 医疗 ASR FastAPI 服务。
- `static/`：网页前端。
- `systemd/`：树莓派 systemd 服务模板。
- `scripts/`：部署、启动、训练、OAuth、ReSpeaker 初始化脚本。
- `tests/`：基础接口测试。
- `release/20260628_105818/`：最终脱敏代码包。

最终 release：

- `release/20260628_105818/computer_code.zip`
- `release/20260628_105818/raspberry_pi_code.zip`

## 3. 环境变量

不要提交 `.env`。从 `.env.example` 复制并填写：

```powershell
Copy-Item .env.example .env
```

核心变量：

```env
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=
NEO4J_TIMEOUT=2
USE_SAMPLE_FALLBACK=true

ASR_ENGINE=remote_whisper
REMOTE_ASR_URL=http://COMPUTER_TAILSCALE_IP:9000/asr/transcribe
REMOTE_ASR_TIMEOUT=15
VOSK_FALLBACK=true

WHISPER_MODEL_PATH=models/whisper-large-v3-medical-best-checkpoint-200
WHISPER_MODEL_NAME=whisper-large-v3-medical-checkpoint-200
WHISPER_LANGUAGE=chinese
WHISPER_TASK=transcribe
WHISPER_DEVICE=auto
WHISPER_TORCH_DTYPE=auto
WHISPER_CHUNK_LENGTH_S=30
WHISPER_BATCH_SIZE=4

HEALTH_ENABLE=true
HEALTH_PROVIDER=google_health
GOOGLE_HEALTH_CLIENT_ID=
GOOGLE_HEALTH_CLIENT_SECRET=
GOOGLE_HEALTH_REDIRECT_URI=http://127.0.0.1:8765/callback
GOOGLE_HEALTH_TOKEN_PATH=data/google_health_token.json
GOOGLE_HEALTH_TIMEOUT=5
GOOGLE_HEALTH_LOOKBACK_DAYS=2
HEALTH_PROXY_CONSULT_URL=

VOICE_CONSULT_URL=http://127.0.0.1:8000/api/consult
VOICE_STATUS_PATH=voice_status.json
VOICE_INPUT_DEVICE=auto
VOICE_OUTPUT_DEVICE=default
ASR_MODEL_PATH=models/vosk-model-small-cn-0.22
VOICE_TRIGGER_MODE=button
VOICE_BUTTON_GPIO=17
VOICE_BUTTON_HOLD_SECONDS=0.15
TTS_ENGINE=espeak-ng
```

敏感文件禁止提交：

- `.env`
- `data/google_health_token.json`
- `models/`
- `data/`
- 语音 WAV
- 运行日志

## 4. 电脑端启动

安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r asr_server\requirements.txt
```

启动 Web：

```powershell
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

启动 Whisper ASR：

```powershell
.\scripts\start_local_whisper_asr.ps1
```

一键启动电脑端和树莓派端：

```powershell
.\scripts\start_all_services_windows.ps1
```

检查接口：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health/status
Invoke-RestMethod http://127.0.0.1:9000/asr/health
```

## 5. 树莓派端部署

树莓派项目路径：

```text
/home/yy-notabot/AI_RAG
```

安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-voice.txt
```

安装 systemd 服务：

```bash
sudo cp systemd/airag-web.service /etc/systemd/system/
sudo cp systemd/airag-voice.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable neo4j airag-web airag-voice
sudo systemctl restart neo4j airag-web airag-voice
```

检查：

```bash
systemctl status neo4j airag-web airag-voice
curl http://127.0.0.1:8000/api/voice/status
```

ReSpeaker 初始化参考：

```bash
bash scripts/init_respeaker_2mic_hat.sh
```

## 6. 业务逻辑

文本问诊：

1. 前端提交症状到 `/api/consult`。
2. DeepSeek 抽取症状。
3. Neo4j 执行 Graph RAG 检索，召回疾病、症状、药品、科室证据。
4. Google Health / Fitbit 模块补充心率和睡眠指标。
5. DeepSeek 基于证据生成导诊建议。
6. 前端展示建议、健康指标和安全提醒。

语音问诊：

1. ReSpeaker 按键录音。
2. 优先调用电脑端 Whisper-large 医疗 ASR。
3. 失败时回退树莓派本地 Vosk。
4. 调用树莓派本地 `/api/consult`。
5. TTS 播报问诊摘要。

安全边界：

- 不输出确诊结论。
- 不输出处方剂量。
- 药品仅为知识库关联提示。
- 手环数据只作辅助参考。
- 严重或持续症状提示线下就医。

## 7. 测试

```powershell
pytest -q
```

当前基础测试覆盖：

- 常见症状问诊。
- 空输入。
- 未命中症状。
- 健康状态接口。
- 健康指标融合。
- 语音状态文件缺失兜底。

## 8. 打包与安全审计

最终打包已排除：

- `.env`
- `project_memory.txt`
- `.venv/`
- `data/`
- `models/`
- `output_models/`
- `whisper_inference_results/`
- `__pycache__/`
- `.pytest_cache/`
- `*.pyc`
- `*.log`
- `*.wav`
- `voice_status.json`
- Google Health OAuth token 文件

最终 zip 扫描结果：

- `computer_code.zip`：未发现明文 API key、私钥、token 文件或模型/数据目录。
- `raspberry_pi_code.zip`：未发现明文 API key、私钥、token 文件或模型/数据目录。

## 9. GitHub 推送

本地已初始化 Git。添加远程仓库后推送：

```powershell
git remote add origin https://github.com/YOUR_NAME/YOUR_REPO.git
git push -u origin main
```

如果远程仓库已经有 README 或初始提交，需要先处理历史合并，建议新建空仓库。
