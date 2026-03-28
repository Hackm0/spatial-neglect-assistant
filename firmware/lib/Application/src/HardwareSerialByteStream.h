#pragma once

#include <Arduino.h>

#include "IByteStream.h"

class HardwareSerialByteStream : public IByteStream {
 public:
  explicit HardwareSerialByteStream(HardwareSerial& serial) : serial_(serial) {}

  void begin(const unsigned long baudRate) override {
    serial_.begin(baudRate);
  }

  int available() override {
    return serial_.available();
  }

  int read() override {
    return serial_.read();
  }

  size_t write(const uint8_t* data, const size_t length) override {
    return serial_.write(data, length);
  }

 private:
  HardwareSerial& serial_;
};
