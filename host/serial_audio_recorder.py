#!/usr/bin/env python3

from __future__ import annotations

import argparse
import enum
import pathlib
import struct
import sys
import time
import wave
from dataclasses import dataclass
from typing import Callable, Optional

import serial
from serial.tools import list_ports


PACKET_MAGIC = 0x48435542  # "HCUB"
PROTOCOL_VERSION = 1
HEADER_STRUCT = struct.Struct("<IBBHI")
STREAM_FORMAT_STRUCT = struct.Struct("<IHHI")
PONG_STRUCT = struct.Struct("<I")
STOP_ACK_STRUCT = struct.Struct("<II")
MAX_PACKET_LENGTH = 4096


class PacketType(enum.IntEnum):
    PING = 0x01
    PONG = 0x02
    START = 0x03
    START_ACK = 0x04
    STOP = 0x05
    STOP_ACK = 0x06
    AUDIO = 0x10
    ERROR = 0x7F


@dataclass
class Packet:
    packet_type: PacketType
    sequence: int
    payload: bytes


@dataclass
class StreamFormat:
    sample_rate: int
    bits_per_sample: int
    channels: int
    frame_samples: int


@dataclass
class StopAck:
    frames_sent: int
    samples_sent: int


class ProtocolError(RuntimeError):
    pass


class SerialAudioClient:
    def __init__(self, serial_port: serial.Serial) -> None:
        self.serial = serial_port
        self._rx_buffer = bytearray()
        self._next_sequence = 1

    def initialize(self, reset_wait: float) -> None:
        if reset_wait > 0:
            time.sleep(reset_wait)
        self.serial.reset_input_buffer()
        self.serial.reset_output_buffer()

    def send_packet(self, packet_type: PacketType, payload: bytes = b"") -> int:
        sequence = self._next_sequence
        self._next_sequence += 1
        header = HEADER_STRUCT.pack(
            PACKET_MAGIC,
            PROTOCOL_VERSION,
            packet_type,
            len(payload),
            sequence,
        )
        self.serial.write(header)
        if payload:
            self.serial.write(payload)
        self.serial.flush()
        return sequence

    def read_packet(self, timeout: float) -> Packet:
        deadline = time.monotonic() + timeout
        magic_prefix = struct.pack("<I", PACKET_MAGIC)

        while True:
            while len(self._rx_buffer) >= HEADER_STRUCT.size:
                magic_index = self._rx_buffer.find(magic_prefix)
                if magic_index == -1:
                    del self._rx_buffer[:-3]
                    break
                if magic_index > 0:
                    del self._rx_buffer[:magic_index]
                if len(self._rx_buffer) < HEADER_STRUCT.size:
                    break

                magic, version, packet_type, length, sequence = HEADER_STRUCT.unpack(
                    self._rx_buffer[: HEADER_STRUCT.size]
                )
                if magic != PACKET_MAGIC:
                    del self._rx_buffer[0]
                    continue
                if version != PROTOCOL_VERSION:
                    del self._rx_buffer[0]
                    continue
                if length > MAX_PACKET_LENGTH:
                    del self._rx_buffer[0]
                    continue

                packet_size = HEADER_STRUCT.size + length
                if len(self._rx_buffer) < packet_size:
                    break

                payload = bytes(self._rx_buffer[HEADER_STRUCT.size:packet_size])
                del self._rx_buffer[:packet_size]

                try:
                    packet_type_enum = PacketType(packet_type)
                except ValueError as exc:
                    raise ProtocolError(f"unknown packet type: 0x{packet_type:02x}") from exc

                return Packet(packet_type_enum, sequence, payload)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out while waiting for packet")

            chunk = self.serial.read(max(1, self.serial.in_waiting or 1))
            if chunk:
                self._rx_buffer.extend(chunk)
                continue

    def wait_for_response(
        self,
        expected_type: PacketType,
        expected_sequence: int,
        timeout: float,
        on_audio: Optional[Callable[[bytes], None]] = None,
    ) -> Packet:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out while waiting for {expected_type.name}")

            packet = self.read_packet(remaining)
            if packet.packet_type == PacketType.ERROR:
                message = packet.payload.decode("utf-8", errors="replace")
                raise ProtocolError(f"device error: {message}")

            if packet.packet_type == PacketType.AUDIO:
                if on_audio is not None:
                    on_audio(packet.payload)
                continue

            if packet.packet_type == expected_type and packet.sequence == expected_sequence:
                return packet

    def ping(self, timeout: float) -> int:
        sequence = self.send_packet(PacketType.PING)
        packet = self.wait_for_response(PacketType.PONG, sequence, timeout)
        if len(packet.payload) != PONG_STRUCT.size:
            raise ProtocolError("invalid pong payload length")
        (uptime_ms,) = PONG_STRUCT.unpack(packet.payload)
        return uptime_ms

    def start(self, timeout: float) -> StreamFormat:
        sequence = self.send_packet(PacketType.START)
        packet = self.wait_for_response(PacketType.START_ACK, sequence, timeout)
        if len(packet.payload) != STREAM_FORMAT_STRUCT.size:
            raise ProtocolError("invalid start ack payload length")
        sample_rate, bits_per_sample, channels, frame_samples = STREAM_FORMAT_STRUCT.unpack(
            packet.payload
        )
        return StreamFormat(sample_rate, bits_per_sample, channels, frame_samples)

    def stop(
        self,
        timeout: float,
        on_audio: Optional[Callable[[bytes], None]] = None,
    ) -> StopAck:
        sequence = self.send_packet(PacketType.STOP)
        packet = self.wait_for_response(PacketType.STOP_ACK, sequence, timeout, on_audio=on_audio)
        if len(packet.payload) != STOP_ACK_STRUCT.size:
            raise ProtocolError("invalid stop ack payload length")
        frames_sent, samples_sent = STOP_ACK_STRUCT.unpack(packet.payload)
        return StopAck(frames_sent, samples_sent)


