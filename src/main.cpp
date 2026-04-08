#include <Arduino.h>
#include <driver/i2s.h>

#include <string.h>

namespace {

constexpr gpio_num_t kPdmClkPin = GPIO_NUM_11;
constexpr gpio_num_t kPdmDataPin = GPIO_NUM_10;
constexpr i2s_port_t kI2SPort = I2S_NUM_0;

constexpr uint32_t kSerialBaudRate = 921600;
constexpr uint32_t kSampleRate = 16000;
constexpr uint16_t kBitsPerSample = 16;
constexpr uint16_t kChannels = 1;
constexpr size_t kReadBufferSamples = 256;

constexpr int kDmaBufferCount = 4;
constexpr int kDmaBufferLength = 256;

constexpr uint32_t kPacketMagic = 0x48435542;  // "HCUB"
constexpr uint8_t kProtocolVersion = 1;
constexpr size_t kMaxRxPayloadSize = 64;
constexpr size_t kRxBufferSize = 128;

enum PacketType : uint8_t {
    kPacketPing = 0x01,
    kPacketPong = 0x02,
    kPacketStart = 0x03,
    kPacketStartAck = 0x04,
    kPacketStop = 0x05,
    kPacketStopAck = 0x06,
    kPacketAudio = 0x10,
    kPacketError = 0x7F,
};

struct __attribute__((packed)) PacketHeader {
    uint32_t magic;
    uint8_t version;
    uint8_t type;
    uint16_t length;
    uint32_t sequence;
};

struct __attribute__((packed)) StreamFormatPayload {
    uint32_t sample_rate;
    uint16_t bits_per_sample;
    uint16_t channels;
    uint32_t frame_samples;
};

struct __attribute__((packed)) PongPayload {
    uint32_t uptime_ms;
};

struct __attribute__((packed)) StopAckPayload {
    uint32_t frames_sent;
    uint32_t samples_sent;
};

int16_t g_audioBuffer[kReadBufferSamples];

uint8_t g_rxBuffer[kRxBufferSize];
size_t g_rxBufferLen = 0;

bool g_streaming = false;
bool g_micReady = false;
uint32_t g_audioSequence = 0;
uint32_t g_audioFramesSent = 0;
uint32_t g_audioSamplesSent = 0;

bool sendPacket(PacketType type, uint32_t sequence, const void *payload, uint16_t payloadLength) {
    PacketHeader header{
        .magic = kPacketMagic,
        .version = kProtocolVersion,
        .type = type,
        .length = payloadLength,
        .sequence = sequence,
    };

    const size_t headerWritten = Serial.write(reinterpret_cast<const uint8_t *>(&header), sizeof(header));
    if (headerWritten != sizeof(header)) {
        return false;
    }

    if (payloadLength == 0) {
        return true;
    }

    const size_t payloadWritten =
        Serial.write(reinterpret_cast<const uint8_t *>(payload), payloadLength);
    return payloadWritten == payloadLength;
}

void sendError(uint32_t sequence, const char *message) {
    const size_t messageLength = strnlen(message, kMaxRxPayloadSize);
    sendPacket(kPacketError, sequence, message, static_cast<uint16_t>(messageLength));
}

bool initPdmMic() {
    i2s_config_t i2sConfig = {
        .mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM),
        .sample_rate = static_cast<int>(kSampleRate),
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = kDmaBufferCount,
        .dma_buf_len = kDmaBufferLength,
        .use_apll = false,
        .tx_desc_auto_clear = false,
        .fixed_mclk = 0,
    };

    esp_err_t err = i2s_driver_install(kI2SPort, &i2sConfig, 0, nullptr);
    if (err != ESP_OK) {
        return false;
    }

    i2s_pin_config_t pinConfig = {
        .bck_io_num = I2S_PIN_NO_CHANGE,
        .ws_io_num = static_cast<int>(kPdmClkPin),
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num = static_cast<int>(kPdmDataPin),
    };

    err = i2s_set_pin(kI2SPort, &pinConfig);
    if (err != ESP_OK) {
        return false;
    }

    i2s_zero_dma_buffer(kI2SPort);
    return true;
}

