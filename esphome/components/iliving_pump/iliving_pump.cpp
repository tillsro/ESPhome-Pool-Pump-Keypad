#include "iliving_pump.h"

#include "esphome/core/log.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

namespace esphome {
namespace iliving_pump {

static const char *const TAG = "iliving_pump";

// These fixtures come from the Waveshare script and a pump-validated 1800 RPM reply.
constexpr auto SPEED1_REQUEST_FIXTURE = protocol::build_request(0x01, 0x00, 0x0C3A);
static_assert(SPEED1_REQUEST_FIXTURE[10] == 0xC4 && SPEED1_REQUEST_FIXTURE[11] == 0x00 &&
                  SPEED1_REQUEST_FIXTURE[12] == 0xDF && SPEED1_REQUEST_FIXTURE[13] == 0x58,
              "iLiving request checksum regression");
static_assert(protocol::rpm_to_value(1400) == 0x0982, "1400 RPM scaling regression");
static_assert(protocol::rpm_to_value(1800) == 0x0C3A, "1800 RPM scaling regression");
static_assert(protocol::rpm_to_value(2000) == 0x0D96, "2000 RPM scaling regression");
static_assert(protocol::rpm_to_value(3450) == 0x1770, "3450 RPM scaling regression");

constexpr std::array<uint8_t, protocol::REPLY_SIZE> SPEED1_REPLY_FIXTURE = {
    0x01, 0x70, 0x01, 0x00, 0x00, 0x0C, 0x00, 0x65, 0x00, 0x02,
    0x00, 0x65, 0x0C, 0x3A, 0x0C, 0x39, 0x0A, 0x7F, 0x00, 0xE4,
    0x0C, 0x9F, 0x01, 0x0D, 0x01, 0x90, 0x01, 0x5F, 0x00, 0x45,
    0x00, 0x00, 0x0C, 0x3A, 0x77, 0x05, 0x85, 0x76,
};
static_assert(protocol::validate_reply(SPEED1_REPLY_FIXTURE), "iLiving reply validation regression");

void ILivingPumpRunSwitch::write_state(bool state) { this->parent_->set_run_command(state); }

void ILivingPumpDemandNumber::control(float value) { this->parent_->set_target_rpm(value); }

void ILivingPump::setup() {
  this->setup_ms_ = millis();
  this->last_tx_ms_ = this->setup_ms_;

  uint8_t stale;
  while (this->available())
    this->read_byte(&stale);

  switch (this->startup_mode_) {
    case STARTUP_MODE_STOPPED:
      this->control_active_ = true;
      this->run_requested_ = false;
      this->publish_status_("INITIALIZING");
      break;
    case STARTUP_MODE_RUNNING:
      this->control_active_ = true;
      this->run_requested_ = true;
      this->publish_status_("INITIALIZING");
      break;
    case STARTUP_MODE_PASSIVE:
    default:
      this->control_active_ = false;
      this->run_requested_ = false;
      this->publish_status_("STANDBY");
      break;
  }

  if (this->demand_rpm_number_ != nullptr)
    this->demand_rpm_number_->publish_state(this->demand_rpm_);
  if (this->run_switch_ != nullptr)
    this->run_switch_->publish_state(this->run_requested_);
  if (this->online_binary_sensor_ != nullptr)
    this->online_binary_sensor_->publish_state(false);
  if (this->motor_running_binary_sensor_ != nullptr)
    this->motor_running_binary_sensor_->publish_state(false);
  if (this->fault_binary_sensor_ != nullptr)
    this->fault_binary_sensor_->publish_state(false);
  if (this->fault_code_sensor_ != nullptr)
    this->fault_code_sensor_->publish_state(0);
  if (this->fault_text_sensor_ != nullptr)
    this->fault_text_sensor_->publish_state("None");
  this->publish_control_active_(this->control_active_);
  this->publish_diagnostics_(this->setup_ms_, true);
}

void ILivingPump::loop() {
  this->drain_uart_();
  this->parse_rx_buffer_();

  const uint32_t now = millis();
  if (this->awaiting_reply_ && (now - this->last_tx_ms_) >= this->reply_timeout_ms_) {
    this->awaiting_reply_ = false;
    this->missed_reply_count_++;
  }

  this->check_offline_(now);
  this->publish_diagnostics_(now);

  if (!this->control_active_ || (now - this->setup_ms_) < this->startup_delay_ms_)
    return;
  if (!this->awaiting_reply_ && (now - this->last_tx_ms_) >= this->poll_interval_ms_)
    this->send_request_();
}

void ILivingPump::dump_config() {
  ESP_LOGCONFIG(TAG, "iLiving ILG8PP390-VS pump controller:");
  ESP_LOGCONFIG(TAG, "  Poll interval: %u ms", this->poll_interval_ms_);
  ESP_LOGCONFIG(TAG, "  Reply timeout: %u ms", this->reply_timeout_ms_);
  ESP_LOGCONFIG(TAG, "  Offline timeout: %u ms", this->offline_timeout_ms_);
  ESP_LOGCONFIG(TAG, "  Initial target: %u RPM", this->demand_rpm_);
  ESP_LOGCONFIG(TAG, "  Startup mode: %s",
                this->startup_mode_ == STARTUP_MODE_PASSIVE
                    ? "PASSIVE"
                    : (this->startup_mode_ == STARTUP_MODE_STOPPED ? "STOPPED" : "RUNNING"));
  ESP_LOGCONFIG(TAG, "  Stop on communication loss: %s", YESNO(this->stop_on_communication_loss_));
}

void ILivingPump::set_run_command(bool run) {
  if (run && this->fault_code_ != 0) {
    ESP_LOGW(TAG, "Start rejected while pump fault E%03u is active", this->fault_code_);
    if (this->run_switch_ != nullptr)
      this->run_switch_->publish_state(false);
    return;
  }

  this->control_active_ = true;
  this->run_requested_ = run;
  this->publish_control_active_(true);
  if (this->run_switch_ != nullptr)
    this->run_switch_->publish_state(run);
  this->schedule_immediate_request_();
}

void ILivingPump::set_target_rpm(float rpm) {
  const uint16_t clamped = static_cast<uint16_t>(
      std::lround(std::max(1000.0f, std::min(3450.0f, rpm))));
  this->demand_rpm_ = clamped;
  if (this->demand_rpm_number_ != nullptr)
    this->demand_rpm_number_->publish_state(clamped);
  if (this->control_active_ && this->run_requested_)
    this->schedule_immediate_request_();
}

void ILivingPump::drain_uart_() {
  uint8_t byte;
  while (this->available()) {
    if (!this->read_byte(&byte))
      break;
    if (this->rx_length_ == this->rx_buffer_.size()) {
      this->discard_rx_prefix_(1);
      this->discarded_byte_count_++;
    }
    this->rx_buffer_[this->rx_length_++] = byte;
  }
}

void ILivingPump::parse_rx_buffer_() {
  while (this->rx_length_ >= 2) {
    size_t header = 0;
    while (header + 1 < this->rx_length_ &&
           !(this->rx_buffer_[header] == 0x01 && this->rx_buffer_[header + 1] == 0x70))
      header++;

    if (header + 1 >= this->rx_length_) {
      const bool keep_last = this->rx_buffer_[this->rx_length_ - 1] == 0x01;
      const size_t discarded = this->rx_length_ - (keep_last ? 1 : 0);
      if (keep_last)
        this->rx_buffer_[0] = 0x01;
      this->rx_length_ = keep_last ? 1 : 0;
      this->discarded_byte_count_ += discarded;
      return;
    }

    if (header > 0) {
      this->discard_rx_prefix_(header);
      this->discarded_byte_count_ += header;
    }

    if (this->have_last_request_ && this->rx_length_ >= protocol::REQUEST_SIZE &&
        std::equal(this->last_request_.begin(), this->last_request_.end(), this->rx_buffer_.begin())) {
      this->discard_rx_prefix_(protocol::REQUEST_SIZE);
      continue;
    }

    if (this->rx_length_ < protocol::REPLY_SIZE)
      return;

    std::array<uint8_t, protocol::REPLY_SIZE> frame{};
    std::copy_n(this->rx_buffer_.begin(), protocol::REPLY_SIZE, frame.begin());
    if (protocol::validate_reply(frame)) {
      this->discard_rx_prefix_(protocol::REPLY_SIZE);
      this->process_reply_(frame);
    } else {
      this->discard_rx_prefix_(1);
      this->discarded_byte_count_++;
    }
  }
}

void ILivingPump::discard_rx_prefix_(size_t count) {
  if (count >= this->rx_length_) {
    this->rx_length_ = 0;
    return;
  }
  std::memmove(this->rx_buffer_.data(), this->rx_buffer_.data() + count, this->rx_length_ - count);
  this->rx_length_ -= count;
}

void ILivingPump::send_request_() {
  const uint16_t value = this->requested_value_();
  const uint8_t sequence = this->next_sequence_++;
  this->last_request_ = protocol::build_request(sequence, 0x00, value);
  this->have_last_request_ = true;
  this->outstanding_sequence_ = sequence;

  const uint32_t now = millis();
  this->last_tx_ms_ = now;
  if (!this->ever_sent_) {
    this->ever_sent_ = true;
    this->first_tx_ms_ = now;
    ESP_LOGI(TAG, "Starting pump polling with demand 0x%04X", value);
  }

  this->write_array(this->last_request_);
  this->flush();
  this->awaiting_reply_ = true;
}

void ILivingPump::process_reply_(const std::array<uint8_t, protocol::REPLY_SIZE> &frame) {
  const uint32_t now = millis();
  this->valid_reply_count_++;
  this->last_valid_reply_ms_ = now;
  this->ever_received_ = true;
  this->offline_declared_ = false;
  const bool communication_was_online = this->online_;
  this->publish_online_(true);
  if (!communication_was_online)
    ESP_LOGI(TAG, "Pump communication established");

  if (frame[2] == this->outstanding_sequence_) {
    this->awaiting_reply_ = false;
  } else {
    ESP_LOGW(TAG, "Valid reply sequence 0x%02X does not match outstanding 0x%02X", frame[2],
             this->outstanding_sequence_);
  }

  this->accepted_value_ = protocol::read_be16(&frame[12]);
  this->actual_value_ = protocol::read_be16(&frame[14]);
  const uint16_t echoed_request = protocol::read_be16(&frame[32]);
  const uint16_t expected_request = this->requested_value_();
  if (echoed_request != expected_request) {
    ESP_LOGV(TAG, "Pump request echo 0x%04X differs from current demand 0x%04X", echoed_request,
             expected_request);
  }

  const bool publish_telemetry =
      !this->have_published_telemetry_ ||
      this->accepted_value_ != this->last_published_accepted_value_ ||
      (now - this->last_telemetry_publish_ms_) >= this->telemetry_interval_ms_;
  if (publish_telemetry) {
    if (this->actual_rpm_sensor_ != nullptr)
      this->actual_rpm_sensor_->publish_state(protocol::value_to_rpm(this->actual_value_));
    if (this->accepted_rpm_sensor_ != nullptr)
      this->accepted_rpm_sensor_->publish_state(protocol::value_to_rpm(this->accepted_value_));
    this->last_published_accepted_value_ = this->accepted_value_;
    this->last_telemetry_publish_ms_ = now;
    this->have_published_telemetry_ = true;
  }

  if (this->motor_running_binary_sensor_ != nullptr)
    this->motor_running_binary_sensor_->publish_state(this->actual_value_ != 0);

  const uint8_t new_fault_code = frame[3];
  if (new_fault_code != this->fault_code_) {
    this->fault_code_ = new_fault_code;
    if (this->fault_code_sensor_ != nullptr)
      this->fault_code_sensor_->publish_state(this->fault_code_);
    if (this->fault_binary_sensor_ != nullptr)
      this->fault_binary_sensor_->publish_state(this->fault_code_ != 0);

    if (this->fault_text_sensor_ != nullptr) {
      if (this->fault_code_ == 0) {
        this->fault_text_sensor_->publish_state("None");
      } else {
        char fault[128];
        std::snprintf(fault, sizeof(fault), "E%03u: %s", this->fault_code_,
                      this->fault_description_(this->fault_code_));
        this->fault_text_sensor_->publish_state(fault);
      }
    }
  }

  if (this->fault_code_ != 0) {
    const bool was_running = this->run_requested_;
    this->run_requested_ = false;
    if (was_running && this->run_switch_ != nullptr)
      this->run_switch_->publish_state(false);

    char status[128];
    std::snprintf(status, sizeof(status), "E%03u: %s", this->fault_code_,
                  this->fault_description_(this->fault_code_));
    this->publish_status_(status);
    if (was_running)
      ESP_LOGE(TAG, "Pump fault E%03u; changing outgoing demand to STOP", this->fault_code_);
    return;
  }

  if (this->accepted_value_ == 0) {
    this->publish_status_(this->actual_value_ == 0 ? "STOPPED" : "STOPPING");
  } else {
    const int difference = std::abs(static_cast<int>(this->actual_value_) -
                                    static_cast<int>(this->accepted_value_));
    this->publish_status_(difference <= 3 ? "RUNNING" : "RAMPING");
  }
}

void ILivingPump::check_offline_(uint32_t now) {
  if (!this->control_active_ || !this->ever_sent_)
    return;
  const uint32_t reference = this->ever_received_ ? this->last_valid_reply_ms_ : this->first_tx_ms_;
  if ((now - reference) < this->offline_timeout_ms_ || this->offline_declared_)
    return;

  this->offline_declared_ = true;
  this->publish_online_(false);
  this->publish_status_("OFFLINE");
  ESP_LOGW(TAG, "Pump reply timeout: valid=%u missed=%u discarded_bytes=%u", this->valid_reply_count_,
           this->missed_reply_count_, this->discarded_byte_count_);
  if (this->stop_on_communication_loss_ && this->run_requested_) {
    ESP_LOGE(TAG, "Pump replies timed out; changing outgoing demand to STOP");
    this->run_requested_ = false;
    if (this->run_switch_ != nullptr)
      this->run_switch_->publish_state(false);
    this->schedule_immediate_request_();
  }
}

void ILivingPump::publish_online_(bool online) {
  if (this->online_ == online)
    return;
  this->online_ = online;
  if (this->online_binary_sensor_ != nullptr)
    this->online_binary_sensor_->publish_state(online);
}

void ILivingPump::publish_control_active_(bool active) {
  if (this->control_active_binary_sensor_ != nullptr)
    this->control_active_binary_sensor_->publish_state(active);
}

void ILivingPump::publish_status_(const std::string &status) {
  if (status == this->last_status_)
    return;
  this->last_status_ = status;
  if (this->status_text_sensor_ != nullptr)
    this->status_text_sensor_->publish_state(status);
}

void ILivingPump::publish_diagnostics_(uint32_t now, bool force) {
  if (!force && (now - this->last_diagnostic_publish_ms_) < DIAGNOSTIC_INTERVAL_MS)
    return;
  this->last_diagnostic_publish_ms_ = now;
  if (this->valid_replies_sensor_ != nullptr)
    this->valid_replies_sensor_->publish_state(this->valid_reply_count_);
  if (this->missed_replies_sensor_ != nullptr)
    this->missed_replies_sensor_->publish_state(this->missed_reply_count_);
  if (this->discarded_bytes_sensor_ != nullptr)
    this->discarded_bytes_sensor_->publish_state(this->discarded_byte_count_);
  if (this->ever_sent_)
    ESP_LOGD(TAG, "RS485 valid=%u missed=%u discarded=%u accepted=0x%04X actual=0x%04X",
             this->valid_reply_count_, this->missed_reply_count_, this->discarded_byte_count_,
             this->accepted_value_, this->actual_value_);
}

void ILivingPump::schedule_immediate_request_() {
  if (!this->ever_sent_)
    this->last_tx_ms_ = millis() - this->poll_interval_ms_;
}

uint16_t ILivingPump::requested_value_() const {
  return this->run_requested_ ? protocol::rpm_to_value(this->demand_rpm_) : 0;
}

const char *ILivingPump::fault_description_(uint8_t code) {
  switch (code) {
    case 1:
      return "IPM module failure";
    case 2:
      return "Output current exceeds limit";
    case 6:
      return "Input voltage too high";
    case 9:
      return "Input voltage too low";
    case 10:
      return "Inverter overload";
    case 11:
      return "Motor overload";
    case 13:
      return "Output phase loss or imbalance";
    case 14:
      return "Inverter overheating";
    case 18:
      return "Current sampling circuit failure";
    case 21:
      return "Display board EEPROM or connection failure";
    case 48:
      return "PFC overcurrent or PFC circuit failure";
    default:
      return "Unknown pump fault/status";
  }
}

}  // namespace iliving_pump
}  // namespace esphome
