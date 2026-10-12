#pragma once
// Host test stub for the bits of esphome/core/helpers.h the component uses.
#include <mutex>

namespace esphome {
class Mutex {
 public:
  void lock() { m_.lock(); }
  bool try_lock() { return m_.try_lock(); }
  void unlock() { m_.unlock(); }

 private:
  std::mutex m_;
};
class LockGuard {
 public:
  explicit LockGuard(Mutex &m) : m_(m) { m_.lock(); }
  ~LockGuard() { m_.unlock(); }
  LockGuard(const LockGuard &) = delete;
  LockGuard &operator=(const LockGuard &) = delete;

 private:
  Mutex &m_;
};
}  // namespace esphome
