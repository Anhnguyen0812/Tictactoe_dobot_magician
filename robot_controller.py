"""Native Dobot Magician controller using lib.DobotDllType.

The controller provides:
- queued MOVL Cartesian movement;
- one continuous circle from chained SetARCCmd arcs;
- alarm reporting and clean recovery;
- no HOME parameter modification.
"""
from __future__ import annotations

import math
import time
from typing import List, Tuple

from lib import DobotDllType as dType

# ============================================================
# MOTION CONFIGURATION
# ============================================================
# PTP is used to move between cells and approach/lift the pen.
PTP_XYZ_VELOCITY = 130.0
PTP_XYZ_ACCELERATION = 120.0
PTP_R_VELOCITY = 70.0
PTP_R_ACCELERATION = 60.0
PTP_VELOCITY_RATIO = 85.0
PTP_ACCELERATION_RATIO = 70.0

# ARC/CIRCLE is used while drawing O.
# Higher ratios and acceleration keep the pen moving across the arc junction.
# Lower them if the pen overshoots or skips on the paper.
ARC_XYZ_VELOCITY = 120.0
ARC_XYZ_ACCELERATION = 120.0
ARC_R_VELOCITY = 65.0
ARC_R_ACCELERATION = 90.0
ARC_VELOCITY_RATIO = 85.0
ARC_ACCELERATION_RATIO = 85.0

# Number of SetARCCmd calls used to draw the O mark.
#   1 -> one 359-degree arc (no junction, leaves a 1-degree gap)
#   2 -> two 180-degree arcs
#   4 -> four 90-degree arcs
CIRCLE_ARC_COUNT = 1

COMMAND_TIMEOUT_SECONDS = 20.0
START_POSITION_TOLERANCE_MM = 2.0
END_POSITION_TOLERANCE_MM = 2.5
RECOVERY_DELAY_MS = 100


