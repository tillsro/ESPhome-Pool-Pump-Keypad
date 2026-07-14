#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace esphome {
namespace iliving_pump {
namespace protocol {

constexpr size_t REQUEST_SIZE = 14;
constexpr size_t REPLY_SIZE = 38;
constexpr uint16_t MAX_PROTOCOL_VALUE = 6000;
constexpr uint16_t MAX_RPM = 3450;

constexpr uint16_t modbus_crc16(const uint8_t *data, size_t count) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < count; i++) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc & 0x0001U) != 0U ? static_cast<uint16_t>((crc >> 1U) ^ 0xA001U)
                                   : static_cast<uint16_t>(crc >> 1U);
    }
  }
  return crc;
}

constexpr uint16_t rpm_to_value(uint16_t rpm) {
  if (rpm == 0)
    return 0;
  if (rpm >= MAX_RPM)
    return MAX_PROTOCOL_VALUE;
  return static_cast<uint16_t>((static_cast<uint32_t>(rpm) * MAX_PROTOCOL_VALUE) / MAX_RPM);
}

constexpr float value_to_rpm(uint16_t value) {
  return static_cast<float>(value) * static_cast<float>(MAX_RPM) /
         static_cast<float>(MAX_PROTOCOL_VALUE);
}

constexpr uint16_t read_be16(const uint8_t *data) {
  return static_cast<uint16_t>((static_cast<uint16_t>(data[0]) << 8U) | data[1]);
}

constexpr std::array<uint8_t, REQUEST_SIZE> build_request(uint8_t sequence, uint8_t status,
                                                          uint16_t value) {
  std::array<uint8_t, REQUEST_SIZE> frame{};
  frame[0] = 0x01;
  frame[1] = 0x70;
  frame[2] = sequence;
  frame[3] = status;
  frame[4] = 0x00;
  frame[5] = 0x0C;
  frame[6] = 0x00;
  frame[7] = 0x00;
  frame[8] = static_cast<uint8_t>(value >> 8U);
  frame[9] = static_cast<uint8_t>(value & 0xFFU);

  uint16_t sum = 0;
  for (size_t i = 0; i < 10; i++)
    sum = static_cast<uint16_t>(sum + frame[i]);
  frame[10] = static_cast<uint8_t>(sum & 0xFFU);
  frame[11] = static_cast<uint8_t>(sum >> 8U);

  const uint16_t crc = modbus_crc16(frame.data(), 12);
  frame[12] = static_cast<uint8_t>(crc & 0xFFU);
  frame[13] = static_cast<uint8_t>(crc >> 8U);
  return frame;
}

constexpr bool validate_reply(const std::array<uint8_t, REPLY_SIZE> &frame) {
  if (frame[0] != 0x01 || frame[1] != 0x70 || frame[4] != 0x00 || frame[5] != 0x0C)
    return false;

  uint16_t sum = 0;
  for (size_t i = 0; i < 34; i++)
    sum = static_cast<uint16_t>(sum + frame[i]);
  const uint16_t wire_sum = static_cast<uint16_t>(frame[34] | (frame[35] << 8U));
  if (sum != wire_sum)
    return false;

  const uint16_t crc = modbus_crc16(frame.data(), 36);
  const uint16_t wire_crc = static_cast<uint16_t>(frame[36] | (frame[37] << 8U));
  return crc == wire_crc;
}

}  // namespace protocol
}  // namespace iliving_pump
}  // namespace esphome