def default_output_path() -> pathlib.Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return pathlib.Path.cwd() / f"recording-{timestamp}.wav"


def detect_serial_port() -> str:
    ports = list(list_ports.comports())
    if not ports:
        raise RuntimeError("no serial ports found")

    preferred_prefixes = ("/dev/cu.usbmodem", "/dev/cu.usbserial")
    for prefix in preferred_prefixes:
        for port in ports:
            if port.device.startswith(prefix):
                return port.device

    for port in ports:
        if "USB" in (port.description or "").upper():
            return port.device

    raise RuntimeError("unable to auto-detect ESP32 serial port, please pass --port")


def list_available_ports() -> None:
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return

    for port in ports:
        print(f"{port.device}\t{port.description}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Receive PCM audio from ESP32-S3 over USB CDC and save it as WAV."
    )
    parser.add_argument("--port", help="serial port, for example /dev/cu.usbmodem101")
    parser.add_argument("--baudrate", type=int, default=921600, help="serial baudrate")
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=default_output_path(),
        help="output WAV file path",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=5.0,
        help="recording duration in seconds, 0 means until Ctrl+C",
    )
    parser.add_argument(
        "--packet-timeout",
        type=float,
        default=2.0,
        help="timeout when waiting for packets in seconds",
    )
    parser.add_argument(
        "--handshake-timeout",
        type=float,
        default=3.0,
        help="timeout for ping/start/stop handshake in seconds",
    )
    parser.add_argument(
        "--reset-wait",
        type=float,
        default=2.0,
        help="wait after opening serial port to let the device enumerate/reset",
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="list available serial ports and exit",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.list_ports:
        list_available_ports()
        return 0

    port = args.port or detect_serial_port()
    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total_audio_bytes = 0

    try:
        with serial.Serial(
            port=port,
            baudrate=args.baudrate,
            timeout=0.05,
            write_timeout=1.0,
        ) as serial_port:
            client = SerialAudioClient(serial_port)
            client.initialize(args.reset_wait)

            uptime_ms = client.ping(args.handshake_timeout)
            print(f"Connected to {port}, device uptime {uptime_ms} ms")

            stream_format = client.start(args.handshake_timeout)
            if stream_format.bits_per_sample != 16:
                raise ProtocolError(
                    f"unsupported sample width: {stream_format.bits_per_sample} bits"
                )

            print(
                "Recording "
                f"{stream_format.sample_rate} Hz, "
                f"{stream_format.bits_per_sample}-bit, "
                f"{stream_format.channels} channel(s) "
                f"to {output_path}"
            )

            start_time = time.monotonic()
            last_report_time = start_time

            with wave.open(str(output_path), "wb") as wav_file:
                wav_file.setnchannels(stream_format.channels)
                wav_file.setsampwidth(stream_format.bits_per_sample // 8)
                wav_file.setframerate(stream_format.sample_rate)

                def write_audio(payload: bytes) -> None:
                    nonlocal total_audio_bytes
                    wav_file.writeframesraw(payload)
                    total_audio_bytes += len(payload)

                try:
                    while True:
                        if args.duration > 0 and time.monotonic() - start_time >= args.duration:
                            break

                        packet = client.read_packet(args.packet_timeout)
                        if packet.packet_type == PacketType.ERROR:
                            message = packet.payload.decode("utf-8", errors="replace")
                            raise ProtocolError(f"device error: {message}")

                        if packet.packet_type == PacketType.AUDIO:
                            write_audio(packet.payload)

                        now = time.monotonic()
                        if now - last_report_time >= 1.0:
                            seconds = total_audio_bytes / (
                                stream_format.sample_rate
                                * stream_format.channels
                                * (stream_format.bits_per_sample // 8)
                            )
                            print(f"Captured {seconds:.2f} s audio...", flush=True)
                            last_report_time = now
                except KeyboardInterrupt:
                    print("Stopping recording...", file=sys.stderr)

                stop_ack = client.stop(args.handshake_timeout, on_audio=write_audio)

            recorded_samples = total_audio_bytes // (stream_format.bits_per_sample // 8)
            recorded_seconds = recorded_samples / (
                stream_format.sample_rate * stream_format.channels
            )
            print(
                "Saved "
                f"{recorded_seconds:.2f} s audio to {output_path} "
                f"(device frames={stop_ack.frames_sent}, samples={stop_ack.samples_sent})"
            )
    except (OSError, RuntimeError, ProtocolError, TimeoutError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
