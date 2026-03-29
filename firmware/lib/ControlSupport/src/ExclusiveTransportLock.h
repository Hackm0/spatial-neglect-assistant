#pragma once

#include <stdint.h>

#include "MillisTimeout.h"

enum class TransportChannel : uint8_t {
  kPrimary = 0U,
  kSecondary = 1U,
};

class ExclusiveTransportLock {
 public:
  explicit ExclusiveTransportLock(unsigned long inactivityTimeoutMs);

  void clear();
  void clearExpired(unsigned long nowMs);
  bool hasActiveTransport() const;
  bool isActiveTransport(TransportChannel channel) const;
  bool tryLockTo(TransportChannel channel, unsigned long nowMs);

 private:
  TransportChannel activeTransport_;
  MillisTimeout inactivityTimeout_;
  bool hasActiveTransport_;
};