class DobotController:
    """Alarm-aware wrapper over DobotDllType for Dobot Magician."""

    def __init__(self, port: str, baudrate: int = 115200):
        self.api = dType.load()
        self.connected = False

        result = dType.ConnectDobot(self.api, port, baudrate)
        state = result[0]

        if state != dType.DobotConnect.DobotConnect_NoError:
            raise RuntimeError(
                f"Dobot connection failed on {port}: state={state}"
            )

        self.connected = True
        print(
            f"Dobot connected: firmware={result[3]} {result[4]}"
        )

        # Start from a clean command queue.
        dType.SetQueuedCmdForceStopExec(self.api)
        dType.SetQueuedCmdClear(self.api)
        dType.ClearAllAlarmsState(self.api)

        # Apply parameters immediately, then start queued execution.
        self.configure_motion(is_queued=0)
        dType.SetQueuedCmdStartExec(self.api)
        dType.dSleep(RECOVERY_DELAY_MS)

        if self.has_alarm():
            codes = self.get_alarm_codes()
            raise RuntimeError(
                f"Dobot alarm after connection: {codes}"
            )

    # ========================================================
    # CONFIGURATION AND STATE
    # ========================================================
    def configure_motion(self, is_queued: int = 0) -> None:
        """Configure PTP and ARC/CIRCLE parameters.

        SetHOMEParams is intentionally not called because it changes the
        robot's stored HOME pose.
        """
        dType.SetPTPCoordinateParams(
            self.api,
            PTP_XYZ_VELOCITY,
            PTP_XYZ_ACCELERATION,
            PTP_R_VELOCITY,
            PTP_R_ACCELERATION,
            isQueued=is_queued,
        )
        dType.SetPTPCommonParams(
            self.api,
            PTP_VELOCITY_RATIO,
            PTP_ACCELERATION_RATIO,
            isQueued=is_queued,
        )
        dType.SetARCParams(
            self.api,
            ARC_XYZ_VELOCITY,
            ARC_R_VELOCITY,
            ARC_XYZ_ACCELERATION,
            ARC_R_ACCELERATION,
            isQueued=is_queued,
        )
        dType.SetARCCommonParams(
            self.api,
            ARC_VELOCITY_RATIO,
            ARC_ACCELERATION_RATIO,
            isQueued=is_queued,
        )

    def pose(self) -> List[float]:
        """Return [x, y, z, rHead, joint1, joint2, joint3, joint4]."""
        return dType.GetPose(self.api)

    def get_alarm_codes(self) -> List[Tuple[int, int]]:
        """Return non-zero alarm bytes as (byte_index, value)."""
        raw, length = dType.GetAlarmsState(self.api)
        return [
            (index, value)
            for index, value in enumerate(raw[:length])
            if value != 0
        ]

    def has_alarm(self) -> bool:
        return bool(self.get_alarm_codes())

    def print_alarms(self) -> List[Tuple[int, int]]:
        codes = self.get_alarm_codes()
        print(f"[ALARM] {codes if codes else 'None'}")
        return codes

    # ========================================================
    # QUEUE AND ALARM MANAGEMENT
    # ========================================================
    def wait_for_index(
        self,
        command_index: int,
        timeout: float = COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        """Wait for one queued command and stop on alarm or timeout."""
        deadline = time.monotonic() + timeout

        while command_index > dType.GetQueuedCmdCurrentIndex(self.api)[0]:
            if self.has_alarm():
                codes = self.print_alarms()
                raise RuntimeError(
                    f"Dobot alarm during motion: {codes}"
                )

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Command {command_index} timed out"
                )

            dType.dSleep(5)

        # An alarm can be raised at the end of the command.
        if self.has_alarm():
            codes = self.print_alarms()
            raise RuntimeError(
                f"Dobot alarm after motion: {codes}"
            )

    def recover(self) -> None:
        """Discard pending commands, clear alarms and restart cleanly."""
        print("[RECOVERY] Stopping and clearing Dobot state...")

        dType.SetQueuedCmdForceStopExec(self.api)
        dType.dSleep(RECOVERY_DELAY_MS)

        dType.SetQueuedCmdClear(self.api)
        dType.ClearAllAlarmsState(self.api)
        dType.dSleep(RECOVERY_DELAY_MS)

        # Queue was cleared, so apply parameters directly.
        self.configure_motion(is_queued=0)
        dType.SetQueuedCmdStartExec(self.api)
        dType.dSleep(RECOVERY_DELAY_MS)

        if self.has_alarm():
            codes = self.print_alarms()
            raise RuntimeError(
                f"Alarm remains after recovery: {codes}"
            )

        print("[RECOVERY] Dobot is ready.")

    def _prepare_new_drawing(self) -> None:
        """Force a clean queue so no previous motion can replay or overlap.

        ForceStop aborts the command that is running right now, then Clear
        drops everything still waiting, including old ARC/CIRCLE commands.
        """
        dType.SetQueuedCmdForceStopExec(self.api)
        dType.dSleep(RECOVERY_DELAY_MS)
        dType.SetQueuedCmdClear(self.api)
        dType.ClearAllAlarmsState(self.api)
        dType.dSleep(RECOVERY_DELAY_MS)

        self.configure_motion(is_queued=0)
        dType.SetQueuedCmdStartExec(self.api)

        # Confirm nothing from the old queue is still moving the arm.
        self._wait_until_idle()

    def _wait_until_idle(
        self,
        timeout: float = COMMAND_TIMEOUT_SECONDS,
        stable_samples: int = 3,
    ) -> None:
        """Block until the tool tip stops moving, proving the queue is empty."""
        deadline = time.monotonic() + timeout
        previous = None
        stable = 0

        while time.monotonic() < deadline:
            if self.has_alarm():
                codes = self.print_alarms()
                raise RuntimeError(f"Dobot alarm while idling: {codes}")

            pose = self.pose()
            sample = (round(pose[0], 2), round(pose[1], 2), round(pose[2], 2))

            if sample == previous:
                stable += 1
                if stable >= stable_samples:
                    return
            else:
                stable = 0

            previous = sample
            dType.dSleep(20)

        raise TimeoutError("Dobot did not become idle")

    # ========================================================
    # MOVEMENT
    # ========================================================
    def move(
        self,
        x: float,
        y: float,
        z: float,
        r: float,
        wait: bool = True,
    ) -> int:
        """Queue a Cartesian MOVL command."""
        if self.has_alarm():
            codes = self.print_alarms()
            raise RuntimeError(
                f"Cannot move while alarm is active: {codes}"
            )

        command_index = dType.SetPTPCmd(
            self.api,
            dType.PTPMode.PTPMOVLXYZMode,
            x,
            y,
            z,
            r,
            isQueued=1,
        )[0]

        if wait:
            self.wait_for_index(command_index)

        return command_index

    def circle_continuous(
        self,
        cx: float,
        cy: float,
        z: float,
        radius: float,
        r_head: float,
    ) -> None:
        """Draw one circle from chained SetARCCmd arcs.

        The robot must already be at the east point:
            start = (cx + radius, cy, z)

        The arc count comes from CIRCLE_ARC_COUNT. Each SetARCCmd runs from
        the current pose through cir_point to to_point, so the arcs chain
        into a closed circle.
        """
        if radius <= 0:
            raise ValueError("Circle radius must be greater than zero")

        # Drop every old command before measuring, so a leftover motion from
        # an earlier run cannot replay while this circle is drawn.
        self._prepare_new_drawing()

        expected_start = [cx + radius, cy, z, r_head]
        current = self.pose()
        start_error = math.sqrt(
            (current[0] - expected_start[0]) ** 2
            + (current[1] - expected_start[1]) ** 2
            + (current[2] - expected_start[2]) ** 2
        )

        if start_error > START_POSITION_TOLERANCE_MM:
            raise RuntimeError(
                f"Robot is {start_error:.2f} mm away from "
                "the circle start point"
            )

        def circle_point(degrees: float) -> List[float]:
            angle = math.radians(degrees)
            return [
                cx + radius * math.cos(angle),
                cy + radius * math.sin(angle),
                z,
                r_head,
            ]

        # Build the arc chain from CIRCLE_ARC_COUNT. A single SetARCCmd can
        # never close the full 360 degrees, so the last arc stops at 359 and
        # leaves a one-degree gap where the pen started.
        count = max(1, int(CIRCLE_ARC_COUNT))
        step = 360.0 / count
        target = 360.0 if count > 1 else 359.0

        arcs = []
        for index in range(count):
            start_degrees = index * step
            if index < count - 1:
                end_degrees = start_degrees + step
            else:
                end_degrees = target
            middle_degrees = (start_degrees + end_degrees) / 2.0
            arcs.append(
                (circle_point(middle_degrees), circle_point(end_degrees))
            )

        print(
            f"[CIRCLE] {len(arcs)} SetARCCmd arc(s): "
            f"center=({cx:.2f}, {cy:.2f}), "
            f"z={z:.2f}, radius={radius:.2f}"
        )

        try:
            command_index = 0
            for middle_point, end_point in arcs:
                command_index = dType.SetARCCmd(
                    self.api,
                    middle_point,
                    end_point,
                    isQueued=1,
                )[0]

            self.wait_for_index(command_index)

            # The queued index can pass before the last arc stops moving, so
            # wait for real stillness before judging the end position.
            self._wait_until_idle()

            actual = self.pose()
            end_error = math.sqrt(
                (actual[0] - expected_start[0]) ** 2
                + (actual[1] - expected_start[1]) ** 2
                + (actual[2] - expected_start[2]) ** 2
            )

            if end_error > END_POSITION_TOLERANCE_MM:
                raise RuntimeError(
                    f"Circle ended {end_error:.2f} mm away "
                    "from its start point"
                )

            print(
                f"[CIRCLE] Completed {len(arcs)} arc(s); "
                f"end error={end_error:.2f} mm"
            )

        except Exception:
            # Prevent a failed circle command from executing on a later turn.
            try:
                dType.SetQueuedCmdForceStopExec(self.api)
                dType.dSleep(50)
                dType.SetQueuedCmdClear(self.api)
            finally:
                if self.has_alarm():
                    self.print_alarms()
                self.recover()
            raise
    
    # Compatibility alias for code that still calls the old method name.
    def circle_three_arcs(
        self,
        cx: float,
        cy: float,
        z: float,
        radius: float,
        r_head: float,
    ) -> None:
        """Compatibility alias. Draws one circle from chained arcs."""
        self.circle_continuous(cx, cy, z, radius, r_head)

    # ========================================================
    # SHUTDOWN
    # ========================================================
    def force_stop(self) -> None:
        """Abort the current motion and drop every pending command."""
        if self.connected:
            dType.SetQueuedCmdForceStopExec(self.api)
            dType.dSleep(50)
            dType.SetQueuedCmdClear(self.api)
            dType.SetQueuedCmdStartExec(self.api)

    def close(self) -> None:
        """Stop safely without adding a new robot movement."""
        if not self.connected:
            return

        try:
            dType.SetQueuedCmdForceStopExec(self.api)
            dType.SetQueuedCmdClear(self.api)
        finally:
            dType.DisconnectDobot(self.api)
            self.connected = False
            print("Dobot disconnected.")
