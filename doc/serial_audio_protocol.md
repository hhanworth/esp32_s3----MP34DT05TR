# USB CDC 音频录音协议

本项目当前使用单一二进制包协议在 `ESP32-S3` 与 `macOS` 主机之间传输控制命令和音频数据。

## 1. 串口参数

- 设备端口：`USB CDC`
- 建议波特率：`921600`
- 音频格式：`PCM 16 kHz / 16-bit / Mono`

## 2. 包头格式

每个包都由固定长度包头加可选负载组成，均使用小端序。

```text
offset  size  field
0       4     magic      = 0x48435542 ("HCUB")
4       1     version    = 1
5       1     type
6       2     length
8       4     sequence
12      N     payload
```

## 3. 包类型

- `0x01 PING`
  - 方向：`Host -> ESP32`
  - 负载：空
- `0x02 PONG`
  - 方向：`ESP32 -> Host`
  - 负载：`uint32 uptime_ms`
- `0x03 START`
  - 方向：`Host -> ESP32`
  - 负载：空
- `0x04 START_ACK`
  - 方向：`ESP32 -> Host`
  - 负载格式：
    - `uint32 sample_rate`
    - `uint16 bits_per_sample`
    - `uint16 channels`
    - `uint32 frame_samples`
- `0x05 STOP`
  - 方向：`Host -> ESP32`
  - 负载：空
- `0x06 STOP_ACK`
  - 方向：`ESP32 -> Host`
  - 负载格式：
    - `uint32 frames_sent`
    - `uint32 samples_sent`
- `0x10 AUDIO`
  - 方向：`ESP32 -> Host`
  - 负载：原始 `PCM16LE` 音频数据
- `0x7F ERROR`
  - 方向：双向保留，当前由 `ESP32 -> Host`
  - 负载：ASCII 错误消息

## 4. 推荐时序

1. `Host` 打开串口并等待设备枚举完成。
2. `Host` 发送 `PING`。
3. `ESP32` 返回 `PONG`。
4. `Host` 发送 `START`。
5. `ESP32` 返回 `START_ACK`。
6. `ESP32` 连续发送 `AUDIO`。
7. `Host` 结束录音时发送 `STOP`。
8. `ESP32` 返回 `STOP_ACK`。

## 5. 主机脚本

Python 录音脚本位于：

- [host/serial_audio_recorder.py](/Volumes/GVE-1T/workspace/PIO_PROJECT/PlatformIO/Projects/HoloCubic/host/serial_audio_recorder.py)

运行前安装依赖：

```bash
python3 -m pip install pyserial
```

示例：

```bash
python3 host/serial_audio_recorder.py --list-ports
python3 host/serial_audio_recorder.py --port /dev/cu.usbmodem101 --duration 10
```
