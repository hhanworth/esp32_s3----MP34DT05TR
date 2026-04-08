# HoloCubic 整板测试与烧写说明

## 1. 原理图确认出的器件

这块板基于 `ESP32-S3-PICO-1-N8R8`，原理图里能直接确认的器件如下：

- 原生 USB Type-C，`GPIO19/20` 直连 USB D+/D-
- `TPS62162` 5V 转 3.3V 电源
- `WS2812B` 单颗 RGB 灯，`DIN -> GPIO6`
- `MPU6050` IMU，I2C：`SDA -> GPIO15`，`SCL -> GPIO16`
- TF 卡座，SPI：`MISO -> GPIO7`，`MOSI -> GPIO13`，`CLK -> GPIO14`，`CS -> GPIO18`
- `MP34DT05` 数字麦克风，PDM：`DOUT -> GPIO10`，`CLK -> GPIO11`
- LCD FPC 接口：
  - `MOSI -> GPIO38`
  - `SCK -> GPIO17`
  - `DC -> GPIO2`
  - `RES -> GPIO4`
  - `BL -> GPIO5`
- `GPIO0` 被引出为下载/启动测试点

说明：

- LCD 在原理图中是外接接口，不是板上固定器件，所以固件只能测试控制脚是否能翻转，不能在没有屏幕模组时验证显示功能。
- 电源芯片和 USB 物理层无法仅靠固件百分百判定好坏，最好配合万用表做一次静态电压确认。

## 2. 现在工程里做了什么修改

已经加入：

- `platformio.ini`
  - `monitor_speed = 115200`
  - `upload_speed = 460800`
  - `-DARDUINO_USB_CDC_ON_BOOT=1`
- `src/main.cpp`
  - 自动整板测试程序
  - 串口命令式复测入口
  - 关键注释

这样做的目的，是让这块没有独立 USB 转串口芯片的自制板，直接通过原生 USB 输出测试日志。

## 3. 首次上电前建议

先别急着烧录，先做这几项：

1. 目检
   - 确认 Type-C 口焊接无连锡
   - 确认 `ESP32-S3-PICO-1-N8R8` 无偏移、虚焊
   - 确认 `MPU6050`、`MP34DT05`、TF 卡座、RGB 灯方向正确
   - 确认天线匹配区域没有短路、缺件

2. 冷态阻值检查
   - `3V3` 对 `GND` 不应接近短路
   - `VBUS_5V` 对 `GND` 不应接近短路

3. 首次插 USB 供电时
   - 使用数据线，不要用纯充电线
   - 观察是否有异常发热
   - 用万用表测 `3V3` 是否稳定在约 `3.3V`

如果这一步不正常，不建议直接反复烧写。

## 4. 详细烧写步骤

### 4.1 在 VSCode / PlatformIO 中打开工程

工程根目录就是当前这个 PlatformIO Project。

确认 `platformio.ini` 里环境是：

```ini
[env:esp32-s3-pico-1-n8r8]
platform = espressif32
board = esp32-s3-pico-1-n8r8
framework = arduino
monitor_speed = 115200
upload_speed = 460800
build_flags =
  -DARDUINO_USB_CDC_ON_BOOT=1
```

说明：

- 这里使用的是项目内自定义板卡文件 `boards/esp32-s3-pico-1-n8r8.json`
- `8 MB Flash` 已在板卡文件里通过 `flash_size = 8MB` 和 `maximum_size = 8388608` 固定
- `8 MB PSRAM` 不需要单独写一个 `psram_size = 8MB` 之类的字段；对 Arduino + PlatformIO，关键是：
  - `memory_type = qio_opi`
  - `-DBOARD_HAS_PSRAM`
- `maximum_ram_size = 327680` 表示片上 SRAM 预算，不是外部 PSRAM 容量

### 4.2 让板子进入下载模式

因为你的板子原理图里没有独立 USB-UART 芯片，也没看到标准自动下载电路，所以最稳妥的做法是手动进下载模式。

操作顺序：

1. 断开 Type-C
2. 将 `GPIO0` 拉低到 `GND`
   - 如果你留了测试点，可以用镊子/杜邦线短接
3. 保持 `GPIO0` 为低，再插入 Type-C
4. 如果板上有复位点或 `CHIP_PU` 可控，也可以在 `GPIO0` 低电平时做一次复位
5. 等电脑识别到 ESP32-S3 下载口后，再开始上传

