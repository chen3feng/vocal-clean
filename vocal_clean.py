#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用脚本：人声分离（去伴奏/噪声）+ 音量统一 + 响度归一化

流程：
  1. 分离   —— 用 demucs(htdemucs) 把人声从音乐/伴奏/噪声里分离出来
  2. 统一   —— 把比“典型响度”更响的段落压到同一水平（只降不升，避免放大噪声）
  3. 归一化 —— 整体归一化到目标 LUFS（默认 -16）

依赖：
  - demucs（含 torch）、numpy —— 请用装有它们的 python 运行本脚本
  - ffmpeg（命令行）

用法：
  python3 vocal_clean.py <输入音频> [选项]

  --out PATH        输出文件路径（默认：<输入>_人声.m4a）
  --model NAME      demucs 模型（默认 htdemucs）
  --skip-separate   输入已经是人声轨，跳过第 1 步分离
  --trim 秒         只保留前 N 秒（裁掉结尾的静音/多余片段）
  --target-db X     音量统一的响度上限 dB（默认自动=典型响度+3dB）
  --lufs X          最终归一化目标响度（默认 -16）
  --keep            保留中间产物（人声 wav / 电平后 wav）

示例：
  # 完整流程：分离 + 统一 + 归一化
  python3 vocal_clean.py 歌.mp3 -o 歌_人声.m4a

  # 已有人声轨，跳过分离，统一音量并截掉 55s 之后
  python3 vocal_clean.py 人声.wav --skip-separate --trim 55 -o 人声_统一.m4a
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np


def run(cmd):
    """运行命令，失败即报错。"""
    print("+", " ".join(cmd), file=sys.stderr)
    subprocess.run(cmd, check=True)


