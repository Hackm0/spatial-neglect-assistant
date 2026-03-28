#pragma once

class MillisTimeout {
 public:
  explicit MillisTimeout(unsigned long durationMs);

  void start(unsigned long nowMs);
  void stop();

  bool isActive() const;
  bool isExpired(unsigned long nowMs) const;
  unsigned long durationMs() const;

 private:
  unsigned long durationMs_;
  unsigned long startedAtMs_;
  bool active_;
};
