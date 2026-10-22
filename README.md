# vocal-clean

一键把歌曲里的人声分离出来（去伴奏 / 去噪声），并统一音量、归一化响度。

## 功能

1. **人声分离** —— 基于 [demucs](https://github.com/adefossez/demucs)（`htdemucs` 模型），把人声从音乐、伴奏、环境噪声中分离出来
2. **音量统一** —— 把比「典型响度」更响的段落压到同一水平（只降不升，避免放大噪声）
3. **响度归一化** —— 整体归一化到目标 LUFS（默认 `-16`，符合流媒体标准）

## 依赖

- Python 3.10+
- [demucs](https://github.com/adefossez/demucs)（含 PyTorch）
- numpy
- [ffmpeg](https://ffmpeg.org)（命令行）

## 安装

```bash
# 1. 安装 ffmpeg
brew install ffmpeg   # macOS

# 2. 创建虚拟环境并安装依赖
python3 -m venv .venv
.venv/bin/pip install demucs numpy
```

## 用法

```bash
# 完整流程：分离 + 统一 + 归一化
.venv/bin/python vocal_clean.py 歌.mp3 -o 歌_人声.m4a

# 只做分离 + 音量统一，跳过响度归一化（保留原始整体响度）
.venv/bin/python vocal_clean.py 歌.mp3 -o 歌_人声.m4a --no-norm

# 已有人声轨，跳过分离，统一音量并截掉 55s 之后
.venv/bin/python vocal_clean.py 人声.wav --skip-separate --trim 55 -o 人声_统一.m4a
```

### 参数

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `-o, --out` | 输出文件路径 | `<输入>_人声.m4a` |
| `--model` | demucs 模型 | `htdemucs` |
| `--skip-separate` | 输入已是人声轨，跳过分离 | — |
| `--trim` | 只保留前 N 秒 | — |
| `--target-db` | 音量统一响度上限 (dB) | 自动 |
| `--lufs` | 最终归一化响度 | `-16` |
| `--no-norm` | 跳过响度归一化 | — |
| `--keep` | 保留中间产物 | — |

## 示例

`examples/` 目录下是两个真实录音（在青海湖边休息区的一家汉堡店户外录制，背景风声较大），
分别给出了原始文件和处理后的人声：

**▶ [在线试听（原始 vs 人声）](https://chen3feng.github.io/vocal-clean/)**

| 原始 | 处理后（人声） |
| --- | --- |
| [`派乐汉堡rap.m4a`](examples/派乐汉堡rap.m4a) | [`派乐汉堡rap_人声.m4a`](examples/派乐汉堡rap_人声.m4a) |
| [`汉堡.m4a`](examples/汉堡.m4a) | [`汉堡_人声.m4a`](examples/汉堡_人声.m4a) |

复现示例（第一个例子结尾混入了另一首歌，用 `--trim 55` 截掉）：

```bash
.venv/bin/python vocal_clean.py examples/派乐汉堡rap.m4a -o out.m4a --trim 55
.venv/bin/python vocal_clean.py examples/汉堡.m4a -o out2.m4a
```

## 说明

- 分离后会同时输出无损母版 `<输出>.wav`，方便二次编辑
- 「音量统一」只降不升：把过响的段落压到统一水平，不放大静音 / 底噪
