import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import (
    binary_sensor,
    number,
    sensor,
    switch,
    text_sensor,
    uart,
)
from esphome.const import (
    CONF_ID,
    DEVICE_CLASS_CONNECTIVITY,
    DEVICE_CLASS_PROBLEM,
    DEVICE_CLASS_RUNNING,
    ENTITY_CATEGORY_DIAGNOSTIC,
    ICON_COUNTER,
    ICON_GAUGE,
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_TOTAL_INCREASING,
    UNIT_REVOLUTIONS_PER_MINUTE,
)

DEPENDENCIES = ["uart"]
AUTO_LOAD = ["binary_sensor", "number", "sensor", "switch", "text_sensor"]
MULTI_CONF = True

iliving_pump_ns = cg.esphome_ns.namespace("iliving_pump")
ILivingPump = iliving_pump_ns.class_("ILivingPump", cg.Component, uart.UARTDevice)
ILivingPumpRunSwitch = iliving_pump_ns.class_(
    "ILivingPumpRunSwitch", switch.Switch
)
ILivingPumpDemandNumber = iliving_pump_ns.class_(
    "ILivingPumpDemandNumber", number.Number
)
StartupMode = iliving_pump_ns.enum("StartupMode")

STARTUP_MODES = {
    "PASSIVE": StartupMode.STARTUP_MODE_PASSIVE,
    "STOPPED": StartupMode.STARTUP_MODE_STOPPED,
    "RUNNING": StartupMode.STARTUP_MODE_RUNNING,
}

CONF_POLL_INTERVAL = "poll_interval"
CONF_REPLY_TIMEOUT = "reply_timeout"
CONF_OFFLINE_TIMEOUT = "offline_timeout"
CONF_TELEMETRY_INTERVAL = "telemetry_interval"
CONF_STARTUP_DELAY = "startup_delay"
CONF_STARTUP_MODE = "startup_mode"
CONF_INITIAL_TARGET_RPM = "initial_target_rpm"
CONF_STOP_ON_COMMUNICATION_LOSS = "stop_on_communication_loss"

CONF_RUN = "run"
CONF_DEMAND_RPM = "demand_rpm"
CONF_ACTUAL_RPM = "actual_rpm"
CONF_ACCEPTED_RPM = "accepted_rpm"
CONF_ONLINE = "online"
CONF_MOTOR_RUNNING = "motor_running"
CONF_CONTROL_ACTIVE = "control_active"
CONF_FAULT = "fault"
CONF_FAULT_CODE = "fault_code"
CONF_STATUS = "status"
CONF_FAULT_TEXT = "fault_text"
CONF_VALID_REPLIES = "valid_replies"
CONF_MISSED_REPLIES = "missed_replies"
CONF_DISCARDED_BYTES = "discarded_bytes"


