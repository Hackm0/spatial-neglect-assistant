#pragma once

#include <Arduino.h>

class MillisInterval {
 public:
  explicit MillisInterval(unsigned long intervalMs);

  bool isReady(unsigned long nowMs);
  void reset(unsigned long nowMs);

 private:
  unsigned long intervalMs_;
  unsigned long lastRunMs_;
};
