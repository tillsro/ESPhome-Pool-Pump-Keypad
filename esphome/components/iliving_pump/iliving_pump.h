#pragma once

#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/number/number.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/switch/switch.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/components/uart/uart.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"
#include "iliving_protocol.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>

namespace esphome {
namespace iliving_pump {

class ILivingPump;

enum StartupMode : uint8_t {
  STARTUP_MODE_PASSIVE = 0,
  STARTUP_MODE_STOPPED = 1,
  STARTUP_MODE_RUNNING = 2,
};

class ILivingPumpRunSwitch : public switch_::Switch, public Parented<ILivingPump> {
 protected:
  void write_state(bool state) override;
};

class ILivingPumpDemandNumber : public number::Number, public Parented<ILivingPump> {
 protected:
  void control(float value) override;
};

class ILivingPump : public Component, public uart::UARTDevice {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;

  void set_poll_interval(uint32_t value) { this->poll_interval_ms_ = value; }
  void set_reply_timeout(uint32_t value) { this->reply_timeout_ms_ = value; }
  void set_offline_timeout(uint32_t value) { this->offline_timeout_ms_ = value; }
  void set_telemetry_interval(uint32_t value) { this->telemetry_interval_ms_ = value; }
  void set_startup_delay(uint32_t value) { this->startup_delay_ms_ = value; }
  void set_startup_mode(StartupMode value) { this->startup_mode_ = value; }
  void set_initial_target_rpm(uint16_t value) { this->demand_rpm_ = value; }
  void set_stop_on_communication_loss(bool value) { this->stop_on_communication_loss_ = value; }

  void set_run_switch(ILivingPumpRunSwitch *value) { this->run_switch_ = value; }
  void set_demand_rpm_number(ILivingPumpDemandNumber *value) { this->demand_rpm_number_ = value; }
  void set_actual_rpm_sensor(sensor::Sensor *value) { this->actual_rpm_sensor_ = value; }
  void set_accepted_rpm_sensor(sensor::Sensor *value) { this->accepted_rpm_sensor_ = value; }
  void set_fault_code_sensor(sensor::Sensor *value) { this->fault_code_sensor_ = value; }
  void set_valid_replies_sensor(sensor::Sensor *value) { this->valid_replies_sensor_ = value; }
  void set_missed_replies_sensor(sensor::Sensor *value) { this->missed_replies_sensor_ = value; }
  void set_discarded_bytes_sensor(sensor::Sensor *value) { this->discarded_bytes_sensor_ = value; }

  void set_online_binary_sensor(binary_sensor::BinarySensor *value) { this->online_binary_sensor_ = value; }
  void set_motor_running_binary_sensor(binary_sensor::BinarySensor *value) {
    this->motor_running_binary_sensor_ = value;
  }
  void set_control_active_binary_sensor(binary_sensor::BinarySensor *value) {
    this->control_active_binary_sensor_ = value;
  }
  void set_fault_binary_sensor(binary_sensor::BinarySensor *value) { this->fault_binary_sensor_ = value; }
  void set_status_text_sensor(text_sensor::TextSensor *value) { this->status_text_sensor_ = value; }
  void set_fault_text_text_sensor(text_sensor::TextSensor *value) { this->fault_text_sensor_ = value; }

  void set_run_command(bool run);
  void set_target_rpm(float rpm);

 protected:
  static constexpr size_t RX_BUFFER_SIZE = 128;
  static constexpr uint32_t DIAGNOSTIC_INTERVAL_MS = 5000;

  void drain_uart_();
  void parse_rx_buffer_();
  void discard_rx_prefix_(size_t count);
  void send_request_();
  void process_reply_(const std::array<uint8_t, protocol::REPLY_SIZE> &frame);
  void check_offline_(uint32_t now);
  void publish_online_(bool online);
  void publish_control_active_(bool active);
  void publish_status_(const std::string &status);
  void publish_diagnostics_(uint32_t now, bool force = false);
  void schedule_immediate_request_();
  uint16_t requested_value_() const;
  static const char *fault_description_(uint8_t code);

  uint32_t poll_interval_ms_{61};
  uint32_t reply_timeout_ms_{55};
  uint32_t offline_timeout_ms_{500};
  uint32_t telemetry_interval_ms_{500};
  uint32_t startup_delay_ms_{1000};
  StartupMode startup_mode_{STARTUP_MODE_PASSIVE};
  bool stop_on_communication_loss_{true};

  bool control_active_{false};
  bool run_requested_{false};
  bool online_{false};
  bool awaiting_reply_{false};
  bool ever_sent_{false};
  bool ever_received_{false};
  bool offline_declared_{false};
  bool have_last_request_{false};
  bool have_published_telemetry_{false};

  uint8_t next_sequence_{1};
  uint8_t outstanding_sequence_{0};
  uint8_t fault_code_{0};
  uint16_t demand_rpm_{1800};
  uint16_t accepted_value_{0};
  uint16_t actual_value_{0};
  uint16_t last_published_accepted_value_{0};

  uint32_t setup_ms_{0};
  uint32_t first_tx_ms_{0};
  uint32_t last_tx_ms_{0};
  uint32_t last_valid_reply_ms_{0};
  uint32_t last_telemetry_publish_ms_{0};
  uint32_t last_diagnostic_publish_ms_{0};
  uint32_t valid_reply_count_{0};
  uint32_t missed_reply_count_{0};
  uint32_t discarded_byte_count_{0};

  std::array<uint8_t, RX_BUFFER_SIZE> rx_buffer_{};
  size_t rx_length_{0};
  std::array<uint8_t, protocol::REQUEST_SIZE> last_request_{};
  std::string last_status_;

  ILivingPumpRunSwitch *run_switch_{nullptr};
  ILivingPumpDemandNumber *demand_rpm_number_{nullptr};
  sensor::Sensor *actual_rpm_sensor_{nullptr};
  sensor::Sensor *accepted_rpm_sensor_{nullptr};
  sensor::Sensor *fault_code_sensor_{nullptr};
  sensor::Sensor *valid_replies_sensor_{nullptr};
  sensor::Sensor *missed_replies_sensor_{nullptr};
  sensor::Sensor *discarded_bytes_sensor_{nullptr};
  binary_sensor::BinarySensor *online_binary_sensor_{nullptr};
  binary_sensor::BinarySensor *motor_running_binary_sensor_{nullptr};
  binary_sensor::BinarySensor *control_active_binary_sensor_{nullptr};
  binary_sensor::BinarySensor *fault_binary_sensor_{nullptr};
  text_sensor::TextSensor *status_text_sensor_{nullptr};
  text_sensor::TextSensor *fault_text_sensor_{nullptr};
};

}  // namespace iliving_pump
}  // namespace esphome
