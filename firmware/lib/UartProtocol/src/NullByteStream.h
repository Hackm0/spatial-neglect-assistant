#pragma once

#include "IByteStream.h"

class NullByteStream : public IByteStream {
 public:
  static NullByteStream& instance() {
    static NullByteStream stream;
    return stream;
  }

  void begin(unsigned long baudRate) override {
    (void)baudRate;
  }

  int available() override {
    return 0;
  }

  int read() override {
    return -1;
  }

  size_t write(const uint8_t* data, size_t length) override {
    (void)data;
    return length;
  }

 private:
  NullByteStream() = default;
};
