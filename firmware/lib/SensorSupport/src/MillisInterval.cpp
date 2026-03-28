#include "MillisInterval.h"

MillisInterval::MillisInterval(const unsigned long intervalMs)
    : intervalMs_(intervalMs), lastRunMs_(0UL) {}

bool MillisInterval::isReady(const unsigned long nowMs) {
  if ((nowMs - lastRunMs_) < intervalMs_) {
    return false;
  }

  lastRunMs_ = nowMs;
  return true;
}

void MillisInterval::reset(const unsigned long nowMs) {
  lastRunMs_ = nowMs;
}