def _validate_timing(config):
    poll_ms = config[CONF_POLL_INTERVAL].total_milliseconds
    reply_ms = config[CONF_REPLY_TIMEOUT].total_milliseconds
    offline_ms = config[CONF_OFFLINE_TIMEOUT].total_milliseconds

    if reply_ms >= poll_ms:
        raise cv.Invalid("reply_timeout must be shorter than poll_interval")
    if offline_ms <= poll_ms:
        raise cv.Invalid("offline_timeout must be longer than poll_interval")
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(ILivingPump),
            cv.Optional(CONF_POLL_INTERVAL, default="61ms"): cv.All(
                cv.positive_time_period_milliseconds,
                cv.Range(min=cv.TimePeriod(milliseconds=40)),
            ),
            cv.Optional(CONF_REPLY_TIMEOUT, default="55ms"): cv.All(
                cv.positive_time_period_milliseconds,
                cv.Range(min=cv.TimePeriod(milliseconds=15)),
            ),
            cv.Optional(CONF_OFFLINE_TIMEOUT, default="500ms"):
                cv.positive_time_period_milliseconds,
            cv.Optional(CONF_TELEMETRY_INTERVAL, default="500ms"):
                cv.positive_time_period_milliseconds,
            cv.Optional(CONF_STARTUP_DELAY, default="1s"):
                cv.positive_time_period_milliseconds,
            cv.Optional(CONF_STARTUP_MODE, default="PASSIVE"): cv.enum(
                STARTUP_MODES, upper=True
            ),
            cv.Optional(CONF_INITIAL_TARGET_RPM, default=1800): cv.int_range(
                min=1000, max=3450
            ),
            cv.Optional(CONF_STOP_ON_COMMUNICATION_LOSS, default=True): cv.boolean,
            cv.Optional(CONF_RUN): switch.switch_schema(
                ILivingPumpRunSwitch,
                icon="mdi:pump",
                default_restore_mode="DISABLED",
            ),
            cv.Optional(CONF_DEMAND_RPM): number.number_schema(
                ILivingPumpDemandNumber,
                unit_of_measurement=UNIT_REVOLUTIONS_PER_MINUTE,
                icon=ICON_GAUGE,
            ),
            cv.Optional(CONF_ACTUAL_RPM): sensor.sensor_schema(
                unit_of_measurement=UNIT_REVOLUTIONS_PER_MINUTE,
                accuracy_decimals=0,
                icon=ICON_GAUGE,
                state_class=STATE_CLASS_MEASUREMENT,
            ),
            cv.Optional(CONF_ACCEPTED_RPM): sensor.sensor_schema(
                unit_of_measurement=UNIT_REVOLUTIONS_PER_MINUTE,
                accuracy_decimals=0,
                icon=ICON_GAUGE,
                state_class=STATE_CLASS_MEASUREMENT,
            ),
            cv.Optional(CONF_ONLINE): binary_sensor.binary_sensor_schema(
                device_class=DEVICE_CLASS_CONNECTIVITY,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_MOTOR_RUNNING): binary_sensor.binary_sensor_schema(
                device_class=DEVICE_CLASS_RUNNING,
            ),
            cv.Optional(CONF_CONTROL_ACTIVE): binary_sensor.binary_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_FAULT): binary_sensor.binary_sensor_schema(
                device_class=DEVICE_CLASS_PROBLEM,
            ),
            cv.Optional(CONF_FAULT_CODE): sensor.sensor_schema(
                accuracy_decimals=0,
                icon="mdi:alert-circle-outline",
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_STATUS): text_sensor.text_sensor_schema(
                icon="mdi:pump"
            ),
            cv.Optional(CONF_FAULT_TEXT): text_sensor.text_sensor_schema(
                icon="mdi:alert-circle-outline",
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_VALID_REPLIES): sensor.sensor_schema(
                accuracy_decimals=0,
                icon=ICON_COUNTER,
                state_class=STATE_CLASS_TOTAL_INCREASING,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_MISSED_REPLIES): sensor.sensor_schema(
                accuracy_decimals=0,
                icon=ICON_COUNTER,
                state_class=STATE_CLASS_TOTAL_INCREASING,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_DISCARDED_BYTES): sensor.sensor_schema(
                accuracy_decimals=0,
                icon=ICON_COUNTER,
                state_class=STATE_CLASS_TOTAL_INCREASING,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
        }
    )
    .extend(uart.UART_DEVICE_SCHEMA)
    .extend(cv.COMPONENT_SCHEMA),
    _validate_timing,
)

FINAL_VALIDATE_SCHEMA = uart.final_validate_device_schema(
    "iliving_pump",
    baud_rate=38400,
    require_tx=True,
    require_rx=True,
    data_bits=8,
    parity="NONE",
    stop_bits=1,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await uart.register_uart_device(var, config)

    cg.add(var.set_poll_interval(config[CONF_POLL_INTERVAL].total_milliseconds))
    cg.add(var.set_reply_timeout(config[CONF_REPLY_TIMEOUT].total_milliseconds))
    cg.add(var.set_offline_timeout(config[CONF_OFFLINE_TIMEOUT].total_milliseconds))
    cg.add(
        var.set_telemetry_interval(
            config[CONF_TELEMETRY_INTERVAL].total_milliseconds
        )
    )
    cg.add(var.set_startup_delay(config[CONF_STARTUP_DELAY].total_milliseconds))
    cg.add(var.set_startup_mode(config[CONF_STARTUP_MODE]))
    cg.add(var.set_initial_target_rpm(config[CONF_INITIAL_TARGET_RPM]))
    cg.add(
        var.set_stop_on_communication_loss(
            config[CONF_STOP_ON_COMMUNICATION_LOSS]
        )
    )

    if conf := config.get(CONF_RUN):
        entity = await switch.new_switch(conf)
        await cg.register_parented(entity, config[CONF_ID])
        cg.add(var.set_run_switch(entity))

    if conf := config.get(CONF_DEMAND_RPM):
        entity = await number.new_number(
            conf, min_value=1000, max_value=3450, step=50
        )
        await cg.register_parented(entity, config[CONF_ID])
        cg.add(var.set_demand_rpm_number(entity))

    for key in [
        CONF_ACTUAL_RPM,
        CONF_ACCEPTED_RPM,
        CONF_FAULT_CODE,
        CONF_VALID_REPLIES,
        CONF_MISSED_REPLIES,
        CONF_DISCARDED_BYTES,
    ]:
        if conf := config.get(key):
            entity = await sensor.new_sensor(conf)
            cg.add(getattr(var, f"set_{key}_sensor")(entity))

    for key in [
        CONF_ONLINE,
        CONF_MOTOR_RUNNING,
        CONF_CONTROL_ACTIVE,
        CONF_FAULT,
    ]:
        if conf := config.get(key):
            entity = await binary_sensor.new_binary_sensor(conf)
            cg.add(getattr(var, f"set_{key}_binary_sensor")(entity))

    for key in [CONF_STATUS, CONF_FAULT_TEXT]:
        if conf := config.get(key):
            entity = await text_sensor.new_text_sensor(conf)
            cg.add(getattr(var, f"set_{key}_text_sensor")(entity))
