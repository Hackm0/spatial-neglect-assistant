#include "ExclusiveTransportLock.h"

ExclusiveTransportLock::ExclusiveTransportLock(
    const unsigned long inactivityTimeoutMs)
    : activeTransport_(TransportChannel::kPrimary),
      inactivityTimeout_(inactivityTimeoutMs),
      hasActiveTransport_(false) {}

void ExclusiveTransportLock::clear() {
  inactivityTimeout_.stop();
  hasActiveTransport_ = false;
}

void ExclusiveTransportLock::clearExpired(const unsigned long nowMs) {
  if (hasActiveTransport_ && inactivityTimeout_.isExpired(nowMs)) {
    clear();
  }
}

bool ExclusiveTransportLock::hasActiveTransport() const {
  return hasActiveTransport_;
}

bool ExclusiveTransportLock::isActiveTransport(
    const TransportChannel channel) const {
  return hasActiveTransport_ && activeTransport_ == channel;
}

bool ExclusiveTransportLock::tryLockTo(const TransportChannel channel,
                                       const unsigned long nowMs) {
  if (hasActiveTransport_ && activeTransport_ != channel) {
    return false;
  }

  activeTransport_ = channel;
  hasActiveTransport_ = true;
  inactivityTimeout_.start(nowMs);
  return true;
}