如果上传已经开始跑了，再松开 `GPIO0` 即可。

### 4.3 在 VSCode 里上传

两种方式都可以：

- 点 PlatformIO 底部状态栏的 `Upload`
- 或在终端执行：

```bash
pio run -t upload
```

如果上传端口识别不稳定：

- 先重新插拔 USB
- 再次手动让 `GPIO0` 进下载模式
- 然后重新上传

### 4.4 打开串口监视器

上传完成后，打开串口监视器：

```bash
pio device monitor -b 115200
```

或者直接用 VSCode 的 `Monitor`。

你应该看到类似以下流程：

- 板卡信息
- 引脚映射
- 自动整板测试
- 周期性输出 IMU / 麦克风实时数据

如果完全看不到日志，优先排查：

- USB 数据线
- `GPIO19/20` 走线
- `ARDUINO_USB_CDC_ON_BOOT=1` 是否生效
- 板子是否实际启动

## 5. 固件自动测试项

### 5.1 自动测试

开机后会自动跑这些项目：

- PSRAM 检测和读写校验
- RGB 灯颜色序列
- `MPU6050` 识别、唤醒、读取加速度/角速度
- TF 卡挂载、写文件、读回
- Wi-Fi 扫描
- 数字麦克风采样
- LCD 接口控制脚翻转

### 5.2 手工观察项

下面这些需要你肉眼或仪器确认：

- RGB 灯是否按顺序显示：红、绿、蓝、白、灭
- LCD 控制脚是否有波形
- 3.3V 电源是否稳定
- Type-C 是否稳定枚举

## 6. 串口命令

打开串口监视器后，可以输入单字符命令复测：

- `r`：重新跑全部测试
- `i`：单独测试 IMU
- `m`：单独测试麦克风
- `s`：单独测试 TF 卡
- `w`：重新扫描 Wi-Fi
- `p`：单独测试 PSRAM
- `l`：重新翻转 LCD 控制脚
- `h`：打印帮助

## 7. 各器件测试时你应该看到什么

### 7.1 RGB 灯

期望现象：

- 红
- 绿
- 蓝
- 白
- 熄灭

如果串口显示通过但灯不亮，重点查：

- `GPIO6`
- WS2812B 焊接方向
- 供电是否接到 `VBUS_5V`

### 7.2 MPU6050

串口里应能看到：

- `addr=0x68` 或 `0x69`
- `WHO_AM_I=0x68`
- 加速度和角速度实时变化

操作板子时，`[LIVE] IMU ...` 的数值应该明显变化。

### 7.3 TF 卡

建议插一张 FAT/FAT32 的 TF 卡再测。

期望现象：

- 能挂载
- 能创建 `/board_test.txt`
- 能读回刚写入的一行文本

如果失败，先确认：

- 卡已插好
- 供电正常
- `GPIO7/13/14/18` 无虚焊

### 7.4 Wi-Fi

期望现象：

- 能扫到附近 AP
- 串口显示若干 SSID 和 RSSI

如果是空旷环境、屏蔽箱环境或附近确实没 AP，可能只会报 `WARN`，不一定是板坏。

### 7.5 数字麦克风 MP34DT05

期望现象：

- 串口输出 `min / max / p2p / rms`
- 对着麦克风说话、敲板子后，`p2p` 和 `rms` 会明显变大

如果一直很小或读失败，重点排查：

- `GPIO10`、`GPIO11`
- 麦克风方向
- `LR` 脚是否按原理图接地

### 7.6 LCD FPC 接口

当前程序会翻转：

- `GPIO38 MOSI`
- `GPIO17 SCK`
- `GPIO2 DC`
- `GPIO4 RES`
- `GPIO5 BL`

没有外接屏时，只能说明 MCU 侧在驱动这些脚；要验证显示本身，必须接上实际 LCD 模组。

## 8. 建议的量产/返修测试顺序

如果你后面要一块块测板，建议固定成这个顺序：

1. 冷态阻值
2. USB 供电
3. 3.3V 电压
4. 下载模式
5. 固件烧写
6. 串口日志
7. RGB 灯
8. IMU
9. TF 卡
10. 麦克风
11. Wi-Fi
12. LCD 接口波形

这样最省时间，也最容易定位是电源、主控、射频还是外设焊接问题。
