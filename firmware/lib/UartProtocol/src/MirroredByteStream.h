#pragma once

#include "IByteStream.h"

class MirroredByteStream : public IByteStream {
 public:
  MirroredByteStream(IByteStream& primaryStream, IByteStream& secondaryStream)
      : primaryStream_(primaryStream), secondaryStream_(secondaryStream) {}

  void begin(const unsigned long baudRate) override {
    primaryStream_.begin(baudRate);
    secondaryStream_.begin(baudRate);
  }

  int available() override {
    return primaryStream_.available();
  }

  int read() override {
    return primaryStream_.read();
  }

  size_t write(const uint8_t* data, const size_t length) override {
    const size_t primaryWritten = primaryStream_.write(data, length);
    // Always attempt the secondary write so USB telemetry still works during
    // cable-only testing, even if the Bluetooth link is absent or unhealthy.
    const size_t secondaryWritten = secondaryStream_.write(data, length);
    if (primaryWritten == length || secondaryWritten == length) {
      return length;
    }

    return primaryWritten > secondaryWritten ? primaryWritten : secondaryWritten;
  }

 private:
  IByteStream& primaryStream_;
  IByteStream& secondaryStream_;
};
