#include "MillisTimeout.h"

MillisTimeout::MillisTimeout(const unsigned long durationMs)
    : durationMs_(durationMs), startedAtMs_(0UL), active_(false) {}

void MillisTimeout::start(const unsigned long nowMs) {
  startedAtMs_ = nowMs;
  active_ = true;
}

void MillisTimeout::stop() {
  active_ = false;
}

bool MillisTimeout::isActive() const {
  return active_;
}

bool MillisTimeout::isExpired(const unsigned long nowMs) const {
  if (!active_) {
    return false;
  }

  return (nowMs - startedAtMs_) >= durationMs_;
}

unsigned long MillisTimeout::durationMs() const {
  return durationMs_;
}
