#include "LedScaleController.h"

namespace {

constexpr int16_t kMinimumPermille = -1000;
constexpr int16_t kMaximumPermille = 1000;
constexpr long kScaleSpanPermille = 2000L;

}  // namespace

LedScaleController::LedScaleController(const LedScaleConfig& config)
    : config_(config), activeLedCount_(0U), initialized_(false) {}

void LedScaleController::begin() {
  if (config_.pins == nullptr || config_.pinCount == 0U) {
    initialized_ = true;
    activeLedCount_ = 0U;
    return;
  }

  for (uint8_t i = 0U; i < config_.pinCount; ++i) {
    pinMode(config_.pins[i], OUTPUT);
  }

  initialized_ = true;
  activeLedCount_ = 0U;
  applyOutputs();
}

void LedScaleController::setScalePermille(const int16_t valuePermille) {
  const uint8_t requestedCount = computeActiveLedCount(valuePermille);
  if (requestedCount == activeLedCount_) {
    return;
  }

  activeLedCount_ = requestedCount;
  applyOutputs();
}

uint8_t LedScaleController::activeLedCount() const {
  return activeLedCount_;
}

int16_t LedScaleController::clampPermille(const int16_t value) {
  if (value < kMinimumPermille) {
    return kMinimumPermille;
  }

  if (value > kMaximumPermille) {
    return kMaximumPermille;
  }

  return value;
}

uint8_t LedScaleController::computeActiveLedCount(const int16_t valuePermille) const {
  if (config_.pinCount == 0U) {
    return 0U;
  }

  const long shifted = static_cast<long>(clampPermille(valuePermille) - kMinimumPermille);

  // Convert 0..2000 permille into 0..pinCount with nearest-integer rounding.
  const long rounded = shifted * static_cast<long>(config_.pinCount) + (kScaleSpanPermille / 2L);
  const long activeCount = rounded / kScaleSpanPermille;

  if (activeCount <= 0L) {
    return 0U;
  }

  if (activeCount >= static_cast<long>(config_.pinCount)) {
    return config_.pinCount;
  }

  return static_cast<uint8_t>(activeCount);
}

uint8_t LedScaleController::outputLevelForState(const bool enabled) const {
  const bool shouldDriveHigh = config_.activeHigh ? enabled : !enabled;
  return shouldDriveHigh ? HIGH : LOW;
}

void LedScaleController::applyOutputs() {
  if (!initialized_ || config_.pins == nullptr) {
    return;
  }

  for (uint8_t i = 0U; i < config_.pinCount; ++i) {
    const bool enabled = i < activeLedCount_;
    digitalWrite(config_.pins[i], outputLevelForState(enabled));
  }
}
