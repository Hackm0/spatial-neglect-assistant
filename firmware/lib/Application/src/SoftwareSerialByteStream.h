#pragma once

#include <SoftwareSerial.h>

#include "IByteStream.h"

class SoftwareSerialByteStream : public IByteStream {
 public:
  explicit SoftwareSerialByteStream(SoftwareSerial& serial,
                                    const int8_t rxIdlePin = -1,
                                    const unsigned long* candidateBaudRates =
                                        nullptr,
                                    const size_t candidateBaudRateCount = 0U,
                                    const unsigned long baudScanIntervalMs = 0UL)
      : serial_(serial),
        rxIdlePin_(rxIdlePin),
        candidateBaudRates_(candidateBaudRates),
        candidateBaudRateCount_(candidateBaudRateCount),
        baudScanIntervalMs_(baudScanIntervalMs),
        currentBaudRate_(0UL),
        currentCandidateIndex_(0U),
        scanningEnabled_(false),
        lastBaudSwitchMs_(0UL) {}

  void begin(const unsigned long baudRate) override {
    currentCandidateIndex_ = findCandidateIndex(baudRate);
    applyBaudRate(baudRate);
    scanningEnabled_ =
        candidateBaudRates_ != nullptr && candidateBaudRateCount_ > 1U &&
        baudScanIntervalMs_ > 0UL;
    lastBaudSwitchMs_ = millis();
  }

  int available() override {
    serial_.listen();
    return serial_.available();
  }

  int read() override {
    serial_.listen();
    return serial_.read();
  }

  size_t write(const uint8_t* data, const size_t length) override {
    return serial_.write(data, length);
  }

  void setBaudScanEnabled(const bool enabled, const unsigned long nowMs) {
    const bool canScan =
        candidateBaudRates_ != nullptr && candidateBaudRateCount_ > 1U &&
        baudScanIntervalMs_ > 0UL;
    const bool nextEnabled = canScan && enabled;
    if (scanningEnabled_ == nextEnabled) {
      return;
    }

    scanningEnabled_ = nextEnabled;
    lastBaudSwitchMs_ = nowMs;
  }

  bool updateBaudScan(const unsigned long nowMs) {
    if (!scanningEnabled_ || candidateBaudRates_ == nullptr ||
        candidateBaudRateCount_ <= 1U || baudScanIntervalMs_ == 0UL) {
      return false;
    }

    if ((nowMs - lastBaudSwitchMs_) < baudScanIntervalMs_) {
      return false;
    }

    currentCandidateIndex_ =
        (currentCandidateIndex_ + 1U) % candidateBaudRateCount_;
    applyBaudRate(candidateBaudRates_[currentCandidateIndex_]);
    lastBaudSwitchMs_ = nowMs;
    return true;
  }

  unsigned long currentBaudRate() const {
    return currentBaudRate_;
  }

 private:
  void applyBaudRate(const unsigned long baudRate) {
    serial_.end();
    serial_.begin(baudRate);
    if (rxIdlePin_ >= 0) {
      pinMode(rxIdlePin_, INPUT_PULLUP);
    }
    serial_.listen();
    while (serial_.available() > 0) {
      static_cast<void>(serial_.read());
    }
    currentBaudRate_ = baudRate;
  }

  size_t findCandidateIndex(const unsigned long baudRate) const {
    if (candidateBaudRates_ == nullptr || candidateBaudRateCount_ == 0U) {
      return 0U;
    }

    for (size_t index = 0U; index < candidateBaudRateCount_; ++index) {
      if (candidateBaudRates_[index] == baudRate) {
        return index;
      }
    }

    return 0U;
  }

  SoftwareSerial& serial_;
  int8_t rxIdlePin_;
  const unsigned long* candidateBaudRates_;
  size_t candidateBaudRateCount_;
  unsigned long baudScanIntervalMs_;
  unsigned long currentBaudRate_;
  size_t currentCandidateIndex_;
  bool scanningEnabled_;
  unsigned long lastBaudSwitchMs_;
};
