#include <unity.h>

#include "AutonomousVibrationController.h"

namespace {

SensorSnapshot makeSnapshot(const bool distanceValid,
                            const bool distanceTimedOut,
                            const uint16_t distanceMm,
                            const bool accelValid,
                            const int16_t accelXMilliG,
                            const int16_t accelYMilliG,
                            const int16_t accelZMilliG) {
  SensorSnapshot snapshot;
  snapshot.distanceValid = distanceValid;
  snapshot.distanceTimedOut = distanceTimedOut;
  snapshot.distanceMm = distanceMm;
  snapshot.accelValid = accelValid;
  snapshot.accelXMilliG = accelXMilliG;
  snapshot.accelYMilliG = accelYMilliG;
  snapshot.accelZMilliG = accelZMilliG;
  return snapshot;
}

SensorSnapshot makeNearbyWithMotion() {
  return makeSnapshot(true, false, 450U, true, 0, 0, 1300);
}

void test_no_trigger_for_invalid_timed_out_or_zero_distance() {
  {
    AutonomousVibrationController controller;
    const SensorSnapshot snapshot = makeSnapshot(false, false, 450U, true, 0, 0, 1300);
    TEST_ASSERT_FALSE(controller.update(snapshot, 0UL));
  }

  {
    AutonomousVibrationController controller;
    const SensorSnapshot snapshot = makeSnapshot(true, true, 450U, true, 0, 0, 1300);
    TEST_ASSERT_FALSE(controller.update(snapshot, 0UL));
  }

  {
    AutonomousVibrationController controller;
    const SensorSnapshot snapshot = makeSnapshot(true, false, 0U, true, 0, 0, 1300);
    TEST_ASSERT_FALSE(controller.update(snapshot, 0UL));
  }
}

void test_no_trigger_for_nearby_stationary_readings() {
  AutonomousVibrationController controller;
  const SensorSnapshot nearbyStationary = makeSnapshot(true, false, 450U, true, 0, 0, 1000);

  TEST_ASSERT_FALSE(controller.update(nearbyStationary, 0UL));
  TEST_ASSERT_FALSE(controller.update(nearbyStationary, 100UL));
  TEST_ASSERT_FALSE(controller.update(nearbyStationary, 1000UL));
}

void test_trigger_for_nearby_with_motion_above_threshold() {
  AutonomousVibrationController controller;
  const SensorSnapshot snapshot = makeNearbyWithMotion();

  TEST_ASSERT_FALSE(controller.update(snapshot, 50UL));
  TEST_ASSERT_TRUE(controller.update(snapshot, 70UL));
}

void test_single_motion_spike_does_not_trigger() {
  AutonomousVibrationController controller;
  const SensorSnapshot motionSnapshot = makeNearbyWithMotion();
  const SensorSnapshot stationarySnapshot =
      makeSnapshot(true, false, 450U, true, 0, 0, 1000);

  TEST_ASSERT_FALSE(controller.update(motionSnapshot, 0UL));
  TEST_ASSERT_FALSE(controller.update(stationarySnapshot, 20UL));
  TEST_ASSERT_FALSE(controller.update(stationarySnapshot, 40UL));
}

void test_exact_pulse_schedule_after_trigger() {
  AutonomousVibrationController controller;
  const SensorSnapshot snapshot = makeNearbyWithMotion();

  TEST_ASSERT_FALSE(controller.update(snapshot, 100UL));
  TEST_ASSERT_TRUE(controller.update(snapshot, 120UL));

  TEST_ASSERT_TRUE(controller.update(snapshot, 269UL));
  TEST_ASSERT_FALSE(controller.update(snapshot, 270UL));

  TEST_ASSERT_FALSE(controller.update(snapshot, 1119UL));
  TEST_ASSERT_TRUE(controller.update(snapshot, 1120UL));
  TEST_ASSERT_TRUE(controller.update(snapshot, 1269UL));
  TEST_ASSERT_FALSE(controller.update(snapshot, 1270UL));

  TEST_ASSERT_FALSE(controller.update(snapshot, 2119UL));
  TEST_ASSERT_TRUE(controller.update(snapshot, 2120UL));
  TEST_ASSERT_TRUE(controller.update(snapshot, 2269UL));
  TEST_ASSERT_FALSE(controller.update(snapshot, 2270UL));

  TEST_ASSERT_FALSE(controller.update(snapshot, 3119UL));
  TEST_ASSERT_FALSE(controller.update(snapshot, 3120UL));
}

void test_burst_completes_even_if_motion_or_distance_clears() {
  AutonomousVibrationController controller;
  const SensorSnapshot triggerSnapshot = makeNearbyWithMotion();
  const SensorSnapshot clearedSnapshot = makeSnapshot(false, false, 0U, false, 0, 0, 0);

  TEST_ASSERT_FALSE(controller.update(triggerSnapshot, 0UL));
  TEST_ASSERT_TRUE(controller.update(triggerSnapshot, 20UL));
  TEST_ASSERT_TRUE(controller.update(clearedSnapshot, 120UL));
  TEST_ASSERT_FALSE(controller.update(clearedSnapshot, 500UL));
  TEST_ASSERT_TRUE(controller.update(clearedSnapshot, 1020UL));
  TEST_ASSERT_TRUE(controller.update(clearedSnapshot, 2020UL));
  TEST_ASSERT_FALSE(controller.update(clearedSnapshot, 2999UL));
  TEST_ASSERT_FALSE(controller.update(clearedSnapshot, 3000UL));
}

void test_no_retrigger_while_object_stays_in_zone() {
  AutonomousVibrationController controller;
  const SensorSnapshot snapshot = makeNearbyWithMotion();

  TEST_ASSERT_FALSE(controller.update(snapshot, 0UL));
  TEST_ASSERT_TRUE(controller.update(snapshot, 20UL));
  TEST_ASSERT_FALSE(controller.update(snapshot, 3000UL));
  TEST_ASSERT_FALSE(controller.update(snapshot, 4000UL));
  TEST_ASSERT_FALSE(controller.update(snapshot, 5000UL));
}

void test_rearm_after_object_leaves_zone_and_returns() {
  AutonomousVibrationController controller;
  const SensorSnapshot inZoneMotion = makeNearbyWithMotion();
  const SensorSnapshot outOfZone = makeSnapshot(true, false, 900U, true, 0, 0, 1300);

  TEST_ASSERT_FALSE(controller.update(inZoneMotion, 0UL));
  TEST_ASSERT_TRUE(controller.update(inZoneMotion, 20UL));
  TEST_ASSERT_FALSE(controller.update(inZoneMotion, 3000UL));
  TEST_ASSERT_FALSE(controller.update(inZoneMotion, 3500UL));

  TEST_ASSERT_FALSE(controller.update(outOfZone, 3600UL));
  TEST_ASSERT_FALSE(controller.update(inZoneMotion, 3700UL));
  TEST_ASSERT_TRUE(controller.update(inZoneMotion, 3720UL));
}

}  // namespace

int main(int argc, char** argv) {
  (void)argc;
  (void)argv;

  UNITY_BEGIN();
  RUN_TEST(test_no_trigger_for_invalid_timed_out_or_zero_distance);
  RUN_TEST(test_no_trigger_for_nearby_stationary_readings);
  RUN_TEST(test_trigger_for_nearby_with_motion_above_threshold);
  RUN_TEST(test_single_motion_spike_does_not_trigger);
  RUN_TEST(test_exact_pulse_schedule_after_trigger);
  RUN_TEST(test_burst_completes_even_if_motion_or_distance_clears);
  RUN_TEST(test_no_retrigger_while_object_stays_in_zone);
  RUN_TEST(test_rearm_after_object_leaves_zone_and_returns);
  return UNITY_END();
}