def run_capture(cmd):
    """运行命令并返回 (stdout, stderr)。"""
    print("+", " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd, capture_output=True, text=True)


def ffmpeg():
    return shutil.which("ffmpeg")


def separate(input_path, outdir, model):
    """demucs 两轨人声分离，返回 vocals.wav 路径。"""
    run([
        sys.executable, "-m", "demucs.separate",
        "--two-stems", "vocals", "-n", model,
        "-o", outdir, input_path,
    ])
    # 输出位置：<outdir>/<model>/<文件名去扩展名>/vocals.wav
    stem = os.path.splitext(os.path.basename(input_path))[0]
    vocals = os.path.join(outdir, model, stem, "vocals.wav")
    if not os.path.exists(vocals):
        raise FileNotFoundError(f"未找到分离结果: {vocals}")
    return vocals


def trim(wav_in, wav_out, seconds):
    """截取前 seconds 秒。"""
    run([ffmpeg(), "-y", "-i", wav_in, "-t", str(seconds),
         "-c:a", "pcm_s16le", wav_out])


def _rms_db_per_window(mono, sr, win, hop):
    """返回 (窗口中心样本下标, 各窗口 RMS dB)。"""
    win_n = int(win * sr)
    hop_n = int(hop * sr)
    centers, dbs = [], []
    i = 0
    while i + win_n <= len(mono):
        seg = mono[i:i + win_n]
        rms = float(np.sqrt(np.mean(seg ** 2)))
        dbs.append(20.0 * np.log10(rms + 1e-9))
        centers.append(i + win_n // 2)
        i += hop_n
    return np.array(centers, dtype=np.float64), np.array(dbs)


def level(wav_in, wav_out, target_db=None, win=0.2, hop=0.1,
          smooth_win=0.5, max_cut_db=14.0, silence_db=-60.0):
    """
    音量统一：只把响度超过 target_db 的段落压下来（上限削平），
    不提升静音/弱音，用平滑包络避免跳变。
    target_db 为 None 时自动取“非静音窗口响度中位数 + 3dB”。
    """
    import wave
    w = wave.open(wav_in, "rb")
    sr = w.getframerate()
    nch = w.getnchannels()
    sw = w.getsampwidth()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    w.close()
    x = x.astype(np.float32) / 32768.0
    x = x.reshape(-1, nch)
    mono = x.mean(axis=1)
    N = len(mono)

    centers, dbs = _rms_db_per_window(mono, sr, win, hop)

    if target_db is None:
        voice = dbs[dbs > silence_db]           # 排除静音
        if len(voice) == 0:
            voice = dbs
        target_db = float(np.median(voice)) + 3.0
    print(f"[level] 响度上限 target_db = {target_db:.1f} dB", file=sys.stderr)

    # 需要施加的增益：越响越要降（负值）；只降不升
    gain = np.clip(target_db - dbs, -max_cut_db, 0.0)

    # 平滑（约 smooth_win 秒）
    smooth_n = max(1, int(smooth_win / hop))
    k = np.ones(smooth_n) / smooth_n
    gain = np.convolve(gain, k, mode="same")
    gain[:smooth_n] = gain[smooth_n]
    gain[-smooth_n:] = gain[-smooth_n - 1]

    lin = np.interp(np.arange(N), centers, gain)
    lin = 10.0 ** (lin / 20.0)
    out = np.clip(x * lin[:, None], -1.0, 1.0)
    y = (out * 32767.0).astype(np.int16)

    w2 = wave.open(wav_out, "wb")
    w2.setnchannels(nch)
    w2.setsampwidth(sw)
    w2.setframerate(sr)
    w2.writeframes(y.tobytes())
    w2.close()


def _loudnorm_measure(wav_in, lufs):
    """loudnorm 第一遍：测量，返回 JSON 参数。"""
    p = run_capture([
        ffmpeg(), "-i", wav_in,
        "-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ])
    txt = p.stderr
    i = txt.rfind("{")
    j = txt.rfind("}")
    if i == -1 or j == -1 or j <= i:
        raise RuntimeError(f"loudnorm 测量失败:\n{txt}")
    return json.loads(txt[i:j + 1])


def normalize(wav_in, out_wav, out_m4a, lufs):
    """loudnorm 两遍：整体归一化到目标 LUFS，输出 wav + m4a。"""
    m = _loudnorm_measure(wav_in, lufs)
    filt = (
        f"loudnorm=I={lufs}:TP=-1.5:LRA=11"
        f":measured_I={m['input_i']}"
        f":measured_TP={m['input_tp']}"
        f":measured_LRA={m['input_lra']}"
        f":measured_thresh={m['input_thresh']}"
        f":offset={m['target_offset']}:linear=true"
    )
    run([ffmpeg(), "-y", "-i", wav_in, "-af", filt,
         "-ar", "44100", "-c:a", "pcm_s16le", out_wav])
    run([ffmpeg(), "-y", "-i", out_wav,
         "-c:a", "aac", "-b:a", "256k", "-ar", "44100", out_m4a])


def main():
    ap = argparse.ArgumentParser(
        description="人声分离 + 音量统一 + 响度归一化（通用脚本）")
    ap.add_argument("input", help="输入音频文件")
    ap.add_argument("-o", "--out", help="输出 m4a 路径")
    ap.add_argument("--model", default="htdemucs", help="demucs 模型")
    ap.add_argument("--skip-separate", action="store_true",
                    help="输入已经是人声轨，跳过分离")
    ap.add_argument("--trim", type=float, default=None,
                    help="只保留前 N 秒")
    ap.add_argument("--target-db", type=float, default=None,
                    help="音量统一响度上限 dB（默认自动）")
    ap.add_argument("--lufs", type=float, default=-16.0,
                    help="最终归一化响度")
    ap.add_argument("--keep", action="store_true", help="保留中间产物")
    args = ap.parse_args()

    if not ffmpeg():
        sys.exit("未找到 ffmpeg，请先安装")

    if args.out is None:
        args.out = os.path.splitext(args.input)[0] + "_人声.m4a"
    out_abs = os.path.abspath(args.out)
    outdir = os.path.dirname(out_abs)
    base = os.path.splitext(os.path.basename(args.out))[0]
    os.makedirs(outdir, exist_ok=True)

    work = tempfile.mkdtemp(prefix="vocal_clean_")
    try:
        # 1. 分离（或直接用输入）
        if args.skip_separate:
            vocals = os.path.abspath(args.input)
        else:
            vocals = separate(os.path.abspath(args.input), work, args.model)

        # 2. 可选截断
        cur = vocals
        if args.trim is not None:
            trimmed = os.path.join(work, "trimmed.wav")
            trim(cur, trimmed, args.trim)
            cur = trimmed

        # 3. 音量统一
        leveled = os.path.join(work, "leveled.wav")
        level(cur, leveled, target_db=args.target_db)

        # 4. 响度归一化
        out_wav = os.path.join(outdir, base + ".wav")
        normalize(leveled, out_wav, out_abs, args.lufs)

        if args.keep:
            keep_dir = os.path.join(outdir, base + "_work")
            os.makedirs(keep_dir, exist_ok=True)
            shutil.copy(vocals, os.path.join(keep_dir, "vocals.wav"))
            shutil.copy(leveled, os.path.join(keep_dir, "leveled.wav"))

        print(f"\n完成：\n  {out_abs}\n  {out_wav}")
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