int readPdmSamples(int16_t *buffer, size_t maxSamples) {
    size_t bytesRead = 0;
    const esp_err_t err = i2s_read(
        kI2SPort,
        buffer,
        maxSamples * sizeof(int16_t),
        &bytesRead,
        pdMS_TO_TICKS(50));
    if (err != ESP_OK) {
        return 0;
    }
    return static_cast<int>(bytesRead / sizeof(int16_t));
}

void startStreaming(uint32_t sequence) {
    if (!g_micReady) {
        sendError(sequence, "microphone init failed");
        return;
    }

    if (g_streaming) {
        sendError(sequence, "stream already started");
        return;
    }

    g_streaming = true;
    g_audioSequence = 0;
    g_audioFramesSent = 0;
    g_audioSamplesSent = 0;

    const StreamFormatPayload payload{
        .sample_rate = kSampleRate,
        .bits_per_sample = kBitsPerSample,
        .channels = kChannels,
        .frame_samples = static_cast<uint32_t>(kReadBufferSamples),
    };
    sendPacket(kPacketStartAck, sequence, &payload, sizeof(payload));
}

void stopStreaming(uint32_t sequence) {
    g_streaming = false;
    const StopAckPayload payload{
        .frames_sent = g_audioFramesSent,
        .samples_sent = g_audioSamplesSent,
    };
    sendPacket(kPacketStopAck, sequence, &payload, sizeof(payload));
}

void handlePacket(const PacketHeader &header, const uint8_t *payload) {
    switch (header.type) {
        case kPacketPing: {
            const PongPayload pong{
                .uptime_ms = millis(),
            };
            sendPacket(kPacketPong, header.sequence, &pong, sizeof(pong));
            break;
        }

        case kPacketStart:
            if (header.length != 0) {
                sendError(header.sequence, "start payload must be empty");
                break;
            }
            startStreaming(header.sequence);
            break;

        case kPacketStop:
            if (header.length != 0) {
                sendError(header.sequence, "stop payload must be empty");
                break;
            }
            stopStreaming(header.sequence);
            break;

        default:
            (void)payload;
            sendError(header.sequence, "unsupported packet type");
            break;
    }
}

void serviceSerialRx() {
    while (Serial.available() > 0 && g_rxBufferLen < sizeof(g_rxBuffer)) {
        g_rxBuffer[g_rxBufferLen++] = static_cast<uint8_t>(Serial.read());
    }

    if (Serial.available() > 0 && g_rxBufferLen == sizeof(g_rxBuffer)) {
        g_rxBufferLen = 0;
        sendError(0, "rx buffer overflow");
        return;
    }

    size_t consumed = 0;
    while (g_rxBufferLen - consumed >= sizeof(PacketHeader)) {
        PacketHeader header{};
        memcpy(&header, g_rxBuffer + consumed, sizeof(header));

        if (header.magic != kPacketMagic || header.version != kProtocolVersion) {
            consumed += 1;
            continue;
        }

        if (header.length > kMaxRxPayloadSize) {
            sendError(header.sequence, "payload too large");
            consumed += 1;
            continue;
        }

        const size_t packetLength = sizeof(PacketHeader) + header.length;
        if (g_rxBufferLen - consumed < packetLength) {
            break;
        }

        handlePacket(header, g_rxBuffer + consumed + sizeof(PacketHeader));
        consumed += packetLength;
    }

    if (consumed == 0) {
        return;
    }

    const size_t remaining = g_rxBufferLen - consumed;
    if (remaining > 0) {
        memmove(g_rxBuffer, g_rxBuffer + consumed, remaining);
    }
    g_rxBufferLen = remaining;
}

void streamAudioFrame() {
    const int samplesRead = readPdmSamples(g_audioBuffer, kReadBufferSamples);
    if (samplesRead <= 0) {
        return;
    }

    const uint16_t payloadLength = static_cast<uint16_t>(samplesRead * sizeof(int16_t));
    if (!sendPacket(kPacketAudio, g_audioSequence, g_audioBuffer, payloadLength)) {
        g_streaming = false;
        return;
    }

    g_audioSequence += 1;
    g_audioFramesSent += 1;
    g_audioSamplesSent += static_cast<uint32_t>(samplesRead);
}

}  // namespace

void setup() {
    Serial.begin(kSerialBaudRate);
    g_micReady = initPdmMic();
}

void loop() {
    serviceSerialRx();

    if (!g_streaming) {
        delay(1);
        return;
    }

    streamAudioFrame();
}
